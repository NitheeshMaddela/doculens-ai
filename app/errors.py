class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


DOCUMENT_NOT_FOUND = "The document is unavailable in this workspace."
PDF_ERRORS = {
    "INVALID_PDF": "This PDF could not be read. Export it again and re-upload it.",
    "ENCRYPTED_PDF": "Remove password protection before uploading this PDF.",
    "TOO_MANY_PAGES": "This PDF exceeds the workspace page limit. Split it and upload again.",
    "NO_TEXT": "No selectable text was found. Upload a text-readable PDF.",
    "PDF_LIMIT": "This PDF exceeded the parsing budget. Split it and upload again.",
    "TEMPORARY_FAILURE": "Processing failed temporarily. Try indexing this document again.",
}


def log_event(event: str, **fields: str | int | float) -> None:
    import json
    import logging

    logging.getLogger("uvicorn.error").info(json.dumps({"event": event, **fields}))
