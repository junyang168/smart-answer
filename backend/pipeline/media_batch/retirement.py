"""Retired generators remain readable, but production launch paths reject them."""
PIPELINE='gemini-transcript-v1'

class RetiredPipeline(ValueError):
    pass


def require_supported(config):
    if config.get('mode')=='inventory' or config.get('pipeline')==PIPELINE:
        return
    raise RetiredPipeline('舊 GPT／Processor 轉錄引擎已退役。原粗稿與台帳保留；請使用 media_batch plan --pipeline gemini-transcript-v1。')


def require_cli_retired():
    raise RetiredPipeline('此歷史生成入口已退役；請使用 python -m backend.pipeline.media_batch plan。歷史產物不會刪除。')
