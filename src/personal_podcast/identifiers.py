import hashlib
import re
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit, urlunsplit

from personal_podcast.models import EpisodeMetadata


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("链接必须是完整的 http 或 https 地址")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized[:48] or fallback


def source_identity_for(url: str) -> Optional[Tuple[str, str]]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    source_id: Optional[str] = None
    if host == "youtu.be":
        source_id = parts.path.strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "m.youtube.com", "youtube-nocookie.com"}:
        if parts.path == "/watch":
            source_id = parse_qs(parts.query).get("v", [None])[0]
        else:
            segments = [segment for segment in parts.path.split("/") if segment]
            if len(segments) >= 2 and segments[0] in {"embed", "live", "shorts"}:
                source_id = segments[1]
    if source_id and re.fullmatch(r"[A-Za-z0-9_-]{6,32}", source_id):
        return "YouTube", source_id
    return None


def inferred_metadata_for_url(url: str) -> Optional[EpisodeMetadata]:
    identity = source_identity_for(url)
    if not identity:
        return None
    source_name, source_id = identity
    return EpisodeMetadata(
        title="",
        source_id=source_id,
        source_name=source_name,
        canonical_url=url,
        thumbnail_url=f"https://i.ytimg.com/vi/{source_id}/hqdefault.jpg",
    )


def episode_id_for(url: str, metadata: Optional[EpisodeMetadata] = None) -> str:
    host = (urlsplit(url).hostname or "source").removeprefix("www.")
    inferred = source_identity_for(url)
    source_name = metadata.source_name if metadata and metadata.source_name else None
    source_id = metadata.source_id if metadata and metadata.source_id else None
    if inferred and not source_id:
        source_name, source_id = inferred
    source = _slug(source_name or host, "source")
    if source_id:
        identity = _slug(source_id, "item")
        return f"{source}-{identity}"[:96]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{source}-{digest}"[:96]
