"""Convert POD attachments to PDF and merge them (pypdf + Pillow)."""
import io
import logging
import mimetypes
from pathlib import PurePath

from PIL import Image
from pypdf import PdfReader, PdfWriter

log = logging.getLogger("otm.pdfs")
logging.getLogger("pypdf").setLevel(logging.ERROR)


class Unsupported(ValueError):
    pass


def extension_for(filename: str, mime: str) -> str:
    ext = PurePath(filename or "").suffix.lower()
    if ext and len(ext) <= 6:
        return ext
    return mimetypes.guess_extension(mime or "") or ".bin"


def to_pdf_bytes(data: bytes, mime: str = "") -> bytes:
    """PDF passthrough; images (jpg/png/tiff/...) become one PDF page per frame."""
    if data.startswith(b"%PDF") or mime == "application/pdf":
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unsupported("password-protected PDF")
        return data
    try:
        with Image.open(io.BytesIO(data)) as img:
            frames = []
            for i in range(getattr(img, "n_frames", 1)):
                img.seek(i)
                frames.append(img.convert("RGB"))
    except Exception as exc:  # noqa: BLE001
        raise Unsupported(f"not a PDF or image ({mime or 'unknown type'})") from exc
    buf = io.BytesIO()
    frames[0].save(buf, format="PDF", save_all=True, append_images=frames[1:], resolution=150)
    return buf.getvalue()


def page_count(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf), strict=False).pages)


def merge(parts: list[bytes], title: str) -> tuple[bytes | None, list[int]]:
    """Merge parts in order. A part that pypdf cannot read is left out instead of failing the whole file.

    Returns (merged bytes or None if nothing was mergeable, indexes of the parts that were left out).
    """
    writer, failed = PdfWriter(), []
    for i, pdf in enumerate(parts):
        try:
            reader = PdfReader(io.BytesIO(pdf), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            before = len(writer.pages)
            writer.append(reader)
            if len(writer.pages) == before and len(reader.pages):
                raise ValueError("no pages appended")
        except Exception as exc:  # noqa: BLE001
            log.error("Part %d left out of '%s': %s", i, title, exc)
            failed.append(i)
    if not len(writer.pages):
        return None, failed
    writer.add_metadata({"/Title": title, "/Producer": "otm-pod-merge (pypdf)"})
    writer.compress_identical_objects()
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), failed
