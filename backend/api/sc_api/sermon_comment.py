import os
import json



class SermonCommentManager:

    def get_key(self, user_id:str, item:str):
        return f"sermon_comment/{user_id}/{item}"

    def set_bookmark(self, user_id:str, item:str, index:str):
        bookmark = {
            'index': index
        }
        bookmark_str = json.dumps(bookmark, ensure_ascii=False)
#        self.s3.put_object(Body=bookmark_str, Bucket=self.bucket_name, Key=self.get_key(user_id, item))

    def get_bookmark(self, user_id:str, item:str):
        try:
 #           response = self.s3.get_object(Bucket=self.bucket_name, Key=self.get_key(user_id, item))
 #           bookmark_data =  response['Body'].read().decode('utf-8')
            bookmark_data = ''
            bookmark =  json.loads(bookmark_data)
            return bookmark
        except Exception as e:
            return {}


    def add_comment(self, user_id:str, item:str, comment:str)->str:
        pass

if __name__ == "__main__":
    sermon_comment = SermonCommentManager()
    x = sermon_comment.get_bookmark("junyang168@gmail.com", "123")
    sermon_comment.set_bookmark("junyang168@gmail.com", "2019-2-18 良心", "[1_28]")
    print(sermon_comment.get_bookmark("junyang168@gmail.com", "2019-2-18 良心"))
