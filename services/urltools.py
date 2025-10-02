from urllib.parse import urlparse, parse_qs
from enum import Enum

Y_DOMAINS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "music.youtube.com"
}

class UrlKind(Enum):
    VIDEO = "video"
    SHORT = "short"
    OTHER = "other"

class InvalidYouTubeUrl(ValueError):
    pass

def _clean_watch(qs):
    v = (qs.get("v") or [None])[0]
    if not v:
        raise InvalidYouTubeUrl("Invalid YouTube watch URL (missing video id).")
    return f"https://www.youtube.com/watch?v={v}", UrlKind.VIDEO

def _clean_shorts(path):
    parts = [p for p in path.split("/") if p]
    try:
        idx = parts.index("shorts")
    except ValueError:
        raise InvalidYouTubeUrl("Invalid YouTube shorts URL.")
    if len(parts) <= idx + 1:
        raise InvalidYouTubeUrl("Invalid YouTube shorts URL (missing id).")
    vid = parts[idx + 1]
    return f"https://www.youtube.com/shorts/{vid}", UrlKind.SHORT

def sanitize_youtube_url(raw: str):
    try:
        u = urlparse(raw)
    except Exception:
        raise InvalidYouTubeUrl("Invalid URL.")
    if not u.scheme or not u.netloc:
        raise InvalidYouTubeUrl("Please enter a valid URL (including https://).")

    host = u.netloc.lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}:
        raise InvalidYouTubeUrl("Only YouTube URLs are supported.")

    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
        if not vid:
            raise InvalidYouTubeUrl("Invalid youtu.be URL (missing id).")
        return f"https://www.youtube.com/watch?v={vid}", UrlKind.VIDEO

    path = u.path or "/"
    qs = parse_qs(u.query or "")

    if path.startswith("/watch"):
        return _clean_watch(qs)

    if "/shorts/" in path:
        return _clean_shorts(path)

    if host == "music.youtube.com" and path.startswith("/watch"):
        return _clean_watch(qs)

    raise InvalidYouTubeUrl("Only direct YouTube video or shorts URLs are supported.")
