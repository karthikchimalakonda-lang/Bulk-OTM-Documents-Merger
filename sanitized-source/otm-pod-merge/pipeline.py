"""
Incremental POD merge: OTM -> Object Storage (raw + merged per customer per day) -> Google Drive.

Object Storage layout (under OS_PREFIX, default POD_Doc_Merge/):
  source/<CUST>/<YYYY-MM-DD>/<documentXid>.<ext>      original files exactly as returned by OTM
  merged/<CUST>/<CUST>_POD_<YYYY-MM-DD>.pdf            merged PDF, appended to on every run that day
  manifests/<CUST>/<CUST>_POD_<YYYY-MM-DD>.json        documents in the merged PDF (order, pages) + Drive link
  links/<YYYY-MM-DD>.json                              per-day index: customer -> merged object + Drive link
  state/state.json                                     watermark (last processed updateDate) + Drive retry queue
  state/lock.json                                      run lock so overlapping invocations don't race
  reports/runs/<chain_id>/...                          per-batch reports + chain summary (JSON and Markdown tables)
  reports/skipped/<YYYY-MM-DD>.json                    skipped/failed documents with the reason
  _config/gdrive_credentials.json                      Google credentials (uploaded by hand, never in the image)

Each run picks up POD documents with updateDate >= watermark (oldest first), so a document that is
updated again in OTM is re-fetched and replaces its old pages in that day's merged file.

A run stops at its document cap / time budget; if it made progress and more is waiting, it asks the
caller to start the next batch (continue_fn - the Function invokes itself, detached) until the backlog is
cleared or MAX_CHAIN_BATCHES is reached. If continuing fails, the next scheduled run picks up from the
saved watermark, so nothing is lost either way.
"""
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pdfs
from config import Settings
from gdrive import Drive
from otm_client import OtmAuthError, OtmClient, OtmPermanentError
from reports import Reports
from store import PreconditionFailed, Store

STATE = "state/state.json"
LOCK = "state/lock.json"
LOCK_MARGIN = timedelta(minutes=5)  # lock outlives the run's time budget by this much

log = logging.getLogger("otm.pipeline")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "file"


def _doc_ts(doc: dict) -> str:
    return (doc.get("updateDate") or doc.get("insertDate") or {}).get("value", "")


def _doc_gid(doc: dict) -> str:
    return f"{doc.get('domainName', 'SAMPLE')}.{doc['documentXid']}"


