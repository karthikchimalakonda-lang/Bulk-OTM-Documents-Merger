"""
In-memory stand-ins for OTM, Object Storage and Google Drive, so the real pipeline code can run
end to end on synthetic POD files without any credentials or network access.
"""
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

SOURCE = Path(__file__).resolve().parent.parent / "sanitized-source" / "otm-pod-merge"
sys.path.insert(0, str(SOURCE))

from config import Settings  # noqa: E402
from otm_client import Content, OtmPermissionError  # noqa: E402
from pipeline import Pipeline  # noqa: E402
from reports import Reports  # noqa: E402
from store import PreconditionFailed  # noqa: E402


# --------------------------------------------------------------------------- synthetic files
def pod_image(label: str, fmt: str = "JPEG", pages: int = 1) -> bytes:
    """A scanned-looking POD page (or multi-page TIFF) with a visible label."""
    frames = []
    for n in range(1, pages + 1):
        img = Image.new("RGB", (850, 1100), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([40, 40, 810, 1060], outline="black", width=3)
        d.text((70, 70), "PROOF OF DELIVERY (synthetic)", fill="black")
        d.text((70, 110), label, fill="black")
        d.text((70, 150), f"page {n} of {pages}", fill="black")
        d.line([70, 980, 400, 980], fill="black", width=2)
        d.text((70, 990), "receiver signature", fill="black")
        frames.append(img)
    buf = io.BytesIO()
    frames[0].save(buf, format=fmt, save_all=pages > 1, append_images=frames[1:])
    return buf.getvalue()


def pod_pdf(label: str, pages: int = 1) -> bytes:
    frames = [Image.open(io.BytesIO(pod_image(f"{label}", "PNG"))).convert("RGB") for _ in range(pages)]
    buf = io.BytesIO()
    frames[0].save(buf, format="PDF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


# --------------------------------------------------------------------------- fakes
class MemoryStore:
    """Object Storage with etags and conditional writes (same interface as store.Store)."""

    def __init__(self, prefix: str = "POD_Doc_Merge"):
        self.prefix, self.objects, self._v = prefix, {}, 0

    def key(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"

    def get(self, path):
        obj = self.objects.get(self.key(path))
        return (obj["data"], obj["etag"]) if obj else None

    def put(self, path, data, content_type="application/octet-stream", if_match=None, if_none_match=None):
        k = self.key(path)
        if if_none_match == "*" and k in self.objects:
            raise PreconditionFailed(f"{path}: exists")
        if if_match and self.objects.get(k, {}).get("etag") != if_match:
            raise PreconditionFailed(f"{path}: etag mismatch")
        self._v += 1
        self.objects[k] = {"data": data, "etag": f"e{self._v}", "type": content_type}
        return f"e{self._v}"

    def delete(self, path):
        self.objects.pop(self.key(path), None)

    def list(self, path_prefix):
        k = self.key(path_prefix)
        return [n[len(self.prefix) + 1:] for n in sorted(self.objects) if n.startswith(k)]

    def get_json(self, path):
        import json
        got = self.get(path)
        return (json.loads(got[0]), got[1]) if got else (None, None)

    def put_json(self, path, obj, **kw):
        import json
        return self.put(path, json.dumps(obj, indent=1, default=str).encode(), "application/json", **kw)


class FakeOtm:
    """OTM REST client stand-in: documents are dicts shaped like the /documents resource."""

    def __init__(self):
        self.docs, self.files, self.customers, self.forbidden = [], {}, {}, set()

    def add(self, xid, owner_type, owner_gid, updated, data, filename, mime):
        self.docs = [d for d in self.docs if d["documentXid"] != xid]
        self.docs.append({"documentXid": xid, "domainName": "SAMPLE", "ownerDataQueryTypeGid": owner_type,
                          "ownerObjectGid": owner_gid, "documentFilename": filename, "documentMimeType": mime,
                          "updateDate": {"value": updated}})
        self.files[xid] = Content(f"SAMPLE.{xid}_C1", data, mime)

    def iter_documents(self, doc_def, updated_since):
        yield from sorted((d for d in self.docs if d["updateDate"]["value"] >= updated_since),
                          key=lambda d: d["updateDate"]["value"])

    def count_documents(self, doc_def, updated_after):
        return sum(1 for d in self.docs if d["updateDate"]["value"] > updated_after)

    def customer_for(self, owner_type, owner_gid):
        if owner_gid in self.forbidden:
            raise OtmPermissionError(f"OTM returned 403 Forbidden for {owner_gid}", 403, owner_gid)
        return self.customers[owner_gid]

    def download_contents(self, doc):
        return [self.files[doc["documentXid"]]]


class FakeDrive:
    """Google Drive stand-in: one folder per customer, files replaced in place (same id and link)."""

    def __init__(self):
        self.folders, self.files = {}, {}

    def ensure_folder(self, name, parent_id):
        return self.folders.setdefault(name, f"folder-{name}")

    def upload_pdf(self, data, name, folder_id, file_id=None):
        file_id = file_id or f"file{len(self.files) + 1:03d}"
        self.files[file_id] = {"name": name, "folder": folder_id, "size": len(data)}
        return {"id": file_id, "name": name, "webViewLink": f"https://drive.example.invalid/file/d/{file_id}/view"}

    def share_with_anyone(self, file_id):
        self.files[file_id]["shared"] = True


class DemoPipeline(Pipeline):
    """The production Pipeline with its I/O clients swapped for the fakes above."""

    def __init__(self, store, otm, drive, **settings):
        import time
        from zoneinfo import ZoneInfo
        self.cfg = Settings(otm_base_url="https://otm.example.invalid", otm_username="demo", otm_password="demo",
                            gdrive_folder_id="demo-root-folder", local_tz="UTC", **settings)
        self.tz = ZoneInfo(self.cfg.local_tz)
        self.store, self.otm, self.fake_drive = store, otm, drive
        self.reports = Reports(store)
        self.started = time.monotonic()

    def _drive(self):
        return self.fake_drive


def ts(day: int, hour: int, minute: int = 0) -> str:
    return (datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_day_one(otm: FakeOtm) -> None:
    """Synthetic PODs: 3 customers, mixed formats, plus out-of-scope, forbidden and unsupported cases."""
    otm.customers = {
        "SAMPLE.SH-0001": {"id": "C1001", "name": "Customer A"},
        "SAMPLE.SH-0002": {"id": "C1001", "name": "Customer A"},
        "SAMPLE.SH-0003": {"id": "C1002", "name": "Customer B"},
        "SAMPLE.OR-0004": {"id": "C1002", "name": "Customer B"},
        "SAMPLE.SH-0005": {"id": "C1003", "name": "Customer C"},
        "SAMPLE.SH-0006": {"id": "C1003", "name": "Customer C"},
    }
    otm.add("POD-0001", "SHIPMENT", "SAMPLE.SH-0001", ts(5, 7), pod_pdf("SH-0001 / C1001", 2), "pod_sh0001.pdf",
            "application/pdf")
    otm.add("POD-0002", "SHIPMENT", "SAMPLE.SH-0002", ts(5, 8), pod_image("SH-0002 / C1001"), "pod_sh0002.jpg",
            "image/jpeg")
    otm.add("POD-0003", "SHIPMENT", "SAMPLE.SH-0003", ts(5, 9), pod_image("SH-0003 / C1002", "PNG"), "pod_sh0003.png",
            "image/png")
    otm.add("POD-0004", "ORDER RELEASE", "SAMPLE.OR-0004", ts(5, 10), pod_image("OR-0004 / C1002", "TIFF", 3),
            "pod_or0004.tiff", "image/tiff")
    otm.add("POD-0005", "SHIPMENT", "SAMPLE.SH-0005", ts(5, 11), pod_pdf("SH-0005 / C1003"), "pod_sh0005.pdf",
            "application/pdf")
    otm.add("POD-0006", "SHIPMENT", "SAMPLE.SH-0006", ts(5, 12), b"plain text note, not a POD scan", "note.txt",
            "text/plain")
    otm.add("POD-0007", "INVOICE", "SAMPLE.INV-0007", ts(5, 13), pod_pdf("INV-0007"), "inv.pdf", "application/pdf")
    otm.customers["SAMPLE.SH-0008"] = {"id": "C1003", "name": "Customer C"}
    otm.forbidden.add("SAMPLE.SH-0008")
    otm.add("POD-0008", "SHIPMENT", "SAMPLE.SH-0008", ts(5, 14), pod_pdf("SH-0008"), "pod_sh0008.pdf",
            "application/pdf")


def seed_day_one_later(otm: FakeOtm) -> None:
    """Same day, later: one new POD for Customer A (append) and a re-uploaded POD for Customer B (replace)."""
    otm.customers["SAMPLE.SH-0009"] = {"id": "C1001", "name": "Customer A"}
    otm.add("POD-0009", "SHIPMENT", "SAMPLE.SH-0009", ts(5, 16), pod_pdf("SH-0009 / C1001"), "pod_sh0009.pdf",
            "application/pdf")
    otm.add("POD-0003", "SHIPMENT", "SAMPLE.SH-0003", ts(5, 17), pod_pdf("SH-0003 / C1002 (re-scanned)", 2),
            "pod_sh0003_v2.pdf", "application/pdf")
