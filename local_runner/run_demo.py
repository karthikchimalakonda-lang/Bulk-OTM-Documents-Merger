"""
Local end-to-end demo on synthetic POD files (no OTM, OCI or Google credentials needed).

Runs the production pipeline twice against in-memory OTM / Object Storage / Google Drive fakes:
  run 1 - eight PODs: merged per customer per day, with out-of-scope, 403 and unsupported cases
  run 2 - later the same day: one new POD is appended, one re-uploaded POD replaces its old pages

Writes the merged PDFs, the run summaries and the per-day link index to evidence/demo-output/.

    python local_runner/run_demo.py
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import DemoPipeline, FakeDrive, FakeOtm, MemoryStore, seed_day_one, seed_day_one_later  # noqa: E402
from reports import to_markdown  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "evidence" / "demo-output"
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("otm").setLevel(logging.ERROR)


def run(store, otm, drive, label, chain_id):
    result = DemoPipeline(store, otm, drive, start_from="2026-01-05T00:00:00Z").run({"chain_id": chain_id})
    summary, _ = store.get_json(f"reports/runs/{result['chain_id']}/summary.json")
    md = to_markdown(summary)
    print(f"===== {label}\n{md}")
    return md


def main():
    store, otm, drive = MemoryStore(), FakeOtm(), FakeDrive()
    seed_day_one(otm)
    first = run(store, otm, drive, "Run 1 - morning", "demo-run-1")
    seed_day_one_later(otm)
    second = run(store, otm, drive, "Run 2 - afternoon (append + replace)", "demo-run-2")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "run-1-summary.md").write_text(first, encoding="utf-8")
    (OUT / "run-2-summary.md").write_text(second, encoding="utf-8")
    for name, obj in store.objects.items():
        rel = name.split("/", 1)[1]
        if rel.startswith("merged/"):
            (OUT / Path(rel).name).write_bytes(obj["data"])
    links, _ = store.get_json("links/2026-01-05.json")
    (OUT / "links-2026-01-05.json").write_text(json.dumps(links, indent=2), encoding="utf-8")
    listing = "\n".join(f"{name}  ({len(obj['data']):,} bytes)" for name, obj in sorted(store.objects.items()))
    (OUT / "object-storage-layout.txt").write_text(listing + "\n", encoding="utf-8")
    print("===== Object Storage after run 2\n" + listing)


if __name__ == "__main__":
    main()
