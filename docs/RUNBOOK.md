# OTM POD Merge – Runbook

An OCI Function that picks up new **POD documents** from OTM, merges them into **one PDF per customer per day**,
stores everything in **OCI Object Storage** and uploads the merged PDFs to a **Google Drive** Shared Drive.
Each run continues from where the previous run stopped, and appends to the day's files.

All commands are for **Windows PowerShell**, run from the project folder `<project folder>`.

---

## 1. Key resources

| What | Value |
|---|---|
| Function | `otm-pod-merge` – `<function-ocid>` |
| Application | `OTM POD Doc Merge` (your compartment and region) |
| Container image repo | `<region-key>.ocir.io/<namespace>/otm-pod-merge/otm-pod-merge:<build-time>` |
| Bucket / folder | `sample-pod-bucket` / `POD_Doc_Merge/` (namespace `<namespace>`) |
| Logs | Console → **Observability & Management → Logging → Log groups → `POD_Doc_Merge` → `OTMPODDocMerge_invoke`** |
| Google Drive | Shared Drive **POD Merge** → folder **POD Merge**, service account `<service-account>@<gcp-project>.iam.gserviceaccount.com` |
| OCI CLI profile | `DEFAULT` |
| Code | `sanitized-source\otm-pod-merge\` · deploy script `deploy\deploy.ps1` |

To avoid repeating long IDs, paste this once per PowerShell window:

```powershell
$FN  = "<function-ocid>"
$BKT = "sample-pod-bucket"
$P   = "POD_Doc_Merge"
$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = "True"; $env:SUPPRESS_LABEL_WARNING = "True"
```

---

## 2. One-time prerequisites (per machine)

1. OCI CLI installed and profile `DEFAULT` in `~\.oci\config`.
2. **Docker Desktop running** (needed only for deploys).
3. Logged in to the container registry (once; uses an OCI **auth token**, not your password):
   ```powershell
   docker login <region-key>.ocir.io -u "<namespace>/<your OCI username>"
   ```

---

## 3. Redeploy (the commands you need most)

| When | Command |
|---|---|
| **Code changed** (normal case) | `powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -AppId <application-ocid> -CompartmentId <compartment-ocid> -Region <region-key>` |
| Code changed **and** you want `.env` values pushed into the function configuration | same command plus `-SyncConfig` |

What the script does: builds the image → pushes it with a new time-stamp tag → updates the function
(memory 2 GB, timeout 300 s, detached timeout 1800 s).

* A normal deploy **does not touch the function configuration** – values edited in the console are kept.
* `-SyncConfig` **merges** `.env` into the configuration: keys in `.env` are added/updated, keys that only exist
  in the console are kept. Empty values in `.env` are ignored.
* **Only a setting changed?** No redeploy needed – edit it in the console (section 8). The next run uses it.
* **Roll back** to a previous build:
  ```powershell
  oci fn function update --function-id $FN --image "<region-key>.ocir.io/<namespace>/otm-pod-merge/otm-pod-merge:<old-tag>" --force --profile DEFAULT
  ```

---

## 4. Invoke the function

Always use **detached** – the command returns immediately and the function runs in the background
(up to 25 minutes, continuing in new batches by itself if there is a backlog).

```powershell
# Normal run: everything new since the last run
oci fn function invoke --function-id $FN --fn-invoke-type detached --file out.json --body '{}' --profile DEFAULT
```

Options (in Windows PowerShell write every `"` inside the JSON as `\"`):

| Purpose | `--body` |
|---|---|
| Normal run | `'{}'` |
| Limit documents per batch | `'{\"max_docs\": 100}'` |
| Only retry pending Google Drive uploads | `'{\"drive_only\": true}'` |
| First run ever only (no state yet): start from a date | `'{\"start_from\": \"2026-10-07T00:00:00Z\"}'` |

> Do **not** use a normal (non-detached) invoke for real runs: the CLI gives up after 60 s and OCI then stops the run.
> A detached call returns no result – check it with the commands in section 5.

**Run on this PC instead of OCI** (same code, same bucket, uses `.env` + profile):
```powershell
cd sanitized-source\otm-pod-merge
$env:OCI_PROFILE = "DEFAULT"; python run_local.py
```

---

## 5. Check what happened

