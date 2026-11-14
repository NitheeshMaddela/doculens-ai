import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from time import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.db import Document, Heartbeat, Job, Owner, Quota, database
from app.documents import enqueue, owned, request_deletion, view
from app.errors import AppError, log_event
from app.pdf import PDFError, parse_pdf
from app.retrieval import ask
from app.schemas import (
    Answer,
    DocumentList,
    DocumentView,
    ErrorBody,
    ErrorResponse,
    Health,
    Login,
    Question,
    Workspace,
    WorkspaceConfig,
)

ROOT = Path(__file__).resolve().parent.parent
DB = Annotated[Session, Depends(database)]
security = HTTPBearer(auto_error=False)
app = FastAPI(title="DocuLens AI", version="0.1.0")
router = APIRouter(
    prefix="/api/v1",
    responses={status: {"model": ErrorResponse} for status in (401, 404, 413, 422, 429, 503)},
)


def error(request_id: str, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        ErrorResponse(
            error=ErrorBody(code=code, message=message, request_id=request_id)
        ).model_dump(),
        status_code=status,
    )


class RequestLimits:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        limit = (
            settings().max_upload_bytes + 32_768 if scope["path"] == "/api/v1/documents" else 32_768
        )
        bounded_receive: Receive = receive
        if scope["method"] == "POST":
            # Read a capped body before parsing multipart or JSON, including chunked requests.
            parts: list[bytes] = []
            seen = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                part: bytes = message.get("body", b"")
                seen += len(part)
                if seen > limit:
                    response = error(
                        request_id, 413, "UPLOAD_TOO_LARGE", "Request exceeds the size limit."
                    )
                    response.headers["X-Request-ID"] = request_id
                    await response(scope, receive, send)
                    return
                parts.append(part)
                if not message.get("more_body", False):
                    break
            buffer = b"".join(parts)
            delivered = False

            async def replay_body() -> Message:
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": buffer, "more_body": False}

            bounded_receive = replay_body

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
                if not any(key == b"cache-control" for key, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
                log_event(
                    "request",
                    request_id=request_id,
                    method=scope["method"],
                    status=int(message["status"]),
                )
            await send(message)

        await self.app(scope, bounded_receive, with_headers)


app.add_middleware(RequestLimits)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(AppError)
async def application_error(request: Request, exc: AppError) -> JSONResponse:
    return error(request.state.request_id, exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error(
        request.state.request_id, 422, "INVALID_REQUEST", "Check the request fields and try again."
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(
        request.state.request_id,
        exc.status_code,
        "HTTP_ERROR",
        "The request could not be completed.",
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    log_event("database_error", request_id=request.state.request_id, kind=type(exc).__name__)
    return error(
        request.state.request_id,
        503,
        "DATABASE_UNAVAILABLE",
        "The database is temporarily unavailable.",
    )


def identity(
    db: DB, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]
) -> UUID:
    if credentials and 32 <= len(credentials.credentials) <= 128:
        digest = sha256(credentials.credentials.encode()).hexdigest()
        owner = db.scalar(select(Owner.id).where(Owner.token_hash == digest))
        if owner is not None:
            return owner
    raise AppError(401, "UNAUTHENTICATED", "Connect a workspace to continue.")


Identity = Annotated[UUID, Depends(identity)]


def take_quota(
    db: Session, owner: UUID, operation: str, minute_limit: int, daily_limit: int
) -> None:
    now = int(time())
    # Atomic counters across API replicas; both increments roll back when a limit is exceeded.
    for seconds, maximum in ((60, minute_limit), (86400, daily_limit)):
        key = f"{operation}:{seconds}"
        slot = now // seconds
        db.execute(
            delete(Quota).where(
                Quota.owner_id == owner, Quota.operation == key, Quota.bucket < slot
            )
        )
        stmt = insert(Quota).values(owner_id=owner, operation=key, bucket=slot, count=1)
        used = db.scalar(
            stmt.on_conflict_do_update(
                index_elements=["owner_id", "operation", "bucket"],
                set_={"count": Quota.count + 1},
                where=Quota.count < maximum,
            ).returning(Quota.count)
        )
        if used is None:
            raise AppError(
                429, "RATE_LIMIT", f"Workspace {operation} limit reached. Try again later."
            )
    db.commit()


@router.get("/health")
def health() -> Health:
    return Health(status="ok")


@router.get("/ready")
def ready(db: DB) -> Health:
    stamp = db.get(Heartbeat, 1)
    if stamp is None or stamp.seen_at < datetime.now(UTC) - timedelta(
        seconds=settings().lease_seconds + 10
    ):
        raise AppError(503, "WORKER_NOT_READY", "The indexing worker is unavailable.")
    return Health(status="ready")


@router.post("/workspaces", status_code=201)
def login(payload: Login, db: DB) -> Workspace:
    if not hmac.compare_digest(
        payload.access_key.encode(), settings().demo_access_key.get_secret_value().encode()
    ):
        raise AppError(401, "INVALID_KEY", "The demo access key is incorrect.")
    token = token_urlsafe(32)
    db.add(Owner(id=uuid4(), token_hash=sha256(token.encode()).hexdigest()))
    db.commit()
    return Workspace(token=token)


@router.get("/workspace-config")
def config() -> WorkspaceConfig:
    value = settings()
    return WorkspaceConfig(
        provider=value.provider,
        max_upload_bytes=value.max_upload_bytes,
        max_pdf_pages=value.max_pdf_pages,
    )


@router.get("/documents")
def documents(
    db: DB,
    owner: Identity,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DocumentList:
    stmt = select(Document).where(Document.owner_id == owner, Document.status != "deleting")
    if cursor:
        position = owned(db, owner, cursor)
        stmt = stmt.where(
            tuple_(Document.created_at, Document.id) < (position.created_at, position.id)
        )
    rows = list(
        db.scalars(stmt.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1))
    )
    return DocumentList(
        items=[view(item) for item in rows[:limit]],
        next_cursor=rows[limit - 1].id if len(rows) > limit else None,
    )


@router.post("/documents", status_code=202)
def upload(db: DB, owner: Identity, file: Annotated[UploadFile, File()]) -> DocumentView:
    config = settings()
    take_quota(db, owner, "upload", config.upload_per_minute, config.upload_per_day)
    return enqueue(
        db,
        owner,
        file.filename or "",
        file.content_type,
        file.file.read(config.max_upload_bytes + 1),
    )


@router.post("/samples", status_code=202)
def samples(db: DB, owner: Identity) -> list[DocumentView]:
    config = settings()
    take_quota(db, owner, "upload", config.upload_per_minute, config.upload_per_day)
    return [
        enqueue(db, owner, pdf.name, "application/pdf", pdf.read_bytes())
        for pdf in sorted((ROOT / "sample_data").glob("*.pdf"))
    ]


@router.get("/documents/{document_id}")
def document(document_id: UUID, db: DB, owner: Identity) -> DocumentView:
    return view(owned(db, owner, document_id))


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def file(document_id: UUID, db: DB, owner: Identity) -> FileResponse:
    doc = owned(db, owner, document_id)
    path = settings().storage_path / f"{doc.id}.pdf"
    if not path.is_file():
        raise AppError(404, "FILE_NOT_FOUND", "The original PDF is unavailable.")
    return FileResponse(path, filename=doc.filename, media_type="application/pdf")


@router.get(
    "/documents/{document_id}/pages/{page}", responses={200: {"content": {"image/png": {}}}}
)
def preview(document_id: UUID, page: int, db: DB, owner: Identity) -> Response:
    doc = owned(db, owner, document_id)
    if doc.status != "indexed" or not doc.page_count or not 1 <= page <= doc.page_count:
        raise AppError(404, "PAGE_NOT_FOUND", "The source page is unavailable.")
    db.commit()
    try:
        png = parse_pdf("render", settings().storage_path / f"{doc.id}.pdf", page)
    except PDFError as exc:
        raise AppError(422, "PREVIEW_UNAVAILABLE", "This page could not be rendered.") from exc
    owned(db, owner, document_id)
    return Response(png, media_type="image/png")


@router.delete("/documents/{document_id}", status_code=204)
def remove(document_id: UUID, db: DB, owner: Identity) -> Response:
    request_deletion(db, owner, document_id)
    return Response(status_code=204)


@router.post("/documents/{document_id}/retry", status_code=202)
def retry(document_id: UUID, db: DB, owner: Identity) -> DocumentView:
    doc = owned(db, owner, document_id, lock=True)
    if doc.status != "failed" or doc.error_code != "TEMPORARY_FAILURE":
        raise AppError(409, "NOT_RETRYABLE", "Only temporary indexing failures can be retried.")
    doc.status, doc.error_code = "queued", None
    db.add(Job(document_id=doc.id))
    db.commit()
    return view(doc)


@router.post("/questions")
def questions(payload: Question, request: Request, db: DB, owner: Identity) -> Answer:
    config = settings()
    take_quota(db, owner, "query", config.query_per_minute, config.query_per_day)
    return ask(db, owner, payload, request.state.request_id)


@router.get("/evaluation/report")
def evaluation() -> JSONResponse:
    path = ROOT / "reports" / "evaluation.json"
    if not path.is_file():
        raise AppError(404, "REPORT_NOT_RUN", "No evaluation report has been generated yet.")
    # Per-question results are useful for offline reproducibility; publish only aggregate evidence.
    result = json.loads(path.read_text())
    return JSONResponse({key: result[key] for key in ("metadata", "metrics", "counts")})


app.include_router(router)
