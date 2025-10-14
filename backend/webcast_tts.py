"""Utilities for producing webcast audio using Gemini's TTS preview model.

This module provides a helper that takes SSML input and saves the generated
speech as an MP3 file. It is designed to support church webcast automation
pipelines where content is prepared in SSML ahead of time.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
from pathlib import Path
from typing import Iterable, Optional

from google import genai
from google.genai import types

MODEL_NAME = "gemini-2.5-pro-preview-tts"
DEFAULT_VOICE_NAME = "Zephyr"
DEFAULT_OUTPUT_SUFFIX = ".mp3"
DEFAULT_VERTEX_LOCATION = "us-central1"


def _strtobool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _default_speaker_voice_map() -> dict[str, str]:
    """Speaker → voice mapping used when multi-speaker synthesis is available."""

    return {
        "male": "Charon",
        "male1": "Charon",
        "female": "Zephyr",
        "female1": "Zephyr",
        "host": "Charon",
        "guest": "Zephyr",
    }


class GeminiTTSClient:
    """Wrapper around the Gemini client focused on SSML-to-audio synthesis."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        location: Optional[str] = None,
        use_vertex: Optional[bool] = None,
    ) -> None:
        api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY"
        )
        if use_vertex is None:
            use_vertex = _strtobool(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"))
        if use_vertex is None:
            use_vertex = api_key is None

        if use_vertex:
            project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
            location = location or os.environ.get("GOOGLE_CLOUD_LOCATION")
            if not project:
                raise EnvironmentError(
                    "Vertex AI mode selected but GOOGLE_CLOUD_PROJECT is not set."
                )
            vertex_kwargs = {
                "vertexai": True,
                "project": project,
                "location": location or DEFAULT_VERTEX_LOCATION,
            }
            self._client = genai.Client(**vertex_kwargs)
        else:
            if not api_key:
                raise EnvironmentError(
                    "No API key found. Set GEMINI_API_KEY/GOOGLE_API_KEY or enable Vertex AI."
                )
            self._client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),)
        self._use_vertex = use_vertex

    def synthesize_ssml_to_mp3(
        self,
        *,
        ssml: str,
        output_path: Path,
        voice_name: Optional[str] = None,
    ) -> Path:
        """Generate MP3 audio for the provided SSML and write it to `output_path`.

        Args:
            ssml: The SSML string to synthesize.
            output_path: Destination path where the MP3 file will be saved. The
                parent directory must exist.
            voice_name: Optional Gemini prebuilt voice name to override the default.

        Returns:
            The path to the written MP3 file.
        """
        normalized_ssml = _rewrite_voice_tags(ssml).strip()
        if not normalized_ssml:
            raise ValueError("SSML input is empty after stripping whitespace.")

        resolved_path = output_path
        if not resolved_path.suffix:
            resolved_path = resolved_path.with_suffix(DEFAULT_OUTPUT_SUFFIX)
        elif resolved_path.suffix.lower() != DEFAULT_OUTPUT_SUFFIX:
            raise ValueError(
                f"Output path must use the {DEFAULT_OUTPUT_SUFFIX} extension."
            )

        speakers = _extract_speakers(normalized_ssml)

        if not self._use_vertex and speakers:
            speech_config = types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=
                    [
                        _build_speaker_voice_config(speaker, voice_name)
                        for speaker in sorted(speakers)
                    ]
                )
            )
        else:
            if self._use_vertex and len(speakers) > 1:
                print(
                    "Warning: Vertex AI TTS currently ignores per-speaker voices; "
                    "falling back to a single voice for all segments."
                )
            speech_config = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name or DEFAULT_VOICE_NAME
                    )
                )
            )

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=normalized_ssml)],
            )
        ]

        audio_chunks: list[bytes] = []
        mime_type: Optional[str] = None

        try:
            stream = self._client.models.generate_content_stream(
                model=MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=1,
                    response_modalities=["audio"],
                    speech_config=speech_config,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "API keys are not supported" in message:
                raise PermissionError(
                    "Gemini returned an authentication error. This model currently "
                    "requires Vertex AI credentials. Set GOOGLE_CLOUD_PROJECT / "
                    "GOOGLE_CLOUD_LOCATION and authenticate with Application Default "
                    "Credentials (e.g. service account JSON) or rerun with --vertex."
                ) from exc
            raise

        try:
            for chunk in stream:
                for candidate in _safe_iter(chunk.candidates):
                    for part in _safe_iter(getattr(candidate.content, "parts", None)):
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            decoded = _decode_inline_audio(inline.data)
                            audio_chunks.append(decoded)
                            if inline.mime_type:
                                mime_type = inline.mime_type
                    # Occasionally the service streams transcript text; ensure it is surfaced.
                    if (
                        getattr(candidate, "finish_reason", None) == "SAFETY"
                        and not audio_chunks
                    ):
                        raise RuntimeError("Generation stopped by safety settings.")

                if getattr(chunk, "error", None):
                    raise RuntimeError(f"Gemini streaming error: {chunk.error}")
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "not allowlisted to request audio output" in message:
                raise PermissionError(
                    "Your account is not allowlisted for Gemini audio output yet. "
                    "Request TTS access for the gemini-2.5-pro-preview-tts model via "
                    "the Google AI Studio allowlist form or use an approved voice model."
                ) from exc
            raise

        if not audio_chunks:
            raise RuntimeError("Gemini did not return any audio data for the supplied SSML.")

        resolved_path.write_bytes(b"".join(audio_chunks))

        # Warn if Gemini did not explicitly return MP3 data.
        if mime_type and mime_type not in {"audio/mp3", "audio/mpeg"}:
            print(
                "Warning: Received audio with MIME type"
                f" {mime_type}. Saved bytes using the .mp3 extension."
            )

        return resolved_path


def _decode_inline_audio(payload: object) -> bytes:
    """Decode audio payloads that may be returned as bytes or base64 strings."""
    if isinstance(payload, bytes):
        try:
            return base64.b64decode(payload, validate=True)
        except binascii.Error:
            return payload
    if isinstance(payload, str):
        return base64.b64decode(payload.encode("utf-8"))
    if isinstance(payload, memoryview):
        return _decode_inline_audio(payload.tobytes())
    raise TypeError(f"Unsupported inline audio payload type: {type(payload)!r}")


def _safe_iter(items: Optional[Iterable]) -> Iterable:
    """Yield from iterable-like objects without raising when empty."""
    return items or []


VOICE_BLOCK_RE = re.compile(
    r"<voice\s+(?:name|id)=(?P<quote>['\"])(?P<speaker>[^'\"<>]+)(?P=quote)(?:\s+[^\s<>/=]+(?:\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s<>]+))?)*\s*>(?P<body>.*?)</voice>",
    flags=re.IGNORECASE | re.DOTALL,
)

INTERSTITIAL_TAG_RE = re.compile(
    r"(</voice>\s*)(?:<break\b[^>]*>\s*|<!--.*?-->\s*)+(?=<voice\b)",
    flags=re.IGNORECASE | re.DOTALL,
)

SPEAKER_PREFIX_RE = re.compile(r"^([^\n:]+):", re.MULTILINE)


def _strip_interstitial_tags(ssml: str) -> str:
    previous = None
    cleaned = ssml
    while previous != cleaned:
        previous = cleaned
        cleaned = INTERSTITIAL_TAG_RE.sub(r"\1", cleaned)
    return cleaned


def _extract_speakers(ssml: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in SPEAKER_PREFIX_RE.finditer(ssml)
        if match.group(1).strip()
    }


def _build_speaker_voice_config(
    speaker_id: str,
    override_voice: Optional[str],
) -> types.SpeakerVoiceConfig:
    voice = (
        _default_speaker_voice_map().get(speaker_id)
        or override_voice
        or DEFAULT_VOICE_NAME
    )
    return types.SpeakerVoiceConfig(
        speaker=speaker_id,
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
        ),
    )


