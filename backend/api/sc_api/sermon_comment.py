import os
import json
from backend.api.config import DATA_BASE_DIR

class SermonCommentManager:
    
    def __init__(self):
        self.bookmark_file = os.path.join(DATA_BASE_DIR, 'bookmark', 'bookmark.json')

    def _load_bookmarks(self):
        if not os.path.exists(self.bookmark_file):
            return {}
        try:
            with open(self.bookmark_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_bookmarks(self, bookmarks):
        os.makedirs(os.path.dirname(self.bookmark_file), exist_ok=True)
        with open(self.bookmark_file, 'w', encoding='utf-8') as f:
            json.dump(bookmarks, f, ensure_ascii=False, indent=4)

    def get_key(self, user_id:str, item:str):
        return f"{user_id}/{item}"

    def set_bookmark(self, user_id:str, item:str, index:str):
        bookmarks = self._load_bookmarks()
        key = self.get_key(user_id, item)
        bookmarks[key] = {
            'index': index
        }
        self._save_bookmarks(bookmarks)

    def get_bookmark(self, user_id:str, item:str):
        bookmarks = self._load_bookmarks()
        key = self.get_key(user_id, item)
        return bookmarks.get(key, {})


    def add_comment(self, user_id:str, item:str, comment:str)->str:
        pass

if __name__ == "__main__":
    sermon_comment = SermonCommentManager()
    # Test setting a bookmark
    sermon_comment.set_bookmark("junyang168@gmail.com", "2019-2-18 良心", "[1_28]")
    # Test getting the bookmark
    bookmark = sermon_comment.get_bookmark("junyang168@gmail.com", "2019-2-18 良心")
    print(f"Retrieved bookmark: {bookmark}")
