"""Runtime settings, read from Function configuration (env vars) or a local .env file."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    otm_base_url: str = field(default_factory=lambda: _env("OTM_BASE_URL").rstrip("/"))
    otm_username: str = field(default_factory=lambda: _env("OTM_USERNAME"))
    otm_password: str = field(default_factory=lambda: os.getenv("OTM_PASSWORD", ""))

    doc_def: str = field(default_factory=lambda: _env("DOC_DEF", "SAMPLE.POD"))
    owner_types: tuple = field(
        default_factory=lambda: tuple(t.strip() for t in _env("OWNER_TYPES", "SHIPMENT,ORDER RELEASE").split(",") if t.strip())
    )
    # Where the customer comes from on the shipment / order release. A plain name is a field
    # (e.g. attribute2); "refnum:<QUALIFIER>" reads that reference number (e.g. refnum:SAMPLE.CUS_NAME).
    customer_id_field: str = field(default_factory=lambda: _env("CUSTOMER_ID_FIELD", "attribute2"))
    customer_name_field: str = field(default_factory=lambda: _env("CUSTOMER_NAME_FIELD", "attribute18"))
    # Used when CUSTOMER_ID_FIELD is empty on the record: this involved party's contact becomes the customer.
    customer_fallback_party: str = field(default_factory=lambda: _env("CUSTOMER_FALLBACK_PARTY", "BILL-TO"))
    local_tz: str = field(default_factory=lambda: _env("LOCAL_TZ", "UTC"))
    # ISO timestamp for the very first run; empty = start of today in LOCAL_TZ.
    start_from: str = field(default_factory=lambda: _env("START_FROM"))

    os_namespace: str = field(default_factory=lambda: _env("OS_NAMESPACE"))
    os_bucket: str = field(default_factory=lambda: _env("OS_BUCKET", "sample-pod-bucket"))
    os_prefix: str = field(default_factory=lambda: _env("OS_PREFIX", "POD_Doc_Merge").strip("/"))
    oci_profile: str = field(default_factory=lambda: _env("OCI_PROFILE", "DEFAULT"))  # local runs only

    # Sync invokes are killed at the function timeout (300s); detached ones at the detached timeout (1800s).
    time_budget_s: int = field(default_factory=lambda: int(_env("TIME_BUDGET_SECONDS", "230")))
    detached_time_budget_s: int = field(default_factory=lambda: int(_env("DETACHED_TIME_BUDGET_SECONDS", "1500")))
    max_docs_per_run: int = field(default_factory=lambda: int(_env("MAX_DOCS_PER_RUN", "150")))
    detached_max_docs: int = field(default_factory=lambda: int(_env("DETACHED_MAX_DOCS", "500")))
    # A run that stops at its cap starts the next batch itself, up to this many batches per chain.
    max_chain_batches: int = field(default_factory=lambda: int(_env("MAX_CHAIN_BATCHES", "20")))
    max_doc_failures: int = field(default_factory=lambda: int(_env("MAX_DOC_FAILURES", "3")))

    # Google Drive: disabled until the credentials object exists in Object Storage.
    gdrive_folder_id: str = field(default_factory=lambda: _env("GDRIVE_FOLDER_ID"))
    gdrive_credentials_object: str = field(default_factory=lambda: _env("GDRIVE_CREDENTIALS_OBJECT", "_config/gdrive_credentials.json"))
    gdrive_impersonate: str = field(default_factory=lambda: _env("GDRIVE_IMPERSONATE"))
    gdrive_share_anyone: bool = field(default_factory=lambda: _env("GDRIVE_SHARE_ANYONE", "false").lower() == "true")

    def validate(self) -> None:
        missing = [n for n, v in (("OTM_BASE_URL", self.otm_base_url), ("OTM_USERNAME", self.otm_username),
                                  ("OTM_PASSWORD", self.otm_password)) if not v]
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")