```powershell
# Is a run in progress right now? (lock present = running)
oci os object head -bn $BKT --name "$P/state/lock.json" --profile DEFAULT

# Where did processing stop + summary of the last batch
oci os object get -bn $BKT --name "$P/state/state.json" --file - --profile DEFAULT

# List the run reports (newest last) ...
oci os object list -bn $BKT --prefix "$P/reports/runs/" --all --query "data[].name" --profile DEFAULT
# ... and print one run's summary tables
oci os object get -bn $BKT --name "$P/reports/runs/<chain_id>/summary.md" --file - --profile DEFAULT

# Today's Drive links per customer
oci os object get -bn $BKT --name "$P/links/2026-10-08.json" --file - --profile DEFAULT
```

Logs: console → Logging → `POD_Doc_Merge` → `OTMPODDocMerge_invoke`. Each run writes
`Run <id> batch N started`, `Ingest: processed=… skipped=…`, the result and the summary tables.

---

## 6. Other useful operations

**Run stuck as "busy" after a crash** (lock older than its `expires_at`; normally removed automatically):
```powershell
oci os object get -bn $BKT --name "$P/state/lock.json" --file - --profile DEFAULT      # check expires_at first
oci os object delete -bn $BKT --name "$P/state/lock.json" --force --profile DEFAULT
```

**Reprocess from an earlier time** (e.g. after OTM permissions change). Make sure no run is in progress, then:
```powershell
oci os object get -bn $BKT --name "$P/state/state.json" --file state.json --profile DEFAULT
# edit state.json:  "watermark": "2026-10-07T00:00:00Z",  "seen_at_watermark": []
oci os object put -bn $BKT --name "$P/state/state.json" --file state.json --force --profile DEFAULT
```
Already-merged documents are replaced (not duplicated), so reprocessing is safe.

**Rotate the Google key**: create a new JSON key for the service account in Google Cloud, then
```powershell
oci os object put -bn $BKT --name "$P/_config/gdrive_credentials.json" --file <new-key>.json --content-type application/json --force --profile DEFAULT
```
and delete the old key in Google Cloud. No redeploy needed.

**View the function configuration** (password masked):
```powershell
oci fn function get --function-id $FN --query "data.config" --profile DEFAULT
```
> To change configuration use the **console** (section 8). `oci fn function update --config` replaces the
> *whole* configuration, so only use it with a complete file (or use `deploy.ps1 -SyncConfig`).

---

## 7. Object Storage layout (`sample-pod-bucket/POD_Doc_Merge/`)

| Path | Contains |
|---|---|
| `source/<CUST>/<YYYY-MM-DD>/<documentXid>.<ext>` | Original POD files exactly as downloaded from OTM (PDF/JPG/PNG). Never modified. |
| `merged/<CUST>/<CUST>_POD_<YYYY-MM-DD>.pdf` | The merged PDF for that customer and day. Appended to on every run. |
| `manifests/<CUST>/<CUST>_POD_<YYYY-MM-DD>.json` | What is inside each merged PDF: every document (OTM id, owner shipment/order, file name, update time, pages, status), version, page count and the **Google Drive file id + link**. |
| `links/<YYYY-MM-DD>.json` | One file per day: for each customer – name, document count, pages, merged object path and **Drive link**. Intended for the e-mail/APEX step. |
| `reports/runs/<chain_id>/batch-NN.json` | What one batch did (window, processed, skipped, files, Drive uploads, note). |
| `reports/runs/<chain_id>/summary.md` / `summary.json` | Summary of a whole run (all its batches) as tables: totals, batches, merged files with Drive links, skipped documents with reasons. |
| `reports/skipped/<YYYY-MM-DD>.json` | Every document that could not be merged and **why** (403 no permission, 404 not found, unreadable file, failed after retries). |
| `state/state.json` | **Watermark** – the OTM update time where the next run starts – plus retry counters, the Drive upload queue and a summary of the last batch. |
| `state/lock.json` | Exists only while a run is in progress (prevents two runs at once). |
| `_config/gdrive_credentials.json` | Google service-account key used for Drive uploads. **Secret** – keep the bucket private. |

`<CUST>` = customer id from OTM (e.g. `C1001`). Dates are **local time** (`LOCAL_TZ`) of the document's OTM update time.
Drive file names: `<CUST>_POD_<YYYY-MM-DD>_<HHMMSS>.pdf` in a sub-folder per customer.

---

## 8. Function configuration (console → Functions → `otm-pod-merge` → **Configuration**)

Changes take effect on the **next run** – no redeploy. If a key is missing, the default below is used.

