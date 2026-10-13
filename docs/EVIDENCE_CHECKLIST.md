# Evidence checklist

## Before publishing or submitting

- [x] Client name, OTM instance URL, integration user, domain, and document type replaced with `SAMPLE` placeholders.
- [x] Function, application, and compartment OCIDs, namespace, bucket, region, invoke endpoint, Drive folder ID, and service account removed.
- [x] Real customer codes replaced with synthetic `C1001`–`C1003`; client time zone replaced with `UTC`.
- [x] `.env`, OCI config, private keys, Google service-account keys, OAuth tokens, and real POD PDFs excluded (`.gitignore` enforces this).
- [x] Tests pass and the evidence was regenerated from the current code with synthetic documents.
- [ ] Repository is public or accessible to ACE reviewers.
- [ ] README and Mermaid diagram render correctly on GitHub.

## Optional additional evidence from the live deployment

Blur OCIDs, tenancy, namespace, bucket, OTM host, customer names and codes, and Drive folder names in every screenshot.

1. OCI Functions application and function list.
2. Function configuration page with keys visible and values masked.
3. Object Storage `merged/` and `links/` folders.
4. Google Drive Shared Drive with the per-customer folders.
5. A run report (`summary.md`) with customer columns blurred.

## Suggested demo flow (under 3 minutes)

1. State the problem: manual download, check, merge, and email of PODs for every shipment.
2. Show the architecture and the scheduled, self-continuing function.
3. Show a run report: documents processed, merged files, Drive links, skipped with reasons.
4. Open a customer's Drive folder and the merged PDF.
5. Re-upload a POD in OTM and show it replaced in the same file, with the same link, on the next run.
