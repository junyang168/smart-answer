
import sys
import os

# Add backend to path
sys.path.append(os.getcwd())

from backend.api.lecture_manager import get_series
from backend.api.sermon_converter_service import update_sermon_project_type

SERIES_ID = "0baa704e-dcc6-4b7d-a004-8332e274701e"

def main():
    print(f"Repairing series {SERIES_ID}...")
    series = get_series(SERIES_ID)
    if not series:
        print("Series not found.")
        return

    p_type = series.project_type
    print(f"Series type is: {p_type}")
    
    count = 0
    for lecture in series.lectures:
        for project_id in lecture.project_ids:
            print(f"Updating project {project_id} to {p_type}...")
            if update_sermon_project_type(project_id, p_type):
                count += 1
            else:
                print(f"Failed to find/update project {project_id}")
                
    print(f"Updated {count} projects.")

if __name__ == "__main__":
    main()
