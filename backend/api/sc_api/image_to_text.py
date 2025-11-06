from __future__ import annotations

import os
from typing import Optional

import cv2
from google import genai
from google.genai import types

from backend.api.config import DATA_BASE_PATH


class ImageToText:
    def __init__(self, item_name: str) -> None:
        self.base_dir = str(DATA_BASE_PATH)
        self.item_name = item_name
        item_name_mp4 = f"{item_name}.mp4"
        self.video_path = os.path.join(self.base_dir, "video", item_name_mp4)
        self._client = genai.Client()
        self._model_name = "gemini-2.5-pro"

    def extract_text_from_frame(self, frame, *, as_markdown: bool = True) -> Optional[str]:
        if frame is None:
            return None

        prompt = (
            "Extract the visible text content from this slide image and format the result as Markdown. "
            "Preserve headings, bullet lists, and line breaks. "
            if as_markdown
            else "Extract all text from the provided slide image. Respond with text only."
        )

        success, buffer = cv2.imencode(".png", frame)
        if not success:
            return None
        
        generate_content_config = types.GenerateContentConfig(
            thinking_config = types.ThinkingConfig(
                thinking_budget=-1,
            )
        )
        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            types.Part.from_bytes(data=buffer.tobytes(), mime_type="image/png"),
                        ],
                    )
                ],
                config=generate_content_config
            )
        except Exception as exc:  # pragma: no cover - network failures
            print(f"Error extracting text: {exc}")
            return None

        if not response:
            return None
        text = getattr(response, "text", None)
        return text.strip() if isinstance(text, str) and text.strip() else None

    def extract_slide_image(self, timestamp: int):
        video = cv2.VideoCapture(self.video_path)
        video.set(cv2.CAP_PROP_POS_MSEC, timestamp)
        success, frame = video.read()
        video.release()
        if not success:
            return None

        # Potential future ROI extraction can be added here.
        return frame

    def extract_slide(self, timestamp: int) -> Optional[str]:
        frame = self.extract_slide_image(timestamp)
        return self.extract_text_from_frame(frame)

    def get_slide_image_url(self, script_base_dir: str, timestamp: int) -> Optional[str]:
        frame = self.extract_slide_image(timestamp)
        if frame is None:
            return None
        img_path = f"{self.item_name}-{timestamp}.jpg"
        cv2.imwrite(os.path.join(script_base_dir, "script_review", img_path), frame)
        return f"data/script_review/{img_path}"


if __name__ == "__main__":
    item_name = "S 190512-GH020035"
    extractor = ImageToText(item_name)
    url = extractor.get_slide_image_url("/Volumes/Jun SSD/data", 30000)
    print(url)
    # text = extractor.extract_slide(30000)
    # print(text)
