import io
import json
import logging
import math
import re
import resource
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_PAGE_TEXT = 100_000
MAX_TOTAL_TEXT = 1_000_000


class PDFError(Exception):
    pass


@dataclass(frozen=True)
class Passage:
    page: int
    ordinal: int
    text: str
    tokens: int


def validate_upload(filename: str, media: str | None, body: bytes) -> str:
    name = re.sub(r"[^\w.() -]", "_", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    if media not in {"application/pdf", "application/octet-stream"} or not name.lower().endswith(
        ".pdf"
    ):
        raise PDFError("INVALID_PDF")
    if not re.match(rb"%PDF-[12]\.\d", body):
        raise PDFError("INVALID_PDF")
    return (name[:-4][:100].strip(" .") or "document") + ".pdf"


def extract_pages(path: Path, page_limit: int) -> tuple[int, list[tuple[int, str]]]:
    try:
        with path.open("rb") as file:
            reader = PdfReader(file, strict=False)
            if reader.is_encrypted:
                raise PDFError("ENCRYPTED_PDF")
            if len(reader.pages) > page_limit:
                raise PDFError("TOO_MANY_PAGES")
            pages = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                # Collapse PDF layout whitespace without losing page boundaries or text order.
                raw = page.extract_text()
                total += len(raw)
                if len(raw) > MAX_PAGE_TEXT or total > MAX_TOTAL_TEXT:
                    raise PDFError("PDF_LIMIT")
                text = " ".join(raw.replace("\0", "").split())
                if text:
                    pages.append((number, text))
            if not pages:
                raise PDFError("NO_TEXT")
            return len(reader.pages), pages
    except (PyPdfError, IndexError, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise PDFError("INVALID_PDF") from exc


def chunk_pages(
    pages: list[tuple[int, str]], target: int = 420, overlap: int = 75
) -> list[Passage]:
    if not 0 <= overlap < target:
        raise ValueError("invalid chunk overlap")
    output: list[Passage] = []
    for page, text in pages:
        # Estimate tokens conservatively from UTF-8 bytes and word boundaries.
        words = text.split()
        weights = [max(1, math.ceil(len(w.encode()) / 3)) + 1 for w in words]
        start = 0
        while start < len(words):
            end, budget = start, 0
            while end < len(words) and (end == start or budget + weights[end] <= target):
                budget += weights[end]
                end += 1
            # Bound pathological unbroken strings before passing them to the provider.
            excerpt = " ".join(words[start:end])
            if len(excerpt.encode()) > target * 4:
                raise PDFError("PDF_LIMIT")
            ordinal = output[-1].ordinal + 1 if output and output[-1].page == page else 0
            output.append(Passage(page, ordinal, excerpt, budget))
            if end == len(words):
                break
            retained = 0
            next_start = end
            while next_start > start + 1 and retained + weights[next_start - 1] <= overlap:
                next_start -= 1
                retained += weights[next_start]
            start = next_start
    return output


def parse_pdf(operation: str, path: Path, number: int, timeout: int = 18) -> bytes:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.pdf", operation, str(path), str(number)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PDFError("PDF_LIMIT") from exc
    if result.returncode:
        reason = result.stderr.decode(errors="replace").strip()
        if reason in {"ENCRYPTED_PDF", "TOO_MANY_PAGES", "NO_TEXT", "PDF_LIMIT", "INVALID_PDF"}:
            raise PDFError(reason)
        raise PDFError("PDF_LIMIT" if result.returncode < 0 else "INVALID_PDF")
    return result.stdout


def render(path: Path, page: int) -> bytes:
    with pdfium.PdfDocument(path) as doc:
        view = doc[page - 1]
        try:
            width, height = view.get_size()
            if not (math.isfinite(width) and math.isfinite(height) and min(width, height) > 0):
                raise PDFError("INVALID_PDF")
            # Serves a passive PNG; embedded PDF actions cannot run in the workspace.
            bitmap = view.render(scale=min(2, 1100 / max(width, height)))
            try:
                result = io.BytesIO()
                with bitmap.to_pil() as image:
                    image.save(result, format="PNG")
                return result.getvalue()
            finally:
                bitmap.close()
        finally:
            view.close()


def main() -> None:
    logging.disable(logging.CRITICAL)
    resource.setrlimit(resource.RLIMIT_CPU, (12, 15))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    operation, filename, number = sys.argv[1:]
    try:
        if operation == "extract":
            sys.stdout.write(json.dumps(extract_pages(Path(filename), int(number))))
        else:
            sys.stdout.buffer.write(render(Path(filename), int(number)))
    except PDFError as exc:
        sys.exit(str(exc))
    except MemoryError:
        sys.exit("PDF_LIMIT")
    except (pdfium.PdfiumError, FileNotFoundError, IndexError, ValueError):
        sys.exit("INVALID_PDF")


if __name__ == "__main__":
    main()
