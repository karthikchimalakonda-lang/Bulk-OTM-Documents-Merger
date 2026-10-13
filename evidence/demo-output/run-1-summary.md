# POD merge run demo-run-1

| Item | Value |
|---|---|
| Batches | 1 |
| Window (POD update time, UTC) | 2026-01-05T00:00:00Z → 2026-01-05T14:00:00Z |
| Documents processed | 6 |
| Out of scope (not merged by design) | INVOICE: 1 |
| Skipped with a reason | 2 |
| Merged files updated | 3 |
| Drive uploads / errors | 3 / 0 |
| Still waiting in OTM (all owner types) | 0 |
| Result | complete |

## Batches
| # | Started (UTC) | Sec | Mode | From | To | Processed | Out of scope | Skipped | Files | Drive | Note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-13T06:27:02Z | 0 | sync | 2026-01-05T00:00:00Z | 2026-01-05T14:00:00Z | 6 | 1 | 2 | 3 | 3 |  |

## Merged files
| Date | Customer | Name | PODs added | Pages now | Left out | Drive |
|---|---|---|---|---|---|---|
| 2026-01-05 | C1001 | Customer A | 2 | 3 | 0 | https://drive.example.invalid/file/d/file001/view |
| 2026-01-05 | C1002 | Customer B | 2 | 4 | 0 | https://drive.example.invalid/file/d/file002/view |
| 2026-01-05 | C1003 | Customer C | 2 | 1 | 0 | https://drive.example.invalid/file/d/file003/view |

## Skipped / failed documents
| Document | Owner | Reason | Explanation | Detail |
|---|---|---|---|---|
| SAMPLE.POD-0006 | SHIPMENT SAMPLE.SH-0006 | unsupported_file | File is not a PDF or image (or is password-protected/corrupt). Stored in source/, left out of the merged PDF. | not a PDF or image (text/plain) |
| SAMPLE.POD-0008 | SHIPMENT SAMPLE.SH-0008 | permission_denied | OTM answered 403 Forbidden: the integration user may not read this record type. Grant read access in OTM, then reprocess (and add the owner type to OWNER_TYPES). | OTM returned 403 Forbidden for SAMPLE.SH-0008 |
