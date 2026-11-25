"""Read input files (.docx, .pdf, .txt, .eml, .msg) into plain text.

Every reader returns a ParsedDoc. If a file cannot be read, the ParsedDoc has an
`error` message instead of raising, so the app never crashes on a bad file.
"""

from __future__ import annotations

import email
import html
import io
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from email import policy

SUPPORTED_TYPES = ["docx", "pdf", "txt", "eml", "msg"]


@dataclass
class ParsedDoc:
    name: str
    kind: str  # "document" or "email"
    text: str = ""
    meta: dict = field(default_factory=dict)  # for emails: from, to, cc, date, subject
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedDoc":
        return cls(**d)


def parse_file(name: str, data: bytes) -> ParsedDoc:
    """Turn the raw bytes of one uploaded file into a ParsedDoc."""
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    readers = {
        "docx": _read_docx,
        "pdf": _read_pdf,
        "txt": _read_txt,
        "eml": _read_eml,
        "msg": _read_msg,
    }
    reader = readers.get(ext)
    if reader is None:
        return ParsedDoc(name, "document", error=f"'{name}' is not a supported file type. "
                         f"Please use one of: {', '.join('.' + t for t in SUPPORTED_TYPES)}.")
    try:
        doc = reader(name, data)
    except Exception as exc:  # any library error becomes a friendly message
        return ParsedDoc(name, "document", error=f"Sorry, '{name}' could not be read "
                         f"({type(exc).__name__}). Is the file damaged or password-protected?")
    doc.text = _tidy(doc.text)
    if not doc.text.strip():
        doc.error = f"'{name}' was opened but no text was found in it."
    return doc


def parse_path(path: str) -> ParsedDoc:
    with open(path, "rb") as fh:
        return parse_file(os.path.basename(path), fh.read())


# ------------------------------------------------------------------ readers

def _read_docx(name: str, data: bytes) -> ParsedDoc:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if cell.text not in cells:  # merged cells repeat their text
                    cells.append(cell.text)
            lines.append(" | ".join(cells))
    return ParsedDoc(name, "document", "\n".join(lines))


def _read_pdf(name: str, data: bytes) -> ParsedDoc:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return ParsedDoc(name, "document", "\n\n".join(pages))


def _read_txt(name: str, data: bytes) -> ParsedDoc:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return ParsedDoc(name, "document", data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ValueError("unknown text encoding")


def _read_eml(name: str, data: bytes) -> ParsedDoc:
    msg = email.message_from_bytes(data, policy=policy.default)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = html_to_text(body)
    meta = {
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "cc": str(msg.get("Cc", "")),
        "date": str(msg.get("Date", "")),
        "subject": str(msg.get("Subject", "")),
    }
    return ParsedDoc(name, "email", body, meta)


def _read_msg(name: str, data: bytes) -> ParsedDoc:
    import extract_msg

    # extract_msg is most reliable with a real file on disk
    fd, tmp = tempfile.mkstemp(suffix=".msg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        msg = extract_msg.openMsg(tmp)
        try:
            body = msg.body or ""
            if not body.strip() and getattr(msg, "htmlBody", None):
                raw = msg.htmlBody
                body = html_to_text(raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw)
            meta = {
                "from": msg.sender or "",
                "to": msg.to or "",
                "cc": msg.cc or "",
                "date": str(msg.date or ""),
                "subject": msg.subject or "",
            }
        finally:
            msg.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return ParsedDoc(name, "email", body, meta)


# ------------------------------------------------------------------ helpers

def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
