import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Chunk, Document, Job, Owner
from app.errors import DOCUMENT_NOT_FOUND, PDF_ERRORS, AppError
from app.pdf import PDFError, validate_upload
from app.schemas import DocumentView


def owned(db: Session, owner: UUID, document_id: UUID, *, lock: bool = False) -> Document:
    stmt = select(Document).where(
        Document.id == document_id, Document.owner_id == owner, Document.status != "deleting"
    )
    result = db.scalar(stmt.with_for_update() if lock else stmt)
    if result is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", DOCUMENT_NOT_FOUND)
    return result


def view(doc: Document) -> DocumentView:
    result = DocumentView.model_validate(doc)
    result.error_message = PDF_ERRORS.get(doc.error_code or "")
    result.retryable = doc.error_code == "TEMPORARY_FAILURE"
    result.searchable = doc.status == "indexed" and doc.profile == settings().profile
    return result


def enqueue(
    db: Session, owner: UUID, filename: str, media: str | None, body: bytes
) -> DocumentView:
    config = settings()
    if len(body) > config.max_upload_bytes:
        raise AppError(413, "UPLOAD_TOO_LARGE", "This PDF exceeds the upload size limit.")
    try:
        name = validate_upload(filename, media, body)
    except PDFError as exc:
        raise AppError(422, "INVALID_PDF", "Upload a valid, text-readable PDF.") from exc
    digest = hashlib.sha256(body).hexdigest()
    # Serialize per-owner quota/duplicate checks and upload creation.
    db.execute(select(Owner.id).where(Owner.id == owner).with_for_update())
    existing = db.scalar(
        select(Document).where(Document.owner_id == owner, Document.sha256 == digest)
    )
    if existing is not None:
        if existing.status == "deleting":
            raise AppError(
                409, "DELETION_PENDING", "Wait for deletion to finish before re-uploading."
            )
        return view(existing)
    count = db.scalar(select(func.count()).select_from(Document).where(Document.owner_id == owner))
    if count is not None and count >= config.document_limit:
        raise AppError(
            409, "DOCUMENT_LIMIT", "Delete an existing document before uploading another."
        )
    doc = Document(
        id=uuid4(),
        owner_id=owner,
        filename=name,
        byte_size=len(body),
        sha256=digest,
        status="queued",
    )
    config.storage_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = config.storage_path / f"{doc.id}.pdf"
    try:
        with path.open("xb") as destination:
            path.chmod(0o600)
            destination.write(body)
        db.add(doc)
        db.flush()
        db.add(Job(document_id=doc.id))
        db.commit()
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise AppError(
            503, "STORAGE_UNAVAILABLE", "PDF storage is temporarily unavailable."
        ) from exc
    # A lost DB connection during COMMIT is ambiguous: preserve the file so a committed job can recover.
    return view(doc)


def request_deletion(db: Session, owner: UUID, document_id: UUID) -> None:
    doc = db.scalar(
        select(Document)
        .where(Document.id == document_id, Document.owner_id == owner)
        .with_for_update()
    )
    # No difference in response between missing and inaccessible ids.
    if doc is None or doc.status == "deleting":
        return
    doc.status = "deleting"
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id, Chunk.owner_id == owner))
    job = db.get(Job, doc.id)
    if job is None:
        db.add(Job(document_id=doc.id))
    else:
        job.attempts = 0
        job.lease_id = None
        job.available_at = datetime.now(UTC)
    db.commit()
