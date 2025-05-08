import requests
import os
import base64
import os
from google import genai
from google.genai.types import HttpOptions, Part
import json
from content_store import  HybridScore



class KeypointSearch:

    def get_keypoints(self, kps):
        return '\n'.join([ k['keypoint'] for k in kps])
    
    def __init__(self):

        response = requests.get(f"{os.environ['SERMON_BASE_URL']}/web/data/script_keypoints/keypoints.json")
        if response.status_code == 200:
            kps = response.json()
            docs =  [
                f"""<document id="{s.get('id')}" title="{s.get('theme')}">
                {self.get_keypoints(s['kps'])}
                </document>""" for s in kps]
            self.keypoints = f"<documents>{docs}</documents>"            
        else:
            raise ValueError("Failed to load keypoints from the provided URL")
        self.json_format = """
        ```json
        [
            {"id": "id1"},
            {"id": "id2"}，
            {"id": "id3"}
        ]    
        ```
        """
        self.prompt_template = """
        Given the following context which contains several articles and the question, identify up to 3 articles from the context whose biblical principles and topics are most relevant to forming an opinion on this  to answering the question. List the id of these articles with JSON format.
        {}
        Context: {}
        Question: {}
        Relevant article Ids:
        """
    def markdown_to_json(self, markdown: str, is_json: bool = False) -> dict:
        """Convert markdown-formatted JSON string to Python dictionary.
        
        Args:
            markdown: String containing JSON wrapped in markdown code block
            
        Returns:
            Parsed dictionary from JSON content
        """
        if is_json:
            return json.loads(markdown)
        
        json_tag = "```json"
        start_idx = markdown.find(json_tag)
        if start_idx < 0:
            return markdown
        end_idx = markdown.find("```", start_idx + len(json_tag))
        if end_idx == -1:
            raise ValueError("No closing code block found in markdown")
        json_str = markdown[start_idx + len(json_tag):end_idx].strip()
        return json.loads(json_str)

    def search(self, question ):
        client = genai.Client(http_options=HttpOptions(api_version="v1"),vertexai=True,project='gen-lang-client-0011233318',location='us-central1')
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                self.prompt_template.format(self.json_format, self.keypoints, question)
            ],
        )
        resp = response.text
        relevants =  self.markdown_to_json(resp)
        return [ HybridScore( id=r['id'], colbert_score=0, dense_score=0, bm25_score= 0, hybrid_score=0.7, content_id=r['id']) for r in relevants]
        
keypointSearch = KeypointSearch()        

if __name__ == "__main__":
    search = KeypointSearch()
    res = search.search('路加福音耶穌家譜有無跳代？怎麼看出來的？')
    print(res)
