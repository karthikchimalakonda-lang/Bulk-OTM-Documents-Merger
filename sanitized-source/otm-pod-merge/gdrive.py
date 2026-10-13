"""
Google Drive upload via the REST API (google-auth + requests, no heavy client library).

Credentials JSON (stored in Object Storage, never in the image) may be either:
  * a service account key   {"type": "service_account", ...}   -> files must go to a Shared Drive
                                                                   (or set GDRIVE_IMPERSONATE for domain-wide delegation)
  * an OAuth user token      {"client_id", "client_secret", "refresh_token"}
"""
import json
import logging
import time

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/drive"]
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
COMMON = {"supportsAllDrives": "true"}

log = logging.getLogger("otm.gdrive")


class Drive:
    def __init__(self, credentials_json: bytes, impersonate: str = ""):
        info = json.loads(credentials_json)
        if info.get("type") == "service_account":
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            if impersonate:
                creds = creds.with_subject(impersonate)
        elif info.get("refresh_token"):
            creds = user_credentials.Credentials(
                token=None, refresh_token=info["refresh_token"], client_id=info["client_id"],
                client_secret=info["client_secret"], token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
                scopes=SCOPES)
        else:
            raise ValueError("Drive credentials must be a service-account key or contain a refresh_token")
        self.s = AuthorizedSession(creds)

    def _call(self, method: str, url: str, attempts: int = 4, **kw):
        """HTTP call with exponential backoff on throttling (429/403 rate limits) and 5xx."""
        for attempt in range(1, attempts + 1):
            try:
                resp = self.s.request(method, url, timeout=(15, 300), **kw)
            except Exception as exc:  # noqa: BLE001 - network errors
                if attempt == attempts:
                    raise
                log.warning("Drive %s failed (%s), retry %d", method, exc, attempt)
            else:
                rate_limited = resp.status_code == 403 and "rateLimitExceeded" in resp.text
                if resp.status_code < 500 and resp.status_code != 429 and not rate_limited:
                    return resp
                if attempt == attempts:
                    return resp
                log.warning("Drive %s returned %s, retry %d", method, resp.status_code, attempt)
            time.sleep(min(2 ** attempt, 30))
        return resp

    def _check(self, resp):
        if resp.status_code >= 400:
            hint = {401: "credentials rejected - check the service account key",
                    403: "no permission - is the service account a Content manager of the folder?",
                    404: "folder/file not found - check GDRIVE_FOLDER_ID and that it is shared with the service account"}
            raise RuntimeError(f"Drive API {resp.status_code} ({hint.get(resp.status_code, 'error')}): {resp.text[:300]}")
        return resp

    def ensure_folder(self, name: str, parent_id: str) -> str:
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{safe}' and mimeType = '{FOLDER_MIME}' and '{parent_id}' in parents and trashed = false"
        resp = self._check(self._call("GET", API, params={"q": q, "fields": "files(id)", "includeItemsFromAllDrives": "true",
                                                   "corpora": "allDrives", **COMMON}))
        files = resp.json().get("files", [])
        if files:
            return files[0]["id"]
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        return self._check(self._call("POST", API, params={"fields": "id", **COMMON}, json=body)).json()["id"]

    def upload_pdf(self, data: bytes, name: str, folder_id: str, file_id: str | None = None) -> dict:
        """Create, or replace the content of an existing file (keeps its id and share link)."""
        params = {"uploadType": "resumable", "fields": "id,name,webViewLink", **COMMON}
        if file_id:
            init = self._call("PATCH", f"{UPLOAD_API}/{file_id}", params=params, json={"name": name},
                                headers={"X-Upload-Content-Type": "application/pdf"})
            if init.status_code == 404:  # deleted in Drive - recreate
                return self.upload_pdf(data, name, folder_id)
        else:
            init = self._call("POST", UPLOAD_API, params=params, json={"name": name, "parents": [folder_id]},
                               headers={"X-Upload-Content-Type": "application/pdf"})
        session_url = self._check(init).headers["Location"]
        return self._check(self._call("PUT", session_url, data=data, headers={"Content-Type": "application/pdf"})).json()

    def share_with_anyone(self, file_id: str) -> None:
        self._check(self._call("POST", f"{API}/{file_id}/permissions", params=COMMON,
                                json={"role": "reader", "type": "anyone"}))
