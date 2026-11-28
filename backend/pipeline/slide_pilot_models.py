"""Bounded vision adapters for the local slide pilot; no automatic fallback."""
from __future__ import annotations

import base64
import os
import json
from pathlib import Path


class VisionReviewer:
    def __init__(self, provider: str, prompt: str, schema: dict):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        self.provider, self.prompt = provider, prompt
        self.schema=schema
        if provider == "openai":
            # This media pipeline uses the owner's ChatGPT subscription only.
            # No API-key fallback, including when subscription access fails.
            self.client = None
            self.model = "gpt-6.1-sol"
            self.config = {"reasoning": {"effort": "low"}, "max_output_tokens": 8192,
                           "transport":"codex_subscription",
                           "store": False, "service_tier": "default",
                           "text": {"format": {"type": "json_schema", "name": "slide_pair_review",
                                                "strict": True, "schema": schema}}}
            # Conservatively include published cache-write premium, claim no
            # cached input discount. output_tokens already includes reasoning.
            self.input_rate, self.output_rate = 2.5, 10.0
            self.price_source = "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
        elif provider == "gemini":
            from google import genai
            from google.genai import types
            key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY1"))
            self.client = (genai.Client(vertexai=False, api_key=key,
                http_options=types.HttpOptions(timeout=90000, retry_options=types.HttpRetryOptions(attempts=1))) if key else None)
            self.model = "gemini-3.8-flash"
            self.config = {"temperature": 0, "max_output_tokens": 8192,
                           "transport":"agy_subscription_preferred_audio_api",
                           "response_mime_type": "application/json", "response_json_schema": schema}
            self.input_rate, self.output_rate = .75, 3.75
            self.price_source = "https://ai.google.dev/gemini-api/docs/pricing"
        else:
            raise ValueError("Provider must be openai or gemini")

    def contents(self, ids: list[str], paths: list[Path]):
        import json
        import mimetypes
        mime_types = [mimetypes.guess_type(p.name)[0] for p in paths]
        if any(mime not in ('image/jpeg', 'image/png') for mime in mime_types):
            raise ValueError('Vision pilot accepts only JPEG or PNG images')
        if self.provider == "openai":
            parts = [{"type": "input_text", "text": "Candidate IDs: " + json.dumps(ids)}]
            parts += [{"type": "input_image", "detail": "high", "image_url":
                f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()} for p, mime in zip(paths, mime_types)]
            return [{"role": "user", "content": parts}]
        from google.genai import types
        return [types.Part.from_text(text=self.prompt + '\nCandidate IDs: ' + json.dumps(ids))] + [
            types.Part.from_bytes(data=p.read_bytes(), mime_type=mime) for p, mime in zip(paths, mime_types)]

    def count(self, contents) -> int:
        if self.provider == "openai":
            raise ValueError('Subscription transport has no provider token-count API; use bounded source packets')
        # Conservative local admission bound; never call an API to count a
        # subscription request. This is not reported as measured provider usage.
        return sum(len((getattr(p,'text',None) or '').encode())+
                   (4096 if getattr(p,'inline_data',None) else 0) for p in contents)

    def generate(self, contents) -> dict:
        if self.provider == "openai":
            from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
            from backend.pipeline.media_model_calls import call_artifacts
            folder=call_artifacts()
            if folder is None:raise ValueError('Subscription media calls require a durable saved_call')
            client=CodexSubscriptionClient(model=self.model,reasoning_effort=self.config['reasoning']['effort'],
                max_output_tokens=self.config['max_output_tokens'],timeout_seconds=900)
            client.call_artifacts=folder/'subscription'
            client.capture_usage_events=True
            texts=[];images=[]
            for message in contents:
                content=message['content']
                if isinstance(content,str):texts.append(content);continue
                for part in content:
                    if part['type']=='input_text':texts.append(part['text'])
                    elif part['type']=='input_image':
                        header,encoded=part['image_url'].split(',',1)
                        if header not in ('data:image/png;base64','data:image/jpeg;base64'):
                            raise ValueError('Subscription vision requires bound image bytes')
                        import hashlib
                        data=base64.b64decode(encoded,validate=True)
                        path=folder/(hashlib.sha256(data).hexdigest()+('.png' if 'png' in header else '.jpg'))
                        if path.exists() and path.read_bytes()!=data:raise ValueError('Image bytes changed')
                        if not path.exists():path.write_bytes(data)
                        images.append(path)
                    else:raise ValueError('Unsupported subscription input part')
            answer=client.generate_json(self.prompt,'\n'.join(texts),self.config['text']['format']['schema'],image_paths=images)
            return {'schema_version':'media-subscription-response.v1','model':self.model,'status':'completed',
                    'transport':'codex_subscription','answer':answer,'subscription_usage':client.last_usage,
                    'cli_identity':client.cli_identity}
        else:
            from google.genai import types
            audio=any(getattr(p,'inline_data',None) and p.inline_data.mime_type.startswith('audio/') for p in contents)
            from backend.pipeline.media_model_calls import call_artifacts
            from backend.pipeline.media_flash_subscription import FlashSubscription
            folder=call_artifacts()
            if folder is None:raise ValueError('Flash subscription requires a durable saved_call')
            parts=[]
            for part in contents:
                if part.text:parts.append({'type':'text','text':part.text})
                elif part.inline_data:
                    blob=part.inline_data;kind=blob.mime_type.split('/')[0]
                    if kind not in ('image','audio'):raise ValueError('Unsupported Flash subscription modality')
                    parts.append({'type':kind,'mimeType':blob.mime_type,'data':base64.b64encode(blob.data).decode()})
                else:raise ValueError('Unsupported Flash subscription content')
            effort=self.config.get('thinking_config',{}).get('thinking_level','MEDIUM').lower()
            if effort not in ('low','medium','high'):raise ValueError('Unsupported Flash subscription thinking level')
            subscription=FlashSubscription(folder/'subscription')
            try:
                subscription.start()
                capabilities=subscription.capabilities
                if not audio or capabilities.get('audio'):
                    return subscription.generate('gemini-3.8-flash-'+effort,parts,self.prompt,self.schema)
            finally:subscription.close()
            if self.client is None:raise ValueError('Audio subscription unsupported and Gemini API credential unavailable')
            response = self.client.models.generate_content(model=self.model, contents=contents,
                config=types.GenerateContentConfig(**{k:v for k,v in self.config.items() if k!='transport'}))
        raw=response.model_dump(mode="json", exclude_none=True)
        raw.update(transport='gemini_api',transport_reason='subscription_audio_capability_false',subscription_capabilities=capabilities)
        return raw

    def text(self, raw: dict) -> str:
        if raw.get('transport') in ('codex_subscription','agy_subscription'):
            if raw.get('status')!='completed':raise ValueError('Incomplete subscription response')
            return json.dumps(raw['answer'],ensure_ascii=False)
        if self.provider == "openai":
            if raw.get("status") != "completed":
                raise ValueError("Incomplete response saved; no automatic retry")
            if raw.get('transport')=='codex_subscription':return json.dumps(raw['answer'],ensure_ascii=False)
            return ''.join(p.get("text", "") for item in raw.get("output", [])
                           if item.get("type") == "message" for p in item.get("content", [])
                           if p.get("type") == "output_text")
        candidates = raw.get("candidates", [])
        if len(candidates) != 1 or candidates[0].get("finish_reason") != "STOP":
            raise ValueError("Incomplete Gemini response saved; no automatic retry")
        parts=candidates[0].get("content", {}).get("parts", [])
        text=''.join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():raise ValueError("Empty completed Gemini response saved; no silent empty answer")
        return text

    def usage(self, raw: dict) -> dict:
        if raw.get('transport')=='agy_subscription':
            return {'input_tokens':None,'output_including_reasoning_tokens':None,'estimated_usd_upper':0.0,
                    'basis':'subscription; no API charge; subscription cost not allocated'}
        if self.provider == "openai":
            if raw.get('transport')=='codex_subscription':
                usage=raw.get('subscription_usage') or {}
                return {'input_tokens':usage.get('input_tokens'),'output_including_reasoning_tokens':usage.get('output_tokens'),
                        'estimated_usd_upper':0.0,'basis':'subscription; no API charge; subscription cost not allocated'}
            usage = raw["usage"]
            inputs, outputs = usage["input_tokens"], usage["output_tokens"]
        else:
            usage = raw["usage_metadata"]
            inputs = usage["prompt_token_count"]
            outputs = (usage.get("candidates_token_count") or 0) + (usage.get("thoughts_token_count") or 0)
        return {"input_tokens": inputs, "output_including_reasoning_tokens": outputs,
                "estimated_usd_upper": (inputs * self.input_rate + outputs * self.output_rate) / 1_000_000}
