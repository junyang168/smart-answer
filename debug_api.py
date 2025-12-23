
# ... imports
import requests
import json

base_url = "http://127.0.0.1:8222/admin/notes-to-sermon"

print("--- Listing Projects ---")
try:
    res = requests.get(f"{base_url}/sermon-projects")
    print(f"Status: {res.status_code}")
    if res.status_code == 200:
        projects = res.json()
        for p in projects:
            print(f" - Title: {p.get('title')}, ID: {p.get('id')}")
            # Try to fetch draft for this ID
            d_url = f"{base_url}/sermon-project/{p.get('id')}/draft"
            print(f"   Fetching draft: {d_url}")
            d_res = requests.get(d_url)
            print(f"   Draft Status: {d_res.status_code}")
            if d_res.status_code != 200:
                print(f"   Draft Error: {d_res.text}")
    else:
        print(f"Error: {res.text}")
except Exception as e:
    print(f"Failed: {e}")


# The project ID is "門徒使命"
project_id = "門徒使命"
encoded_id = urllib.parse.quote(project_id)

base_url = "http://localhost:8000/api/admin/notes-to-sermon"

print(f"Testing API for project: {project_id}")

# 1. Get Source
print("\n--- Getting Source ---")
try:
    url = f"{base_url}/sermon-project/{encoded_id}/source"
    print(f"GET {url}")
    res = requests.get(url)
    print(f"Status: {res.status_code}")
    if res.status_code == 200:
        data = res.json()
        content = data.get("content", "")
        print(f"Length: {len(content)}")
        print(f"Preview: {content[:50]}...")
    else:
        print(f"Error: {res.text}")
except Exception as e:
    print(f"Failed: {e}")

# 2. Get Draft
print("\n--- Getting Draft ---")
try:
    url = f"{base_url}/sermon-project/{encoded_id}/draft"
    print(f"GET {url}")
    res = requests.get(url)
    print(f"Status: {res.status_code}")
    if res.status_code == 200:
        data = res.json()
        content = data.get("content", "")
        print(f"Length: {len(content)}")
        print(f"Preview: {content[:50]}...")
    else:
        print(f"Error: {res.text}")
except Exception as e:
    print(f"Failed: {e}")
