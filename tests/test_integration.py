import io
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import worker
from app.config import settings
from app.db import Chunk, Document, Job, engine
from app.main import app
from app.providers import ProviderError, embed, provider

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    target = os.getenv("TEST_DATABASE_URL")
    if not target:
        pytest.fail(
            "Set TEST_DATABASE_URL to a disposable PostgreSQL pgvector database ending in _test"
        )
    if not (make_url(target).database or "").endswith("_test"):
        pytest.fail("Integration tests require a disposable database ending in _test")
    monkeypatch.setenv("DATABASE_URL", target)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("PROVIDER", "local")
    monkeypatch.setenv("DEMO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(32 * 1024))
    monkeypatch.setenv("QUERY_PER_MINUTE", "3")
    monkeypatch.setenv("UPLOAD_PER_MINUTE", "8")
    engine.cache_clear()
    settings.cache_clear()
    provider.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        with TestClient(app) as client:
            yield client
    finally:
        command.downgrade(config, "base")
        engine().dispose()
        engine.cache_clear()
        settings.cache_clear()
        provider.cache_clear()


def auth(api: TestClient) -> dict[str, str]:
    response = api.post("/api/v1/workspaces", json={"access_key": "test-access"})
    assert response.status_code == 201
    return {"Authorization": "Bearer " + response.json()["token"]}


def upload(
    api: TestClient,
    headers: dict[str, str],
    name: str = "cancellation-policy.pdf",
    body: bytes | None = None,
    media: str = "application/pdf",
) -> str:
    data = body if body is not None else (ROOT / "sample_data/cancellation-policy.pdf").read_bytes()
    response = api.post("/api/v1/documents", files={"file": (name, data, media)}, headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return str(response.json()["id"])


def wake(doc_id: str) -> None:
    with Session(engine()) as session, session.begin():
        job = session.get(Job, UUID(doc_id))
        assert job is not None
        job.available_at = datetime.now(UTC) - timedelta(seconds=1)


def test_real_pgvector_workflow_and_isolation(api: TestClient) -> None:
    a, b = auth(api), auth(api)
    assert api.get("/api/v1/ready").status_code == 503
    assert api.get("/api/v1/documents").status_code == 401
    key = upload(api, a, "../cancellation-policy.pdf")
    assert upload(api, a) == key
    url = f"/api/v1/documents/{key}"
    assert worker.process_once()
    assert api.get("/api/v1/ready").status_code == 200
    assert api.get(url, headers=a).json()["filename"] == "cancellation-policy.pdf"
    assert api.get(url, headers=a).json()["page_count"] == 2
    for path in (url, f"{url}/pages/2", f"{url}/file"):
        assert api.get(path, headers=b).status_code == 404
    assert api.post(f"{url}/retry", headers=b).status_code == 404
    assert api.delete(url, headers=b).status_code == 204
    payload = {"question": "What is the cancellation notice period?", "include_evidence": True}
    assert (
        api.post("/api/v1/questions", json=payload | {"document_ids": [key]}, headers=b).status_code
        == 404
    )
    assert api.post("/api/v1/questions", json=payload, headers=b).json()["retrieved_count"] == 0
    response = api.post("/api/v1/questions", json=payload, headers=a)
    data = response.json()
    assert response.headers["X-Request-ID"] == data["request_id"]
    assert "14 days" in data["answer"]
    assert data["citations"][0]["page"] == 2
    assert api.get(f"{url}/pages/2", headers=a).content.startswith(b"\x89PNG")
    assert api.get(f"{url}/pages/3", headers=a).status_code == 404
    assert api.get(f"{url}/file", headers=a).headers["content-disposition"].startswith("attachment")
    assert api.delete(url, headers=a).status_code == 204
    assert api.get(url, headers=a).status_code == 404
    assert api.post("/api/v1/questions", json=payload, headers=a).json()["retrieved_count"] == 0
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0
    assert worker.process_once()
    assert not (settings().storage_path / f"{key}.pdf").exists()
    with Session(engine()) as db:
        assert db.get(Document, UUID(key)) is None


def test_rejects_oversized_mime_and_scanned_pdf(api: TestClient) -> None:
    headers = auth(api)
    response = api.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("large.pdf", b"%PDF-1.4" + b"x" * 70_000, "application/pdf")},
    )
    assert response.status_code == 413
    for data, mime in [(b"not a PDF", "application/pdf"), (b"%PDF-1.4", "text/html")]:
        assert (
            api.post(
                "/api/v1/documents", headers=headers, files={"file": ("a.pdf", data, mime)}
            ).status_code
            == 422
        )
    encrypted = PdfWriter()
    encrypted.add_blank_page(width=300, height=300)
    encrypted.encrypt("test-password")
    locked = io.BytesIO()
    encrypted.write(locked)
    for body, code in [
        (locked.getvalue(), "ENCRYPTED_PDF"),
        (b"%PDF-1.4\nmissing xref", "INVALID_PDF"),
    ]:
        key = upload(api, headers, body=body)
        assert worker.process_once()
        assert api.get(f"/api/v1/documents/{key}", headers=headers).json()["error_code"] == code
    pdf = PdfWriter()
    pdf.add_blank_page(width=300, height=300)
    stream = io.BytesIO()
    pdf.write(stream)
    key = upload(api, headers, body=stream.getvalue())
    assert worker.process_once()
    failed = api.get(f"/api/v1/documents/{key}", headers=headers).json()
    assert failed["error_code"] == "NO_TEXT"
    assert not failed["retryable"]


def test_worker_reclaims_expired_job_and_retries_provider(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = auth(api)
    key = upload(api, headers)
    lost = worker.claim()
    assert lost is not None
    wake(key)

    def unavailable(texts: list[str]) -> list[list[float]]:
        raise ProviderError

    monkeypatch.setattr(worker, "embed", unavailable)
    assert worker.process_once()
    worker.finish(lost, [], 0, "INVALID_PDF")
    assert api.get(f"/api/v1/documents/{key}", headers=headers).json()["status"] == "queued"
    wake(key)
    assert worker.process_once()
    assert api.get(f"/api/v1/documents/{key}", headers=headers).json()["retryable"]
    monkeypatch.setattr(worker, "embed", embed)
    assert api.post(f"/api/v1/documents/{key}/retry", headers=headers).status_code == 202
    assert worker.process_once()
    assert api.get(f"/api/v1/documents/{key}", headers=headers).json()["searchable"]


def test_query_rate_limit(api: TestClient) -> None:
    headers = auth(api)
    for status in (200, 200, 200, 429):
        response = api.post(
            "/api/v1/questions", headers=headers, json={"question": "What is the salary?"}
        )
        assert response.status_code == status
    assert response.json()["error"]["code"] == "RATE_LIMIT"


def test_deletion_retries_storage_failure(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = auth(api)
    key = upload(api, headers)
    assert worker.process_once()
    url = f"/api/v1/documents/{key}"
    assert api.delete(url, headers=headers).status_code == 204
    path = settings().storage_path / f"{key}.pdf"
    original = Path.unlink

    def blocked(candidate: Path, missing_ok: bool = False) -> None:
        if candidate == path:
            raise PermissionError
        original(candidate, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", blocked)
        assert worker.process_once()
    assert path.is_file()
    assert api.get(url, headers=headers).status_code == 404
    wake(key)
    assert worker.process_once()
    assert not path.exists()
