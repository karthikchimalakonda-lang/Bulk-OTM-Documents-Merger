"""
One-time Google sign-in for the Drive upload (OAuth "Desktop app" client).

  python deploy/gdrive_authorize.py <client_secret.json> [out.json]

Opens the browser; sign in with the account whose Drive should receive the files and allow access.
Writes {client_id, client_secret, refresh_token, token_uri} to out.json (default: gdrive_credentials.json
in the project root). Upload that file to Object Storage as POD_Doc_Merge/_config/gdrive_credentials.json.
"""
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]

client_file = sys.argv[1]
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "gdrive_credentials.json"

flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
# prompt=consent + offline guarantees a refresh token even if this account authorized before.
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent", open_browser=False,
                              authorization_prompt_message="Open this URL to sign in:\n{url}\n")
if not creds.refresh_token:
    sys.exit("No refresh token returned - revoke the app at myaccount.google.com/permissions and retry")

out.write_text(json.dumps({
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "refresh_token": creds.refresh_token,
    "token_uri": creds.token_uri,
}, indent=1))
print(f"Saved Drive credentials to {out}")
