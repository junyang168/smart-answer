"""Per-service YouTube playback links; never fetch video or infer lyrics."""
import re
from urllib.parse import urlsplit, parse_qs

VIDEO_FIELDS = {"hymn": "hymnYoutubeUrl", "responseHymn": "responseHymnYoutubeUrl"}


def youtube_url(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("YouTube 連結需為文字")
    value = value.strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("請提供有效的 HTTPS YouTube 影片連結")
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        video = parsed.path.removeprefix("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            ids = parse_qs(parsed.query).get("v", [])
            video = ids[0] if len(ids) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:embed|shorts|live)/([\w-]{11})", parsed.path)
            video = match[1] if match else ""
    else:
        video = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise ValueError("請提供有效的 YouTube 影片連結")
    return value  # retain original video, time offset and query parameters