class Pipeline:
    def __init__(self, settings: Settings | None = None):
        self.cfg = settings or Settings()
        self.cfg.validate()
        self.tz = ZoneInfo(self.cfg.local_tz)
        self.store = Store(self.cfg.os_bucket, self.cfg.os_prefix, self.cfg.os_namespace, self.cfg.oci_profile)
        self.otm = OtmClient(self.cfg.otm_base_url, self.cfg.otm_username, self.cfg.otm_password,
                             self.cfg.customer_id_field, self.cfg.customer_name_field, self.cfg.customer_fallback_party)
        self.reports = Reports(self.store)
        self.started = time.monotonic()

    # ------------------------------------------------------------------ entry point
    def run(self, options: dict | None = None, detached: bool = False, continue_fn=None) -> dict:
        """One batch. continue_fn(body) starts the next batch (raises if it cannot)."""
        options = options or {}
        self.options = options
        default_budget = self.cfg.detached_time_budget_s if detached else self.cfg.time_budget_s
        self.budget = int(options.get("time_budget_s") or default_budget)
        max_docs = int(options.get("max_docs") or (self.cfg.detached_max_docs if detached else self.cfg.max_docs_per_run))
        chain_id = options.get("chain_id") or f"{_now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
        batch = int(options.get("batch") or 1)
        started_at = _iso(_now())
        log.info("Run %s batch %d started (%s, budget %ds, max %d docs)", chain_id, batch,
                 "detached" if detached else "sync", self.budget, max_docs)

        lock_etag = self._acquire_lock()
        if not lock_etag:
            return {"status": "busy", "reason": "another run holds the lock - this invocation did nothing",
                    "chain_id": chain_id, "batch": batch}
        try:
            state, state_etag = self.store.get_json(STATE)
            state = state or self._initial_state(options.get("start_from"))
            self.state = state
            summary = {"status": "ok", "chain_id": chain_id, "batch": batch, "started_at": started_at,
                       "invoke_type": "detached" if detached else "sync", "watermark_before": state["watermark"]}
            if not options.get("drive_only"):
                summary.update(self._ingest(state, max_docs))
            summary["drive"] = self._sync_drive(state)
            summary["watermark_after"] = state["watermark"]
            summary["duration_s"] = round(self._elapsed())
            state["last_run"] = {"at": _iso(_now()),
                                 **{k: v for k, v in summary.items() if k not in ("status", "skipped")}}
            self.store.put_json(STATE, state, **({"if_match": state_etag} if state_etag else {"if_none_match": "*"}))
        except OtmAuthError as exc:
            log.error("Stopping: %s", exc)
            summary = {"status": "error", "chain_id": chain_id, "batch": batch, "started_at": started_at,
                       "reason": str(exc), "note": "stopped: OTM credentials rejected - nothing processed or skipped"}
        finally:
            self.store.delete(LOCK)

        self._decide_continuation(summary, continue_fn)
        self._report(summary)
        return summary

    def _decide_continuation(self, summary: dict, continue_fn) -> None:
        if summary.get("status") != "ok" or not summary.get("more_pending"):
            return
        if summary["watermark_after"] == summary["watermark_before"]:
            summary["note"] = ("stopped: no progress (a document is failing temporarily) - "
                               "the next scheduled run retries it")
            return
        if summary["batch"] >= self.cfg.max_chain_batches:
            summary["note"] = (f"stopped: reached {self.cfg.max_chain_batches} batches - "
                               "the next scheduled run continues")
            return
        if not continue_fn:
            summary["note"] = "more pending - the next run continues"
            return
        try:
            carry = {k: v for k, v in self.options.items() if k in ("max_docs", "time_budget_s")}
            continue_fn({**carry, "chain_id": summary["chain_id"], "batch": summary["batch"] + 1})
            summary["continued"] = True
            summary["note"] = f"more pending - batch {summary['batch'] + 1} started"
        except Exception as exc:  # noqa: BLE001 - the schedule is the fallback
            log.error("Could not start the next batch: %s", exc)
            summary["note"] = ("more pending - could not start the next batch "
                               f"({str(exc)[:150]}); the next scheduled run continues")

    def _report(self, summary: dict) -> None:
        try:
            self.reports.write_batch(summary["chain_id"], summary["batch"], summary)
            if not summary.get("continued"):
                remaining = None
                if summary.get("watermark_after"):
                    remaining = self.otm.count_documents(self.cfg.doc_def, summary["watermark_after"])
                full = self.reports.write_chain_summary(summary["chain_id"], remaining)
                summary["chain_summary"] = {k: v for k, v in full.items()
                                            if k not in ("batch_details", "skipped", "files")}
        except Exception:  # noqa: BLE001 - reporting must never fail a run
            log.exception("Writing the run report failed")

    def _initial_state(self, start_from: str | None) -> dict:
        start = start_from or self.cfg.start_from
        if not start:
            today = datetime.now(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
            start = _iso(today)
        log.info("No state yet - starting from %s", start)
        return {"watermark": _iso(_parse(start)), "seen_at_watermark": [], "failures": {}, "drive_pending": []}

    def _acquire_lock(self) -> str | None:
        body = {"owner": "otm-pod-merge", "expires_at": _iso(_now() + timedelta(seconds=self.budget) + LOCK_MARGIN)}
        for _ in range(2):
            try:
                return self.store.put_json(LOCK, body, if_none_match="*")
            except PreconditionFailed:
                current, _ = self.store.get_json(LOCK)
                if current and _parse(current["expires_at"]) > _now():
                    return None
                log.warning("Removing stale lock %s", current)
                self.store.delete(LOCK)
        return None

    def _elapsed(self) -> float:
        return time.monotonic() - self.started

    # ------------------------------------------------------------------ OTM -> Object Storage
    def _ingest(self, state: dict, max_docs: int) -> dict:
        fetch_deadline = self.budget * 0.6  # leave time for merging, uploads and Drive
        wm, seen = state["watermark"], set(state.get("seen_at_watermark", []))
        new_wm, new_seen = wm, set(seen)
        failures: dict = state.setdefault("failures", {})
        groups: dict[tuple, list] = {}
        stats = {"processed": 0, "skipped_out_of_scope": {}, "skipped": [], "retrying": 0, "more_pending": False}

        def advance(ts: str, gid: str):
            nonlocal new_wm, new_seen
            if ts != new_wm:
                new_wm, new_seen = ts, {gid}
            else:
                new_seen.add(gid)

        for doc in self.otm.iter_documents(self.cfg.doc_def, wm):
            ts, gid = _doc_ts(doc), _doc_gid(doc)
            if ts == wm and gid in seen:
                continue
            if stats["processed"] >= max_docs or self._elapsed() > fetch_deadline:
                stats["more_pending"] = True
                break
            owner_type = doc.get("ownerDataQueryTypeGid") or "UNKNOWN"
            if owner_type not in self.cfg.owner_types:
                oos = stats["skipped_out_of_scope"]
                oos[owner_type] = oos.get(owner_type, 0) + 1
                advance(ts, gid)
                continue
            try:
                customer = self.otm.customer_for(owner_type, doc["ownerObjectGid"])
                contents = self.otm.download_contents(doc)
            except OtmAuthError:
                raise  # bad credentials: stop the run, skip nothing
            except OtmPermanentError as exc:  # 403/404 - retrying won't help: record why and move on
                log.warning("%s skipped (%s): %s", gid, exc.reason, exc)
                stats["skipped"].append(self._skip(doc, gid, ts, exc.reason, str(exc)))
                failures.pop(gid, None)
                advance(ts, gid)
                continue
            except Exception as exc:  # noqa: BLE001 - temporary (timeouts, 5xx): retry on later runs
                failures[gid] = failures.get(gid, 0) + 1
                if failures[gid] < self.cfg.max_doc_failures:
                    log.warning("%s failed (attempt %d of %d), will retry next run: %s",
                                gid, failures[gid], self.cfg.max_doc_failures, exc)
                    stats["retrying"] += 1
                    stats["more_pending"] = True
                    break
                log.error("%s failed %d times, skipping: %s", gid, failures[gid], exc)
                stats["skipped"].append(self._skip(doc, gid, ts, "failed_after_retries", str(exc)))
                failures.pop(gid, None)
                advance(ts, gid)
                continue

            day = _parse(ts).astimezone(self.tz).date().isoformat()
            for n, content in enumerate(contents, 1):
                entry = self._store_source(doc, gid, ts, customer, day, content, n, len(contents))
                if entry["status"] == "unsupported":
                    stats["skipped"].append(self._skip(doc, gid, ts, "unsupported_file", entry.get("note", ""),
                                                       source=entry["source_object"]))
                groups.setdefault((customer["id"], day), []).append(entry)
            failures.pop(gid, None)
            advance(ts, gid)
            stats["processed"] += 1

        stats["merged_files"] = []
        for (cust, day), entries in groups.items():
            result = self._write_group(cust, day, entries)
            stats["skipped"] += result.pop("merge_failed", [])
            stats["merged_files"].append(result)
        state["watermark"], state["seen_at_watermark"] = new_wm, sorted(new_seen)
        by_day: dict[str, list] = {}
        for item in stats["skipped"]:
            by_day.setdefault(_parse(item["updated_at"]).astimezone(self.tz).date().isoformat(), []).append(item)
        for day, items in by_day.items():
            self.reports.record_skipped(day, items)
        log.info("Ingest: processed=%d out_of_scope=%s skipped=%d retrying=%d more_pending=%s",
                 stats["processed"], stats["skipped_out_of_scope"], len(stats["skipped"]), stats["retrying"],
                 stats["more_pending"])
        return stats

    @staticmethod
    def _skip(doc: dict, gid: str, ts: str, reason: str, detail: str, **extra) -> dict:
        return {"document": gid, "owner_type": doc.get("ownerDataQueryTypeGid"),
                "owner_gid": doc.get("ownerObjectGid"), "filename": doc.get("documentFilename"),
                "updated_at": ts, "reason": reason, "detail": detail[:300], **extra}

    def _store_source(self, doc, gid, ts, customer, day, content, n, total) -> dict:
        ext = pdfs.extension_for(doc.get("documentFilename", ""), content.mime or doc.get("documentMimeType", ""))
        suffix = f"_{n}" if total > 1 else ""
        src = f"source/{_safe(customer['id'])}/{day}/{_safe(doc['documentXid'])}{suffix}{ext}"
        self.store.put(src, content.data, content.mime or "application/octet-stream")
        entry = {
            "document_gid": gid, "content_gid": content.content_gid, "owner_type": doc.get("ownerDataQueryTypeGid"),
            "owner_gid": doc.get("ownerObjectGid"), "filename": doc.get("documentFilename"),
            "mime": content.mime or doc.get("documentMimeType"), "updated_at": ts, "source_object": src,
            "customer_name": customer.get("name", ""),
        }
        try:
            entry["_pdf"] = pdfs.to_pdf_bytes(content.data, entry["mime"])
            entry["pages"], entry["status"] = pdfs.page_count(entry["_pdf"]), "merged"
        except Exception as exc:  # noqa: BLE001 - kept in source/, left out of the merged PDF
            entry["pages"], entry["status"], entry["note"] = 0, "unsupported", str(exc)[:200]
            log.warning("%s not merged: %s", src, exc)
        return entry

    def _write_group(self, cust: str, day: str, entries: list[dict]) -> dict:
        base = f"{_safe(cust)}_POD_{day}"
        mpath, merged_path = f"manifests/{_safe(cust)}/{base}.json", f"merged/{_safe(cust)}/{base}.pdf"
        manifest, _ = self.store.get_json(mpath)
        manifest = manifest or {"customer_id": cust, "date": day, "merged_object": self.store.key(merged_path),
                                "version": 0, "documents": [], "drive": {}}

        new_gids = {e["document_gid"] for e in entries}
        replaced = any(d["document_gid"] in new_gids for d in manifest["documents"])
        cache = {e["source_object"]: e.pop("_pdf", None) for e in entries}
        manifest["documents"] = [d for d in manifest["documents"] if d["document_gid"] not in new_gids] + entries
        manifest["customer_name"] = next((e["customer_name"] for e in entries if e["customer_name"]),
                                         manifest.get("customer_name", ""))

        title = f"{cust} - POD - {day}"
        existing = None if replaced else self.store.get(merged_path)
        merged, merge_failed, mode = None, [], "append"
        if existing:  # plain append onto the current merged file
            new_docs = [e for e in entries if e["status"] == "merged"]
            merged, failed = pdfs.merge([existing[0]] + [cache[e["source_object"]] for e in new_docs], title)
            if 0 in failed:  # stored merged file is damaged -> rebuild from the source files instead
                log.warning("Merged file %s unreadable - rebuilding from source files", merged_path)
                merged = None
            else:
                merge_failed = [new_docs[i - 1] for i in failed]
        if merged is None:  # first file of the day, a document was updated, or append failed -> rebuild
            mode = "rebuild"
            docs = [d for d in manifest["documents"] if d["status"] == "merged"]
            parts = []
            for d in docs:
                try:
                    parts.append(cache.get(d["source_object"])
                                 or pdfs.to_pdf_bytes(self.store.get(d["source_object"])[0], d["mime"]))
                except Exception as exc:  # noqa: BLE001 - missing/corrupt source: leave it out, keep the rest
                    log.error("Source %s unusable: %s", d["source_object"], exc)
                    parts.append(b"")
            merged, failed = pdfs.merge(parts, title) if parts else (None, [])
            merge_failed = [docs[i] for i in failed]
        for d in merge_failed:
            d["status"], d["note"] = "merge_failed", "PDF could not be read while merging"

        result = {"customer": cust, "customer_name": manifest["customer_name"], "date": day, "mode": mode,
                  "added": len(entries), "excluded": len(merge_failed),
                  "merge_failed": [{"document": d["document_gid"], "owner_type": d.get("owner_type"),
                                    "owner_gid": d.get("owner_gid"), "filename": d.get("filename"),
                                    "updated_at": d["updated_at"], "reason": "merge_failed",
                                    "detail": d["source_object"]} for d in merge_failed]}
        parts = [merged] if merged else []
        if merged:
            self.store.put(merged_path, merged, "application/pdf")
            manifest.update(pages=pdfs.page_count(merged), size_bytes=len(merged))
        manifest["version"] += 1
        manifest["latest_update"] = max(d["updated_at"] for d in manifest["documents"])
        manifest["updated_at"] = _iso(_now())
        self.store.put_json(mpath, manifest)
        self._update_index(manifest)
        if parts:
            pending = self.state.setdefault("drive_pending", [])
            if mpath not in pending:
                pending.append(mpath)
        result["pages"] = manifest.get("pages", 0)
        return result

    def _update_index(self, manifest: dict) -> None:
        path = f"links/{manifest['date']}.json"
        index, etag = self.store.get_json(path)
        index = index or {"date": manifest["date"], "customers": {}}
        index["customers"][manifest["customer_id"]] = {
            "customer_name": manifest.get("customer_name", ""),
            "merged_object": manifest["merged_object"],
            "documents": len(manifest["documents"]),
            "pages": manifest.get("pages", 0),
            "latest_update": manifest["latest_update"],
            "drive_link": manifest["drive"].get("link"),
            "drive_name": manifest["drive"].get("name"),
        }
        self.store.put_json(path, index)

    # ------------------------------------------------------------------ Object Storage -> Google Drive
    def _drive(self) -> Drive | None:
        if not self.cfg.gdrive_folder_id:
            return None
        creds = self.store.get(self.cfg.gdrive_credentials_object)
        if not creds:
            return None
        return Drive(creds[0], self.cfg.gdrive_impersonate)

    def _sync_drive(self, state: dict) -> dict:
        pending = state.setdefault("drive_pending", [])
        if not pending:
            return {"status": "nothing_pending"}
        drive = self._drive()
        if not drive:
            return {"status": "not_configured", "pending": len(pending)}

        folders, synced, errors = {}, [], []
        for mpath in list(pending):
            if self._elapsed() > self.budget:
                break
            try:
                manifest, _ = self.store.get_json(mpath)
                merged_path = manifest["merged_object"][len(self.store.prefix) + 1:]
                data = self.store.get(merged_path)[0]
                cust = manifest["customer_id"]
                if cust not in folders:
                    folders[cust] = drive.ensure_folder(cust, self.cfg.gdrive_folder_id)
                stamp = _parse(manifest["latest_update"]).astimezone(self.tz).strftime("%Y-%m-%d_%H%M%S")
                name = f"{cust}_POD_{stamp}.pdf"
                had_file = bool(manifest["drive"].get("file_id"))
                res = drive.upload_pdf(data, name, folders[cust], manifest["drive"].get("file_id"))
                if self.cfg.gdrive_share_anyone and not had_file:
                    drive.share_with_anyone(res["id"])
                manifest["drive"] = {"file_id": res["id"], "link": res.get("webViewLink"), "name": name,
                                     "synced_version": manifest["version"], "synced_at": _iso(_now())}
                self.store.put_json(mpath, manifest)
                self._update_index(manifest)
                pending.remove(mpath)
                synced.append({"customer": cust, "date": manifest["date"], "link": res.get("webViewLink")})
            except Exception as exc:  # noqa: BLE001 - stays pending, retried next run
                log.exception("Drive sync failed for %s", mpath)
                errors.append({"manifest": mpath, "error": str(exc)[:300]})
        return {"status": "ok" if not errors else "partial", "synced": synced, "errors": errors, "pending": len(pending)}
