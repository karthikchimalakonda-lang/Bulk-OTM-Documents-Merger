"""OTM REST client: POD document search, customer lookup and binary download."""
import logging
from dataclasses import dataclass
from typing import Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_PATH = "/logisticsRestApi/resources-int/v2"
TIMEOUT = (15, 120)
DOC_FIELDS = ("documentXid,domainName,documentDefGid,documentMimeType,documentFilename,"
              "ownerDataQueryTypeGid,ownerObjectGid,updateDate,insertDate,uploadedAt")
OWNER_RESOURCES = {"SHIPMENT": "shipments", "ORDER RELEASE": "orderReleases", "INVOICE": "invoices"}

log = logging.getLogger("otm.client")


class OtmError(RuntimeError):
    """Transient or unexpected OTM problem - worth retrying on a later run."""


class OtmPermanentError(OtmError):
    """Retrying will not help until something changes in OTM (permissions, deleted record)."""

    reason = "otm_error"

    def __init__(self, message: str, status: int, url: str):
        super().__init__(message)
        self.status, self.url = status, url


class OtmPermissionError(OtmPermanentError):
    reason = "permission_denied"


class OtmNotFoundError(OtmPermanentError):
    reason = "not_found"


@dataclass
class Content:
    content_gid: str
    data: bytes
    mime: str


class OtmAuthError(OtmError):
    """Bad credentials - the whole run stops, nothing is skipped."""


def _resource_of(url: str) -> str:
    path = requests.utils.unquote(url.split(API_PATH, 1)[-1].split("?", 1)[0])
    return path.lstrip("/")[:160]


def _link(obj: dict, rel: str) -> str | None:
    return next((l["href"] for l in obj.get("links", []) if l.get("rel") == rel), None)


class OtmClient:
    def __init__(self, base_url: str, username: str, password: str, customer_id_field: str = "attribute2",
                 customer_name_field: str = "attribute18", customer_fallback_party: str = "BILL-TO"):
        self.base = base_url + API_PATH
        self.id_field, self.name_field = customer_id_field, customer_name_field
        self.fallback_party = customer_fallback_party
        self.s = requests.Session()
        self.s.auth = (username, password)
        self.s.headers["Accept"] = "application/json"
        retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET"}))
        self.s.mount("https://", HTTPAdapter(max_retries=retry))
        self._customer_cache: dict[str, dict] = {}

    def _get(self, url: str, params: dict | None = None, **kw) -> requests.Response:
        resp = self.s.get(url, params=params, timeout=TIMEOUT, **kw)
        if resp.status_code == 401:
            raise OtmAuthError("OTM returned 401 Unauthorized - check OTM_USERNAME / OTM_PASSWORD")
        if resp.status_code == 403:
            raise OtmPermissionError(
                f"OTM returned 403 Forbidden for {_resource_of(url)}: the integration user has no read access "
                f"to this record type, so it cannot be processed until OTM grants that access", 403, url)
        if resp.status_code == 404:
            raise OtmNotFoundError(f"OTM returned 404 Not Found for {_resource_of(url)}: the record no longer "
                                   f"exists or was renamed", 404, url)
        resp.raise_for_status()
        return resp

    def count_documents(self, doc_def: str, updated_after: str) -> int | None:
        """Total POD documents (all owner types) updated after the given time; None if OTM won't say."""
        params = {"q": f'documentDefGid eq "{doc_def}" and updateDate gt "{updated_after}"',
                  "limit": 1, "totalResults": "true", "fields": "documentXid"}
        try:
            return self._get(f"{self.base}/documents", params).json().get("totalResults")
        except Exception as exc:  # noqa: BLE001 - only used for reporting
            log.warning("Could not count remaining documents: %s", exc)
            return None

    def iter_documents(self, doc_def: str, updated_since: str, page_size: int = 200) -> Iterator[dict]:
        """POD documents with updateDate >= updated_since, oldest first."""
        params = {
            "q": f'documentDefGid eq "{doc_def}" and updateDate ge "{updated_since}"',
            "orderBy": "updateDate:asc",
            "fields": DOC_FIELDS,
            "expand": "contents",
            "limit": page_size,
            "offset": 0,
        }
        while True:
            page = self._get(f"{self.base}/documents", params).json()
            yield from page.get("items", [])
            if not page.get("hasMore"):
                return
            params["offset"] += page.get("count") or page_size

    def customer_for(self, owner_type: str, owner_gid: str) -> dict:
        """{'id': 'C1001', 'name': '...'} using CUSTOMER_ID_FIELD / CUSTOMER_NAME_FIELD on the owner record."""
        key = f"{owner_type}|{owner_gid}"
        if key in self._customer_cache:
            return self._customer_cache[key]
        resource = OWNER_RESOURCES.get(owner_type)
        if not resource:
            raise OtmPermanentError(f"No OTM resource known for owner type {owner_type}", 0, owner_gid)
        url = f"{self.base}/{resource}/{requests.utils.quote(owner_gid, safe='')}"
        plain = [f for f in (self.id_field, self.name_field) if f and not f.startswith("refnum:")]
        data = self._get(url, {"fields": ",".join(plain)}).json() if plain else {}
        refnums = None

        def read(spec: str) -> str | None:
            nonlocal refnums
            if not spec:
                return None
            if not spec.startswith("refnum:"):
                return data.get(spec)
            if refnums is None:
                refnums = self._get(url + "/refnums", {"limit": 1000}).json().get("items", [])
            qual = spec.split(":", 1)[1]
            for r in refnums:  # keys are e.g. shipmentRefnumQualGid / orderReleaseRefnumValue
                q = next((v for k, v in r.items() if k.endswith("RefnumQualGid")), None)
                if q in (qual, qual.split(".", 1)[-1]):
                    return next((v for k, v in r.items() if k.endswith("RefnumValue")), None)
            return None

        cust_gid = read(self.id_field)
        if not cust_gid and self.fallback_party:
            parties = self._get(url + "/involvedParties").json().get("items", [])
            cust_gid = next((p.get("involvedPartyContactGid") for p in parties
                             if p.get("involvedPartyQualGid") == self.fallback_party), None)
        result = {"id": (cust_gid or "UNKNOWN").split(".", 1)[-1], "name": read(self.name_field) or ""}
        self._customer_cache[key] = result
        return result

    def download_contents(self, doc: dict) -> list[Content]:
        contents = doc.get("contents") or {}
        items = contents.get("items")
        if items is None:  # not expanded - follow the collection link
            href = _link(contents, "self") or f"{_link(doc, 'self')}/contents"
            items = self._get(href).json().get("items", [])
        out = []
        for item in items:
            if item.get("isAwaitingVirusCheck"):
                raise OtmError(f"{item.get('documentContentGid')} is still awaiting virus check")
            resp = self._get(self._download_url(item), headers={"Accept": "*/*"})
            out.append(Content(item.get("documentContentGid", ""), resp.content,
                               (resp.headers.get("Content-Type") or "").split(";")[0].lower()))
        return out

    def _download_url(self, item: dict) -> str:
        href = _link(item, "canonical") or ""
        if "/custom-actions/download/" in href:
            return href
        self_href = _link(item, "self")
        if not self_href:
            raise OtmError(f"No download link for content {item.get('documentContentGid')}")
        return self_href.replace(f"{API_PATH}/documents/", f"{API_PATH}/custom-actions/download/documents/", 1)
