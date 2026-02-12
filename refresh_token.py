"""
Refresh the Google OAuth token stored in token.json.
Usage: python refresh_token.py
"""
import json
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',
]

token_path = Path(__file__).parent / "token.json"

if not token_path.exists():
    print(f"ERROR: {token_path} not found.")
    exit(1)

creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

print(f"Token expired: {creds.expired}")
print(f"Token expiry:  {creds.expiry}")

if creds.expired and creds.refresh_token:
    print("Refreshing token...")
    creds.refresh(Request())
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Token refreshed successfully!")
    print(f"New expiry: {creds.expiry}")
elif not creds.expired:
    print("Token is still valid, no refresh needed.")
elif not creds.refresh_token:
    print("ERROR: No refresh_token found. You need to re-authorize.")
    print("Run the following to generate a new token.json:")
    print("  python -c \"from google_auth_oauthlib.flow import InstalledAppFlow; "
          "flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', "
          f"{SCOPES}); "
          "creds = flow.run_local_server(port=0); "
          "open('token.json','w').write(creds.to_json())\"")
