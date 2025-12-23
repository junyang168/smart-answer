import sys
import os

# Add project root to path (smart-answer/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

try:
    from backend.api.config import GOOGLE_DRIVE_FOLDER_ID
    from googleapiclient.discovery import build
    import google.auth

    print(f"Scanning Drive folder structure for root: {GOOGLE_DRIVE_FOLDER_ID}")
    
    # Authenticate
    SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']
    credentials, _ = google.auth.default(scopes=SCOPES)
    service = build('drive', 'v3', credentials=credentials)

    # Recursive BFS
    folders = [(GOOGLE_DRIVE_FOLDER_ID, "ROOT")]
    queue = [(GOOGLE_DRIVE_FOLDER_ID, "ROOT")]
    
    count = 0
    while queue:
        current_id, current_path = queue.pop(0)
        count += 1
        print(f"[{count}] Found Folder: {current_path} ({current_id})")
        
        page_token = None
        while True:
            response = service.files().list(
                q=f"'{current_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token
            ).execute()
            
            for file in response.get('files', []):
                new_path = f"{current_path} / {file.get('name')}"
                folders.append((file.get('id'), new_path))
                queue.append((file.get('id'), new_path))
                
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
                
    print("-" * 30)
    print(f"Total Folders Found: {len(folders)}")

except Exception as e:
    print(f"Script Error: {e}")
    import traceback
    traceback.print_exc()
