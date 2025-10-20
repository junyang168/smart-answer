import os
import base64
import json

import cv2
import requests
from PIL import Image
from google import genai
from google.genai.types import HttpOptions, Part

from backend.api.config import DATA_BASE_PATH



class ImageToText:
    def __init__(self, item_name:str) -> None:
        self.base_dir = str(DATA_BASE_PATH)
        self.item_name = item_name
        item_name_mp4 = item_name + '.mp4'
        self.video_path = os.path.join(self.base_dir, 'video', item_name_mp4 )


    def extract_text_from_frame(self, frame):
        try:
            # Convert the frame to a PIL Image
            pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

            client = genai.Client(http_options=HttpOptions(api_version="v1"),vertexai=True,project='gen-lang-client-0011233318',location='us-central1')
            response = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    "Extract Text from Image. Respond with extracted text ONLY.",
                    pil_image
                ],
            )
            return response.text
        except Exception as e:
            print(f"Error extracting text: {e}")
            return None


    def extract_slide_image(self, timestamp:int):    
        video = cv2.VideoCapture(self.video_path)
        video.set(cv2.CAP_PROP_POS_MSEC, timestamp )
        success, frame = video.read()
        if not success:
            return None        
#        roi, _ = screen_detector.detect_screen(frame)
        roi = None
        if roi:
            roi_x, roi_y, roi_w, roi_h = self.roi
            roi_frame =  frame[roi_y:roi_y+roi_h, roi_x:roi_x+roi_w]
        else:
            roi_frame = frame
        video.release()
        return roi_frame
    
    def extract_slide(self,  timestamp:int):
        frame = self.extract_slide_image(timestamp)
        return self.extract_text_from_frame(frame)

    def get_slide_image_url(self, script_base_dir:str, timestamp:int):        
        frame = self.extract_slide_image(timestamp)
        img_path = f"{self.item_name}-{timestamp}.jpg"
        cv2.imwrite(os.path.join(script_base_dir,'script_review', img_path), frame)
        return f"data/script_review/{img_path}"

if __name__ == '__main__' :
    item_name = 'S 190512-GH020035'
    extractor = ImageToText(item_name)
    url = extractor.get_slide_image_url('/Volumes/Jun SSD/data', 30000)
    print(url)
#    text = extractor.extract_slide(30000)
#    print(text)
