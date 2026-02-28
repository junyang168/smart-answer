import requests
import json

url = "http://127.0.0.1:8222/admin/notes-to-sermon/sermon-project/_12%E7%AB%A0_-_%E5%AE%89%E6%81%AF%E6%97%A5%E7%9A%84%E7%88%AD%E8%AD%B0%EF%BC%8C%E8%A4%BB%E7%80%86%E8%81%96%E9%9D%88%E7%9A%84%E7%BD%AA/audit-draft"
# Try fetching the list of projects first to get a valid ID
res = requests.get("http://127.0.0.1:8222/admin/notes-to-sermon/sermon-projects")
if res.status_code == 200:
    projects = res.json()
    if projects:
        valid_id = projects[-1]['id']
        url = f"http://127.0.0.1:8222/admin/notes-to-sermon/sermon-project/{valid_id}/audit-draft"
        print(f"Using Sermon ID: {valid_id}")

print(f"POST {url}")
try:
    response = requests.post(url)
    print("Status Code:", response.status_code)
    print("Response Headers:", response.headers)
    print("Raw Output:")
    print(response.text)
except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")
