import os
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import google.auth
from io import BytesIO
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
token_path = Path("/Users/junyang/app/smart-answer/token.json")
creds = None
if token_path.exists():
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

if not creds:
    creds, _ = google.auth.default(scopes=SCOPES)

drive_service = build('drive', 'v3', credentials=creds)

html_content = """
<html>
<head>
<style>
    body { font-family: 'Arial'; font-size: 11pt; line-height: 1.5; }
    blockquote { margin-left: 36pt; padding-left: 12pt; border-left: 3pt solid #cccccc; color: #555555; background-color: #f9f9f9; padding: 10pt; }
</style>
</head>
<body>
<p>This is normal text.</p>
<blockquote>
<p><strong>Quote Header</strong></p>
<p>Quote paragraph 1.</p>
<p>Quote paragraph 2.</p>
</blockquote>
<p>More normal text.</p>
</body>
</html>
"""

media = MediaIoBaseUpload(
    BytesIO(html_content.encode('utf-8')),
    mimetype='text/html',
    resumable=True
)

file_metadata = {
    'name': 'Test Blockquote Format',
    'mimeType': 'application/vnd.google-apps.document'
}
file = drive_service.files().create(
    body=file_metadata,
    media_body=media,
    fields='id, webViewLink'
).execute()

print("Created test doc:", file.get('webViewLink'))
