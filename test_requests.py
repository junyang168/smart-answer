import requests
import urllib.parse
from pprint import pprint

project_id = "_12章_-_安息日的爭議，褻瀆聖靈的罪"
encoded_id = urllib.parse.quote(project_id)
url = f"http://127.0.0.1:8222/admin/notes-to-sermon/sermon-project/{encoded_id}/audit-draft"

print(f"Requesting: {url}")
try:
    response = requests.post(url)
    print(f"Status: {response.status_code}")
    print("Response JSON:")
    try:
        pprint(response.json())
    except:
        print(response.text)
except Exception as e:
    import traceback
    traceback.print_exc()
