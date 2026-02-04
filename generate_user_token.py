
import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Define scopes
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/cloud-platform'
]

CLIENT_SECRET_FILE = 'client_secret.json'
TOKEN_FILE = 'token.json'

def main():
    if not os.path.exists(CLIENT_SECRET_FILE):
        print(f"Error: {CLIENT_SECRET_FILE} not found.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRET_FILE, SCOPES
    )
    
    # Run local server for auth
    creds = flow.run_local_server(port=0)
    
    # Save the credentials for the next run
    with open(TOKEN_FILE, 'w') as token:
        token.write(creds.to_json())
        
    print(f"Success! Token saved to {TOKEN_FILE}")

if __name__ == '__main__':
    main()