def _rewrite_voice_tags(ssml: str) -> str:
    """Convert <voice name="speaker"> blocks into ``speaker:`` prefixes.

    Gemini's multi-speaker preview model expects segments to be prefixed with the
    speaker identifier (e.g. ``speaker_a:``). This helper rewrites Web Speech API
    ``<voice>`` tags into that convention, removes standalone ``<break>`` elements
    or comments that sit between voice blocks, and preserves any markup inside each
    block.
    """

    ssml = _strip_interstitial_tags(ssml)

    def _replace(match: re.Match[str]) -> str:
        speaker = match.group("speaker").strip()
        body = match.group("body").strip()
        if body:
            return f"{speaker}:\n{body}\n"
        return f"{speaker}:\n"

    return VOICE_BLOCK_RE.sub(_replace, ssml)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert SSML into an MP3 file using Gemini 2.5 preview TTS.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--ssml",
        help="Raw SSML string to synthesize. Use quotes to preserve markup.",
    )
    source_group.add_argument(
        "--ssml-file",
        type=Path,
        help="Path to a file containing SSML to synthesize.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination path for the generated MP3 file.",
    )
    parser.add_argument(
        "--voice",
        help=(
            "Optional Gemini prebuilt voice name. Defaults to"
            f" {DEFAULT_VOICE_NAME}."
        ),
    )
    parser.add_argument(
        "--api-key",
        help="Override the Gemini API key. If omitted, environment variables are used.",
    )
    parser.add_argument(
        "--project",
        help="Google Cloud project ID for Vertex AI requests.",
    )
    parser.add_argument(
        "--location",
        help=(
            "Vertex AI region for requests. Defaults to"
            f" {DEFAULT_VERTEX_LOCATION} when using Vertex AI."
        ),
    )
    parser.add_argument(
        "--vertex",
        action="store_true",
        help="Force using Vertex AI endpoints (requires Google Cloud authentication).",
    )
    parser.add_argument(
        "--no-vertex",
        action="store_true",
        help="Force using the Gemini Developer API (API key).",
    )
    return parser.parse_args()


def _load_ssml_from_args(args: argparse.Namespace) -> str:
    if args.ssml:
        return args.ssml
    assert args.ssml_file is not None  # for type checkers
    return args.ssml_file.read_text(encoding="utf-8")


def main() -> None:
    args = _parse_args()
    ssml = _load_ssml_from_args(args)

    if args.vertex and args.no_vertex:
        raise SystemExit("--vertex and --no-vertex cannot be used together.")

    use_vertex: Optional[bool] = None
    if args.vertex:
        use_vertex = True
    elif args.no_vertex:
        use_vertex = False

    client = GeminiTTSClient(
        api_key=args.api_key,
        project=args.project,
        location=args.location,
        use_vertex=use_vertex,
    )
    output_path = client.synthesize_ssml_to_mp3(
        ssml=ssml,
        output_path=args.output,
        voice_name=args.voice,
    )
    print(f"MP3 saved to: {output_path}")


if __name__ == "__main__":
    main()
