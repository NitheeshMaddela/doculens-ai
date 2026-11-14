from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Status = Literal["queued", "processing", "indexed", "failed"]


class Health(BaseModel):
    status: Literal["ok", "ready"]


class Login(BaseModel):
    access_key: str = Field(min_length=1, max_length=256)


class Workspace(BaseModel):
    token: str


class WorkspaceConfig(BaseModel):
    provider: Literal["local", "openai"]
    max_upload_bytes: int
    max_pdf_pages: int


class DocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    byte_size: int
    page_count: int | None
    status: Status
    error_code: str | None
    error_message: str | None = None
    retryable: bool = False
    searchable: bool = False
    created_at: datetime
    indexed_at: datetime | None


class DocumentList(BaseModel):
    items: list[DocumentView]
    next_cursor: UUID | None


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    document_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=300)
    include_evidence: bool = False
    mode: Literal["answer", "search"] = "answer"

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        if not (value := value.strip()):
            raise ValueError("Enter a question.")
        return value


class Citation(BaseModel):
    document_id: UUID
    filename: str
    page: int
    chunk_id: UUID
    excerpt: str


class Answer(BaseModel):
    answer: str | None
    abstained: bool
    citations: list[Citation]
    evidence: list[Citation]
    retrieved_count: int
    request_id: str
    latency_ms: float


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
