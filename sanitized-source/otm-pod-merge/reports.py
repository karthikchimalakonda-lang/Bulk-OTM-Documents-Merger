"""
Run reports in Object Storage, readable by people (Markdown) and by APEX/scripts (JSON).

  reports/runs/<chain_id>/batch-NN.json     what one invocation did
  reports/runs/<chain_id>/summary.json      totals for the whole chain (all batches)
  reports/runs/<chain_id>/summary.md        the same as tables
  reports/skipped/<YYYY-MM-DD>.json         every skipped/failed document with the reason (for reprocessing)

A "chain" is one scheduled/manual invocation plus the batches it started itself to finish a backlog.
"""
import logging

log = logging.getLogger("otm.reports")

# Plain-language explanations shown next to every skip/failure reason.
REASONS = {
    "out_of_scope": "Owner type is not in OWNER_TYPES, so the POD is not merged by design.",
    "permission_denied": "OTM answered 403 Forbidden: the integration user may not read this record type. "
                         "Grant read access in OTM, then reprocess (and add the owner type to OWNER_TYPES).",
    "not_found": "OTM answered 404 Not Found: the owner record or file no longer exists in OTM.",
    "failed_after_retries": "Temporary error repeated on several runs (OTM/network); the document was skipped "
                            "so the queue keeps moving. It can be reprocessed later.",
    "unsupported_file": "File is not a PDF or image (or is password-protected/corrupt). Stored in source/, "
                        "left out of the merged PDF.",
    "merge_failed": "PDF could not be read while merging. Stored in source/, left out of the merged PDF.",
}


class Reports:
    def __init__(self, store):
        self.store = store

    def record_skipped(self, day: str, items: list[dict]) -> None:
        if not items:
            return
        path = f"reports/skipped/{day}.json"
        current, _ = self.store.get_json(path)
        current = current or {"date": day, "documents": []}
        known = {(d["document"], d["reason"]) for d in current["documents"]}
        current["documents"] += [i for i in items if (i["document"], i["reason"]) not in known]
        self.store.put_json(path, current)

    def write_batch(self, chain_id: str, batch: int, summary: dict) -> None:
        self.store.put_json(f"reports/runs/{chain_id}/batch-{batch:02d}.json", summary)

    def write_chain_summary(self, chain_id: str, remaining: int | None) -> dict:
        batches = []
        for name in sorted(self.store.list(f"reports/runs/{chain_id}/batch-")):
            data, _ = self.store.get_json(name)
            if data:
                batches.append(data)
        totals = _totals(batches)
        summary = {"chain_id": chain_id, "batches": len(batches), "remaining_in_otm": remaining, **totals,
                   "batch_details": [_batch_row(b) for b in batches]}
        self.store.put_json(f"reports/runs/{chain_id}/summary.json", summary)
        md = to_markdown(summary)
        self.store.put(f"reports/runs/{chain_id}/summary.md", md.encode(), "text/markdown; charset=utf-8")
        log.info("Chain summary\n%s", md)
        return summary


def _batch_row(b: dict) -> dict:
    return {
        "batch": b.get("batch"), "started": b.get("started_at"), "seconds": b.get("duration_s"),
        "mode": b.get("invoke_type"), "from": b.get("watermark_before"), "to": b.get("watermark_after"),
        "processed": b.get("processed", 0), "out_of_scope": sum((b.get("skipped_out_of_scope") or {}).values()),
        "skipped": len(b.get("skipped") or []), "failed_retrying": b.get("retrying") or 0,
        "files_updated": len(b.get("merged_files") or []), "drive_synced": len((b.get("drive") or {}).get("synced") or []),
        "drive_errors": len((b.get("drive") or {}).get("errors") or []), "note": b.get("note", ""),
    }


def _totals(batches: list[dict]) -> dict:
    rows = [_batch_row(b) for b in batches]
    files: dict[tuple, dict] = {}
    skipped, out_of_scope, links = [], {}, {}
    for b in batches:
        for m in b.get("merged_files") or []:
            key = (m["customer"], m["date"])
            f = files.setdefault(key, {"customer": m["customer"], "customer_name": m.get("customer_name", ""),
                                       "date": m["date"], "added": 0, "pages": 0, "excluded": 0})
            f["added"] += m.get("added", 0)
            f["pages"] = m.get("pages", f["pages"])
            f["excluded"] += m.get("excluded", 0)
        for s in (b.get("drive") or {}).get("synced") or []:
            links[(s["customer"], s["date"])] = s.get("link")
        skipped += b.get("skipped") or []
        for k, v in (b.get("skipped_out_of_scope") or {}).items():
            out_of_scope[k] = out_of_scope.get(k, 0) + v
    for key, f in files.items():
        f["drive_link"] = links.get(key)
    return {
        "started": rows[0]["started"] if rows else None,
        "window_from": rows[0]["from"] if rows else None,
        "window_to": rows[-1]["to"] if rows else None,
        "processed": sum(r["processed"] for r in rows),
        "out_of_scope": out_of_scope,
        "skipped_count": len(skipped),
        "drive_synced": sum(r["drive_synced"] for r in rows),
        "drive_errors": sum(r["drive_errors"] for r in rows),
        "files": sorted(files.values(), key=lambda f: (f["date"], f["customer"])),
        "skipped": skipped,
        "final_note": rows[-1]["note"] if rows else "",
    }


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def to_markdown(s: dict) -> str:
    oos = ", ".join(f"{k}: {v}" for k, v in s["out_of_scope"].items()) or "0"
    parts = [
        f"# POD merge run {s['chain_id']}",
        "",
        _table(["Item", "Value"], [
            ["Batches", s["batches"]],
            ["Window (POD update time, UTC)", f"{s['window_from']} → {s['window_to']}"],
            ["Documents processed", s["processed"]],
            ["Out of scope (not merged by design)", oos],
            ["Skipped with a reason", s["skipped_count"]],
            ["Merged files updated", len(s["files"])],
            ["Drive uploads / errors", f"{s['drive_synced']} / {s['drive_errors']}"],
            ["Still waiting in OTM (all owner types)", s["remaining_in_otm"]],
            ["Result", s["final_note"] or "complete"],
        ]),
        "",
        "## Batches",
        _table(["#", "Started (UTC)", "Sec", "Mode", "From", "To", "Processed", "Out of scope", "Skipped",
                "Files", "Drive", "Note"],
               [[r["batch"], r["started"], r["seconds"], r["mode"], r["from"], r["to"], r["processed"],
                 r["out_of_scope"], r["skipped"], r["files_updated"], r["drive_synced"], r["note"]]
                for r in s["batch_details"]]),
        "",
        "## Merged files",
        _table(["Date", "Customer", "Name", "PODs added", "Pages now", "Left out", "Drive"],
               [[f["date"], f["customer"], f["customer_name"], f["added"], f["pages"], f["excluded"],
                 f["drive_link"] or "pending"] for f in s["files"]]) if s["files"] else "_No files changed._",
    ]
    if s["skipped"]:
        parts += ["", "## Skipped / failed documents",
                  _table(["Document", "Owner", "Reason", "Explanation", "Detail"],
                         [[d["document"], f"{d.get('owner_type', '')} {d.get('owner_gid', '')}".strip(), d["reason"],
                           REASONS.get(d["reason"], ""), d.get("detail", "")[:200]] for d in s["skipped"]])]
    return "\n".join(parts) + "\n"
