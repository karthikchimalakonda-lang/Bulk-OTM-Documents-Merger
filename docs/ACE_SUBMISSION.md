# ACE Product Usage submission draft

## Suggested title

Serverless OTM Proof-of-Delivery Validator, Merger, and Google Drive Publisher on OCI Functions

Short title: **Bulk OTM Documents Merger**

## Contribution description

I built a serverless document pipeline on Oracle Cloud Infrastructure that automates how proof-of-delivery (POD) documents from Oracle Transportation Management reach customers. Previously, operations staff opened each shipment, downloaded every POD, checked it, merged the files, and emailed them. Now a scheduled OCI Function does it end to end.

On each run, the function reads POD documents updated since its last watermark through the OTM REST API. It resolves each document's customer from the shipment or order release, and validates the file. Out-of-scope owner types, permission and not-found errors, and unreadable or password-protected files are each recorded with a plain-language reason. Valid files are converted to PDF, including photos and multi-page TIFF scans, and merged into one PDF per customer per day in OCI Object Storage. Later runs append new PODs. When a POD is re-uploaded in OTM, its old pages are replaced instead of duplicated.

The merged file is published to a Google Drive Shared Drive in a folder per customer. Drive content is updated in place, so the customer's link stays the same all day, and teams share a link instead of downloading and attaching files. The design is built for volume: an Object Storage lock prevents overlapping runs, the watermark uses conditional writes, and detached invokes have time and document budgets. The function invokes itself to clear backlogs in further batches. A retry queue covers failed Drive uploads, and every run writes Markdown and JSON reports for operations.

## Evidence to attach

- Repository link: https://github.com/karthikchimalakonda-lang/Bulk-OTM-Documents-Merger
- `evidence/01-unit-tests-passing.png` - 12 passing offline tests
- `evidence/02-run1-merge-and-validation.png` - per-customer merge with validation outcomes and Drive links
- `evidence/03-run2-append-and-replace.png` - incremental append and in-place replacement with stable links
- `evidence/04-object-storage-layout.png` - Object Storage layout and the daily link index

## Reviewer notes

All evidence uses synthetic POD files and in-memory stand-ins for OTM, Object Storage, and Google Drive, running the unmodified pipeline code. No client names, customer data, OTM endpoints, OCIDs, or credentials are included.