### OTM connection and what to pick up
| Key | Current value | What it does |
|---|---|---|
| `OTM_BASE_URL` | `https://<otm-instance>.otmgtm.<region>.ocs.oraclecloud.com` | OTM instance (REST API base). |
| `OTM_USERNAME` | `SAMPLE.INTEGRATION` | OTM integration user (Basic Auth). |
| `OTM_PASSWORD` | ******** | Its password. **Secret** – planned move to OCI Vault. |
| `DOC_DEF` | `SAMPLE.POD` | Which document type to merge (`documentDefGid`). |
| `OWNER_TYPES` | `SHIPMENT,ORDER RELEASE` | Which OTM owners' PODs are merged. Add `INVOICE` once the user can read invoices. Others are counted as "out of scope". |
| `START_FROM` | *(not set)* | Only for the very first run (no `state.json`): start time, e.g. `2026-10-07T00:00:00Z`. Default: today 00:00 in LOCAL_TZ. |

### Customer detection
| Key | Current value | What it does |
|---|---|---|
| `CUSTOMER_ID_FIELD` | `attribute2` | Field on the shipment/order release holding the customer id (`SAMPLE.C1001` → folder `C1001`). Can also be `refnum:<QUALIFIER>`. |
| `CUSTOMER_NAME_FIELD` | `attribute18` | Field holding the customer name (shown in reports/links). E.g. `refnum:SAMPLE.CUS_NAME` to read that reference number instead. |
| `CUSTOMER_FALLBACK_PARTY` | `BILL-TO` | If the id field is empty, this involved party's contact is used as the customer. |
| `LOCAL_TZ` | `UTC` | Time zone that decides which day's file a POD goes into, and Drive file times. |

### Run size and timing
| Key | Current value | What it does |
|---|---|---|
| `DETACHED_MAX_DOCS` | `500` | Max documents per batch for detached runs (schedule / normal invokes). |
| `MAX_DOCS_PER_RUN` | `150` | Max documents per batch for non-detached runs. |
| `DETACHED_TIME_BUDGET_SECONDS` | `1500` | Time a detached batch may use before it stops and continues in a new batch (OCI limit 1800). |
| `TIME_BUDGET_SECONDS` | `230` | Same for non-detached runs (OCI limit 300). |
| `MAX_CHAIN_BATCHES` | `20` | Max batches one run starts by itself; after that the next scheduled run continues. |
| `MAX_DOC_FAILURES` | `3` | A document failing with a temporary error is retried on this many runs, then skipped with a reason. |
| `FN_INVOKE_ENDPOINT` | `https://<invoke-subdomain>.<region>.functions.oci.oraclecloud.com` | Used by the function to start its own next batch. |

### Storage
| Key | Current value | What it does |
|---|---|---|
| `OS_NAMESPACE` | `<namespace>` | Object Storage namespace (set automatically by `deploy.ps1`). |
| `OS_BUCKET` | `sample-pod-bucket` | Bucket for everything in section 7. |
| `OS_PREFIX` | `POD_Doc_Merge` | Top folder inside the bucket. |

### Google Drive
| Key | Current value | What it does |
|---|---|---|
| `GDRIVE_FOLDER_ID` | `<drive-folder-id>` | Drive folder (**POD Merge** in the Shared Drive) that receives the per-customer folders. Remove it to switch Drive uploads off (files are then queued). |
| `GDRIVE_CREDENTIALS_OBJECT` | `_config/gdrive_credentials.json` | Where the Google key is stored in the bucket (path under `OS_PREFIX`). The key itself is **never** in the configuration. |
| `GDRIVE_SHARE_ANYONE` | `false` | `true` = new Drive files get "anyone with the link can view". Default: access follows the Shared Drive's members. |
| `GDRIVE_IMPERSONATE` | *(not set)* | Only for domain-wide delegation (not used). |

---

## 9. Quick troubleshooting

| Symptom | Where to look / what to do |
|---|---|
| Invoke returns nothing / not sure it ran | Section 5: `state.json` → `last_run.at`, latest `reports/runs/.../summary.md`, logs. |
| Report says `busy` | Another run is in progress. Wait; if it persists past the lock's `expires_at`, delete the lock (section 6). |
| Note "stopped: no progress" | A document hit a temporary OTM error; it is retried on the next run (up to `MAX_DOC_FAILURES`). |
| Documents in "Skipped" with `permission_denied` | OTM returned 403 for that owner type – needs read access in OTM for `SAMPLE.INTEGRATION`. |
| Drive errors in the report | Message includes a hint (key rejected / not a Content manager / folder not found). Files stay queued and upload on the next run; force with `'{\"drive_only\": true}'`. |
| `docker push failed` during deploy | Start Docker Desktop and run `docker login <region-key>.ocir.io` (section 2). |
| Run shows `OTM credentials rejected` | `OTM_PASSWORD` changed/expired – update it in the configuration. Nothing was skipped. |
