from pathlib import Path
from rich.console import Console

console = Console()

# Import the existing singleton
from backend.api.gemini_client import gemini_client
from google.genai import types

import time

def call_google_nano_banana_2(prompt: str, output_image_path: str, max_retries: int = 3) -> bool:
    """
    Highly advanced integration with Google Nano Banana 2 (via Gemini Imagen 3).
    Includes rate limit handling for Quota limits.
    """
    console.print(f"🍌 [bold yellow]Nano Banana 2 generating:[/bold yellow] '{prompt}'")
    
    from google import genai
    vertex_client = genai.Client()
    
    for attempt in range(max_retries):
        try:
            result = vertex_client.models.generate_images(
                model='imagen-3.0-generate-001',
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/jpeg",
                    aspect_ratio="16:9"
                )
            )
            if result.generated_images:
                with open(output_image_path, "wb") as f:
                    f.write(result.generated_images[0].image.image_bytes)
                return True
            else:
                console.print("[bold red]Nano Banana API returned no images.[/bold red]")
                return False
        except Exception as e:
            wait_time = 20 * (attempt + 1)
            console.print(f"[bold yellow]API Error ({e}). Waiting {wait_time}s before retry {attempt+1}/{max_retries}...[/bold yellow]")
            time.sleep(wait_time)
                
    console.print(f"[bold red]Failed after {max_retries} retries.[/bold red]")
    return False

def process_visuals_for_scenes(storyboard: list, work_dir: Path) -> list:
    """
    Iterates over the storyboard, generating a real AI image for each scene via Nano Banana.
    """
    for item in storyboard:
        scene_id = item.get("scene_id")
        prompt = item.get("visual_prompt", "Beautiful cinematic church background")
        duration_sec = item.get("duration_sec", 5.0)
        
        visual_filename = f"scene_{scene_id}_visual.jpg"
        visual_filepath = work_dir / visual_filename
        
        if item.get("visual_source"):
            console.print(f"[dim]Skipping image generation for Scene {scene_id}, driven by continuous video source: {item['visual_source']}[/dim]")
            continue
            
        if not visual_filepath.exists():
            success = call_google_nano_banana_2(prompt, str(visual_filepath))
            if not success:
                # Create a fallback placeholder image if generation fails
                from PIL import Image
                img = Image.new('RGB', (1920, 1080), color=(50, 50, 50))
                img.save(str(visual_filepath))
                console.print(f"[dim]Generated fallback solid image for Nano Banana.[/dim]")
        else:
            console.print(f"[dim]Cache hit for Nano Banana 2 image: {visual_filename}[/dim]")
            
        item["visual_filepath"] = str(visual_filepath)
        
    return storyboard
