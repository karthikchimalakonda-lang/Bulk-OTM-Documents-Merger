# Sanitized source

`otm-pod-merge/` is one OCI Function (Python 3.12, custom Dockerfile on the Oracle Functions base images).

| File | Responsibility |
| --- | --- |
| `func.py` | FDK entry point; detects detached invokes; invokes itself to continue a backlog |
| `pipeline.py` | Watermark, validation, per-customer/day grouping, append or rebuild, manifests, link index, Drive sync |
| `otm_client.py` | OTM REST: POD search, customer lookup (field, reference number, or involved party), downloads |
| `pdfs.py` | Image/TIFF → PDF, merge that leaves out unreadable parts instead of failing |
| `gdrive.py` | Google Drive REST: folder per customer, resumable upload, in-place replace, optional link sharing |
| `store.py` | Object Storage wrapper with conditional writes (resource principal in OCI, config file locally) |
| `reports.py` | Batch and run summaries (JSON + Markdown), skipped-document log with explanations |
| `config.py` | All settings from the function configuration / environment |
| `run_local.py` | Run against real services from a workstation using `.env` |

Sanitization applied:
- Function renamed to `otm-pod-merge`.
- Client-specific domain, document type, customer codes, and time zone replaced with `SAMPLE`, `C1001`, and `UTC`.
- All OCIDs, endpoints, namespace, bucket, and Drive identifiers replaced with placeholders.
- The deploy script now takes the application OCID, compartment OCID, and region as required parameters.
