import sys
from pathlib import Path
import azure.cognitiveservices.speech as speechsdk
from rich.console import Console

# Import configuration
sys.path.append("/Users/junyang/app/smart-answer/")
from backend.api.config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION

console = Console()

def main():
    if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
        console.print("[bold red]Error: AZURE_SPEECH_KEY or AZURE_SPEECH_REGION is not set in your config (.env)![/bold red]")
        sys.exit(1)

    ssml_file = Path("/opt/homebrew/var/www/church/web/data/sermon_to_video/主恩的滋味/主恩的滋味.ssml")
    audio_file = ssml_file.with_suffix('.mp3')
    
    if not ssml_file.exists():
        console.print(f"[bold red]Cannot find SSML file at {ssml_file}[/bold red]")
        sys.exit(1)

    console.print(f"[bold cyan]Reading SSML from:[/bold cyan] {ssml_file}")
    with open(ssml_file, "r", encoding="utf-8") as f:
        ssml_content = f.read()

    config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
    config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(audio_file))

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=audio_config)

    def bookmark_listener(evt):
        offset_seconds = evt.audio_offset / 10000000.0
        console.print(f"[bold yellow]🎯 MARKER REACHED:[/bold yellow] [green]'{evt.text}'[/green] at [magenta]{offset_seconds:.3f} s[/magenta]")

    # Connect the event listener
    synthesizer.bookmark_reached.connect(bookmark_listener)

    console.print("[bold cyan]Synthesizing audio and waiting for bookmarks...[/bold cyan]")
    result = synthesizer.speak_ssml_async(ssml_content).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        console.print(f"\n[bold green]✅ Success![/bold green] Audio perfectly saved to:\n[blue]{audio_file}[/blue]")
    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        console.print(f"[bold red]Canceled by Azure:[/bold red] {cancellation_details.reason}")
        if cancellation_details.error_details:
            console.print(f"[bold red]Error details:[/bold red] {cancellation_details.error_details}")
        sys.exit(1)
    else:
        console.print(f"[bold red]Speech synthesis failed:[/bold red] {result.reason}")
        sys.exit(1)

if __name__ == "__main__":
    main()
