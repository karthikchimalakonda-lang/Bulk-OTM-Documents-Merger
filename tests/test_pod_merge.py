"""Offline tests for the POD validator and merger (synthetic files, in-memory fakes).

    python -m unittest discover -s tests -v
"""
import io
import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "local_runner"))
from fakes import (DemoPipeline, FakeDrive, FakeOtm, MemoryStore, pod_image, pod_pdf,  # noqa: E402
                   seed_day_one, seed_day_one_later, ts)
import pdfs  # noqa: E402
from pypdf import PdfReader  # noqa: E402

logging.disable(logging.CRITICAL)
DAY = "2026-01-05"


def pages(store, cust):
    return len(PdfReader(io.BytesIO(store.get(f"merged/{cust}/{cust}_POD_{DAY}.pdf")[0])).pages)


class PdfTests(unittest.TestCase):
    def test_multipage_tiff_becomes_one_page_per_frame(self):
        self.assertEqual(pdfs.page_count(pdfs.to_pdf_bytes(pod_image("x", "TIFF", 3), "image/tiff")), 3)

    def test_pdf_passthrough(self):
        data = pod_pdf("x", 2)
        self.assertEqual(pdfs.to_pdf_bytes(data, "application/pdf"), data)

    def test_non_pod_file_rejected(self):
        with self.assertRaises(pdfs.Unsupported):
            pdfs.to_pdf_bytes(b"plain text", "text/plain")

    def test_unreadable_part_left_out_not_fatal(self):
        merged, failed = pdfs.merge([pod_pdf("a"), b"%PDF-broken", pod_pdf("b")], "t")
        self.assertEqual(failed, [1])
        self.assertEqual(pdfs.page_count(merged), 2)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.store, self.otm, self.drive = MemoryStore(), FakeOtm(), FakeDrive()
        seed_day_one(self.otm)

    def run_once(self, **options):
        return DemoPipeline(self.store, self.otm, self.drive, start_from=f"{DAY}T00:00:00Z").run(options)

    def test_merges_one_pdf_per_customer_per_day(self):
        result = self.run_once()
        self.assertEqual(result["processed"], 6)
        self.assertEqual({m["customer"] for m in result["merged_files"]}, {"C1001", "C1002", "C1003"})
        self.assertEqual((pages(self.store, "C1001"), pages(self.store, "C1002"), pages(self.store, "C1003")),
                         (3, 4, 1))

    def test_validation_outcomes_are_reported(self):
        result = self.run_once()
        self.assertEqual(result["skipped_out_of_scope"], {"INVOICE": 1})
        self.assertEqual(sorted(s["reason"] for s in result["skipped"]), ["permission_denied", "unsupported_file"])
        self.assertIsNotNone(self.store.get(f"reports/skipped/{DAY}.json"))

    def test_drive_upload_and_link_index(self):
        self.run_once()
        links, _ = self.store.get_json(f"links/{DAY}.json")
        self.assertEqual(len(self.drive.files), 3)
        self.assertTrue(all(c["drive_link"] for c in links["customers"].values()))
        self.assertEqual(set(self.drive.folders), {"C1001", "C1002", "C1003"})

    def test_incremental_append_and_replace(self):
        self.run_once()
        link_before = self.store.get_json(f"links/{DAY}.json")[0]["customers"]["C1002"]["drive_link"]
        seed_day_one_later(self.otm)
        result = self.run_once()
        self.assertEqual(result["processed"], 2)
        modes = {m["customer"]: m["mode"] for m in result["merged_files"]}
        self.assertEqual(modes, {"C1001": "append", "C1002": "rebuild"})
        self.assertEqual(pages(self.store, "C1001"), 4)   # 3 + 1 appended
        self.assertEqual(pages(self.store, "C1002"), 5)   # old 1-page scan replaced by 2-page re-scan
        link_after = self.store.get_json(f"links/{DAY}.json")[0]["customers"]["C1002"]["drive_link"]
        self.assertEqual(link_before, link_after)          # same Drive file updated in place

    def test_nothing_reprocessed_when_no_changes(self):
        self.run_once()
        result = self.run_once()
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["merged_files"], [])

    def test_backlog_continues_in_next_batch(self):
        started = []
        pipeline = DemoPipeline(self.store, self.otm, self.drive, start_from=f"{DAY}T00:00:00Z")
        result = pipeline.run({"max_docs": 2}, continue_fn=started.append)
        self.assertTrue(result["more_pending"])
        self.assertEqual(started, [{"max_docs": 2, "chain_id": result["chain_id"], "batch": 2}])

    def test_overlapping_run_is_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.store.put_json("state/lock.json", {"owner": "other", "expires_at": future})
        self.assertEqual(self.run_once()["status"], "busy")

    def test_stale_lock_from_crashed_run_is_cleared(self):
        self.store.put_json("state/lock.json", {"owner": "crashed", "expires_at": ts(5, 1)})
        self.assertEqual(self.run_once()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
