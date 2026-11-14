from time import monotonic
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Chunk, Document
from app.errors import DOCUMENT_NOT_FOUND, AppError, log_event
from app.providers import ModelAnswer, ProviderError, embed, provider, terms
from app.schemas import Answer, Citation, Question

ABSTENTION = "I couldn't find sufficient support in the uploaded documents."


def validated(output: ModelAnswer | None, evidence: list[Citation]) -> tuple[str, list[Citation]]:
    if not output or output.abstained or not output.answer.strip() or len(output.answer) > 5000:
        return ABSTENTION, []
    lookup = {str(item.chunk_id): item for item in evidence}
    ids = list(dict.fromkeys(output.citation_ids))
    if not 1 <= len(ids) <= 5 or any(key not in lookup for key in ids):
        return ABSTENTION, []
    return output.answer.strip(), [lookup[key] for key in ids]


def ask(db: Session, owner: UUID, payload: Question, request_id: str) -> Answer:
    start = monotonic()
    config = settings()
    scope = set(payload.document_ids or [])
    docs = select(Document.id).where(Document.owner_id == owner, Document.status != "deleting")
    if scope and set(db.scalars(docs.where(Document.id.in_(scope)))) != scope:
        raise AppError(404, "DOCUMENT_NOT_FOUND", DOCUMENT_NOT_FOUND)
    db.commit()
    evidence: list[Citation] = []
    embedding_ms = search_ms = generation_ms = 0.0
    answer = None
    citations: list[Citation] = []
    try:
        searchable = docs.where(Document.status == "indexed", Document.profile == config.profile)
        if scope:
            searchable = searchable.where(Document.id.in_(scope))
        if bool(terms(payload.question)) and db.scalar(searchable.limit(1)) is not None:
            db.commit()
            stage = monotonic()
            vector = embed([payload.question])[0]
            embedding_ms = (monotonic() - stage) * 1000
            stage = monotonic()
            if any(vector):
                distance = Chunk.embedding.cosine_distance(vector)
                stmt = (
                    select(
                        Chunk.id,
                        Chunk.document_id,
                        Chunk.page,
                        Chunk.text,
                        Chunk.token_count,
                        Document.filename,
                    )
                    .join(Document, Document.id == Chunk.document_id)
                    .where(
                        Chunk.owner_id == owner,
                        Document.owner_id == owner,
                        Document.status == "indexed",
                        Document.profile == config.profile,
                        distance < 1.0,
                    )
                    .order_by(distance, Chunk.id)
                    .limit(config.retrieval_k)
                )
                if scope:
                    stmt = stmt.where(Document.id.in_(scope))
                remaining = config.max_context_tokens
                for row in db.execute(stmt):
                    cost = row.token_count + 160  # Bound JSON overhead and source metadata.
                    if cost > remaining:
                        break
                    remaining -= cost
                    evidence.append(
                        Citation(
                            document_id=row.document_id,
                            filename=row.filename,
                            page=row.page,
                            chunk_id=row.id,
                            excerpt=row.text,
                        )
                    )
            search_ms = (monotonic() - stage) * 1000
        db.commit()
        if payload.mode == "answer":
            stage = monotonic()
            answer, citations = validated(
                provider().answer(payload.question, evidence) if evidence else None, evidence
            )
            generation_ms = (monotonic() - stage) * 1000
        # Recheck after external inference. Never return an in-flight citation after deletion.
        if evidence:
            live = set(
                db.scalars(
                    docs.where(
                        Document.id.in_({row.document_id for row in evidence}),
                        Document.status == "indexed",
                        Document.profile == config.profile,
                    )
                )
            )
            if any(row.document_id not in live for row in evidence):
                evidence, citations = [], []
                answer = ABSTENTION if payload.mode == "answer" else None
    except ProviderError as exc:
        raise AppError(
            503, "PROVIDER_UNAVAILABLE", "The answer provider is unavailable. Try again."
        ) from exc
    latency = round((monotonic() - start) * 1000, 2)
    log_event(
        "retrieval",
        request_id=request_id,
        embedding_ms=round(embedding_ms, 2),
        search_ms=round(search_ms, 2),
        generation_ms=round(generation_ms, 2),
        retrieved=len(evidence),
        cited=len(citations),
        latency_ms=latency,
    )
    return Answer(
        answer=answer,
        abstained=not citations if payload.mode == "answer" else not evidence,
        citations=citations,
        evidence=evidence if payload.include_evidence or payload.mode == "search" else [],
        retrieved_count=len(evidence),
        request_id=request_id,
        latency_ms=latency,
    )
