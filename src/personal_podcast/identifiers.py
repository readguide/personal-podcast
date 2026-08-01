import hashlib
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from personal_podcast.models import EpisodeMetadata


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("链接必须是完整的 http 或 https 地址")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized[:48] or fallback


def episode_id_for(url: str, metadata: Optional[EpisodeMetadata] = None) -> str:
    host = (urlsplit(url).hostname or "source").removeprefix("www.")
    source = _slug(metadata.source_name if metadata and metadata.source_name else host, "source")
    if metadata and metadata.source_id:
        identity = _slug(metadata.source_id, "item")
        return f"{source}-{identity}"[:96]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{source}-{digest}"[:96]
