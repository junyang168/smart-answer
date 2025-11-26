import sys
import os
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

# Mock missing dependencies
sys.modules["fastapi"] = MagicMock()
sys.modules["fastapi.responses"] = MagicMock()
sys.modules["fastapi.staticfiles"] = MagicMock()
sys.modules["fastapi.middleware.cors"] = MagicMock()
sys.modules["git"] = MagicMock()

# Add backend directory and project root to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
project_root = os.path.abspath(os.path.join(backend_dir, '..'))
sys.path.append(backend_dir)
sys.path.append(project_root)

from api.sc_api.sermon_comment import SermonCommentManager
from api.config import DATA_BASE_DIR

def test_sermon_bookmark():
    print("Starting Sermon Bookmark Manager Test...")
    
    # Setup: Ensure clean state for test
    bookmark_dir = os.path.join(DATA_BASE_DIR, 'bookmark')
    bookmark_file = os.path.join(bookmark_dir, 'bookmark.json')
    
    # Back up existing file if it exists
    backup_file = None
    if os.path.exists(bookmark_file):
        backup_file = bookmark_file + ".bak"
        shutil.copy2(bookmark_file, backup_file)
        print(f"Backed up existing bookmarks to {backup_file}")
    
    try:
        # Initialize Manager
        manager = SermonCommentManager()
        
        # Test Data
        user_id = "test_user@example.com"
        item = "test_sermon_123"
        index = "[10_20]"
        
        # 1. Test Setting a Bookmark
        print(f"Setting bookmark for {user_id}, item {item} to {index}")
        manager.set_bookmark(user_id, item, index)
        
        # Verify file creation
        if not os.path.exists(bookmark_file):
            print("FAILED: Bookmark file was not created.")
            return False
            
        # 2. Test Getting a Bookmark
        print(f"Getting bookmark for {user_id}, item {item}")
        retrieved_bookmark = manager.get_bookmark(user_id, item)
        
        if retrieved_bookmark.get('index') == index:
            print("SUCCESS: Retrieved bookmark matches set bookmark.")
        else:
            print(f"FAILED: Retrieved bookmark {retrieved_bookmark} does not match expected index {index}")
            return False
            
        # 3. Verify JSON Content
        with open(bookmark_file, 'r') as f:
            data = json.load(f)
            key = manager.get_key(user_id, item)
            if key in data and data[key]['index'] == index:
                 print("SUCCESS: JSON file content is correct.")
            else:
                 print(f"FAILED: JSON file content is incorrect. Data: {data}")
                 return False

        # 4. Test Updating a Bookmark
        new_index = "[30_40]"
        print(f"Updating bookmark for {user_id}, item {item} to {new_index}")
        manager.set_bookmark(user_id, item, new_index)
        
        retrieved_bookmark = manager.get_bookmark(user_id, item)
        if retrieved_bookmark.get('index') == new_index:
             print("SUCCESS: Updated bookmark matches.")
        else:
             print(f"FAILED: Updated bookmark {retrieved_bookmark} does not match expected index {new_index}")
             return False

        return True

    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    finally:
        # Cleanup: Restore backup if it existed, otherwise delete created file
        if backup_file:
            shutil.move(backup_file, bookmark_file)
            print(f"Restored original bookmarks from {backup_file}")
        elif os.path.exists(bookmark_file):
            # If we created the file and there was no backup, we might want to keep it or delete it.
            # For a test, usually we clean up. But since this writes to the actual DB dir, let's be careful.
            # The prompt said "store data in DATA_BASE_DIR", so maybe I should leave it?
            # But this is a test script. I'll remove the test entry if possible, but for now restoring backup or leaving it is fine.
            # Actually, let's just leave it as is or maybe delete the test key?
            # Simplest is to just print that we are done.
            pass

if __name__ == "__main__":
    if test_sermon_bookmark():
        print("\nALL TESTS PASSED")
    else:
        print("\nTESTS FAILED")
