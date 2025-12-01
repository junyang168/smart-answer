import requests
import json
import sys

# Configuration
API_BASE_URL = "http://localhost:8000/sc_api"
USER_ID = "junyang168@gmail.com" # Assuming this user exists and has permissions
SERIES_ID = "series-1" # Replace with a valid series ID from sermon_series.json if known, or I'll try to find one first

def get_first_series_id():
    try:
        response = requests.get(f"{API_BASE_URL}/sermon_series")
        response.raise_for_status()
        series_list = response.json()
        if series_list:
            return series_list[0]['id']
    except Exception as e:
        print(f"Error fetching series: {e}")
    return None

def test_generate_series_metadata(series_id):
    print(f"Testing generate_series_metadata for series_id: {series_id}")
    url = f"{API_BASE_URL}/generate_series_metadata"
    payload = {
        "user_id": USER_ID,
        "series_id": series_id
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        print("Success! Generated Metadata:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        
        # Validation
        required_fields = ["title", "summary", "topics", "keypoints"]
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            print(f"FAILED: Missing fields: {missing_fields}")
        else:
            print("PASSED: All required fields present.")
            
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        print(f"Response: {e.response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    series_id = get_first_series_id()
    if series_id:
        test_generate_series_metadata(series_id)
    else:
        print("Could not find any series to test.")
