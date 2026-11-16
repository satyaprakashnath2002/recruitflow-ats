import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_RESUME_BYTES = 10 * 1024 * 1024


class InvalidResume(ValueError):
    pass


class OversizedResume(InvalidResume):
    pass


class PrivateObjectStore(Protocol):
    def put(self, content: bytes) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalPrivateObjectStore:
    """Private filesystem adapter for local development; never mounted as static content."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        parsed = uuid.UUID(key.removesuffix(".pdf"))
        path = (self.root / f"{parsed}.pdf").resolve()
        if path.parent != self.root:
            raise ValueError("Invalid object key")
        return path

    def put(self, content: bytes) -> str:
        key = f"{uuid.uuid4()}.pdf"
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("xb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def validate_pdf(content: bytes) -> str:
    if not content:
        raise InvalidResume("Resume file is empty")
    if len(content) > MAX_RESUME_BYTES:
        raise OversizedResume("Resume exceeds the 10 MB limit")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        # Accessing the trailer forces structural parsing; filename/MIME alone is never trusted.
        if not reader.trailer:
            raise InvalidResume("Resume is not a valid PDF")
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise InvalidResume("Resume is not a valid PDF") from exc
    return hashlib.sha256(content).hexdigest()
