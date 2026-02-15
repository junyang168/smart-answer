"""
LLM Client Abstraction for the Multi-Agent Pipeline.

Dispatches to either Google Gemini or any OpenAI-compatible API (e.g. Kimi K2.5)
based on the MAS_LLM_PROVIDER environment variable.
"""
from __future__ import annotations

import json
from typing import Optional

from backend.api.config import (
    MAS_LLM_PROVIDER,
    OPENAI_COMPATIBLE_API_KEY,
    OPENAI_COMPATIBLE_BASE_URL,
    OPENAI_COMPATIBLE_MODEL,
)


def generate(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> str:
    """
    Unified generation interface for the multi-agent pipeline.
    
    Args:
        system_prompt: The system instruction for the LLM.
        user_prompt: The user message.
        temperature: Sampling temperature.
        json_mode: If True, request JSON output format.
    
    Returns:
        The generated text response.
    """
    if MAS_LLM_PROVIDER == "openai_compatible":
        return _generate_openai_compatible(system_prompt, user_prompt, temperature, json_mode)
    else:
        return _generate_gemini(system_prompt, user_prompt, temperature, json_mode)


def _generate_gemini(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    json_mode: bool,
) -> str:
    """Generate using Google Gemini via the existing gemini_client."""
    from google.genai import types
    from backend.api.gemini_client import gemini_client

    MODEL_ID = "gemini-3-pro-preview"

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=temperature,
        thinking_config=types.ThinkingConfig(thinking_budget=-1),
    )
    if json_mode:
        config.response_mime_type = "application/json"

    response = gemini_client.generate_raw(
        model=MODEL_ID,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])],
        config=config,
    )
    return response.text


def _generate_openai_compatible(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    json_mode: bool,
) -> str:
    """Generate using any OpenAI-compatible API (Kimi K2.5, etc.)."""
    from openai import OpenAI

    if not OPENAI_COMPATIBLE_API_KEY:
        raise ValueError(
            "OPENAI_COMPATIBLE_API_KEY must be set when MAS_LLM_PROVIDER=openai_compatible"
        )

    client = OpenAI(
        api_key=OPENAI_COMPATIBLE_API_KEY,
        base_url=OPENAI_COMPATIBLE_BASE_URL,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    kwargs = {
        "model": OPENAI_COMPATIBLE_MODEL,
        "messages": messages,
        "temperature": temperature,
    }

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content
