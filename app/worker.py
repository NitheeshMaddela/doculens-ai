import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import UUID, uuid4, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Chunk, Document, Heartbeat, Job, engine
from app.errors import log_event
from app.pdf import PDFError, chunk_pages, parse_pdf
from app.providers import ProviderError, embed


@dataclass(frozen=True)
class Claim:
    document_id: UUID
    owner_id: UUID
    lease_id: UUID
    attempts: int
    deleting: bool


def claim() -> Claim | None:
    now = datetime.now(UTC)
    with Session(engine()) as db, db.begin():
        heartbeat = insert(Heartbeat).values(id=1, seen_at=now)
        db.execute(heartbeat.on_conflict_do_update(index_elements=["id"], set_={"seen_at": now}))
        # All job mutations lock document then job. Document locking also serializes deletion.
        row = db.execute(
            select(Document, Job)
            .join(Job, Job.document_id == Document.id)
            .where(Job.available_at <= now)
            .order_by(Job.available_at)
            .limit(1)
            .with_for_update(of=Document, skip_locked=True)
        ).first()
        if row is None:
            return None
        doc, job = row
        job.lease_id = uuid4()
        job.available_at = now + timedelta(seconds=settings().lease_seconds)
        job.attempts += 1
        deleting = doc.status == "deleting"
        if not deleting:
            doc.status = "processing"
        return Claim(doc.id, doc.owner_id, job.lease_id, job.attempts, deleting)


def finish(claimed: Claim, rows: list[Chunk], page_count: int, error: str | None) -> None:
    with Session(engine()) as db, db.begin():
        doc = db.get(Document, claimed.document_id, with_for_update=True)
        job = db.get(Job, claimed.document_id)
        if doc is None or job is None or job.lease_id != claimed.lease_id:
            return
        if claimed.deleting:
            if error:
                job.lease_id = None
                job.available_at = datetime.now(UTC) + timedelta(
                    seconds=min(60, 2 ** min(job.attempts, 6))
                )
            else:
                db.delete(doc)
            return
        if error == "TEMPORARY_FAILURE" and claimed.attempts < 3:
            doc.status = "queued"
            job.lease_id = None
            job.available_at = datetime.now(UTC) + timedelta(seconds=2**claimed.attempts)
            return
        db.delete(job)
        if error:
            doc.status, doc.error_code = "failed", error
        else:
            db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            db.add_all(rows)
            doc.status = "indexed"
            doc.page_count = page_count
            doc.indexed_at = datetime.now(UTC)
            doc.profile = settings().profile
            doc.error_code = None


def process_once() -> bool:
    claimed = claim()
    if claimed is None:
        return False
    start = monotonic()
    config = settings()
    path = config.storage_path / f"{claimed.document_id}.pdf"
    rows: list[Chunk] = []
    page_count = 0
    error = None
    try:
        if claimed.deleting:
            path.unlink(missing_ok=True)
        elif claimed.attempts > 3:
            error = "TEMPORARY_FAILURE"
        else:
            page_count, pages = json.loads(parse_pdf("extract", path, config.max_pdf_pages))
            chunks = chunk_pages(pages)
            for index in range(0, len(chunks), 32):
                if monotonic() - start > config.lease_seconds - 35:
                    raise ProviderError
                batch = chunks[index : index + 32]
                for chunk, vector in zip(
                    batch, embed([chunk.text for chunk in batch]), strict=True
                ):
                    rows.append(
                        Chunk(
                            id=uuid5(claimed.document_id, f"{chunk.page}:{chunk.ordinal}"),
                            document_id=claimed.document_id,
                            owner_id=claimed.owner_id,
                            page=chunk.page,
                            ordinal=chunk.ordinal,
                            text=chunk.text,
                            token_count=chunk.tokens,
                            embedding=vector,
                        )
                    )
    except PDFError as exc:
        error = str(exc)
    except (ProviderError, OSError):
        error = "TEMPORARY_FAILURE"
    finish(claimed, rows, page_count, error)
    log_event(
        "job_finished",
        document_id=str(claimed.document_id),
        outcome=error or ("deleted" if claimed.deleting else "indexed"),
        chunks=len(rows),
        latency_ms=round((monotonic() - start) * 1000, 2),
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    while True:
        try:
            if not process_once():
                sleep(1)
        except SQLAlchemyError as exc:
            log_event("worker_database_error", kind=type(exc).__name__)
            sleep(3)
