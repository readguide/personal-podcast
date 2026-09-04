import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

from personal_podcast.models import Episode


@dataclass(frozen=True)
class ChannelVideo:
    message_id: int
    title: str
    author: str
    platform: str
    source_url: str
    telegram_url: str
    published_at: datetime
    duration_seconds: float = 0
    thumbnail_path: Optional[Path] = None


@dataclass
class LibraryItem:
    key: str
    title: str
    author: str
    platform: str
    source_url: str
    date: datetime
    duration_seconds: float
    in_podcast: bool = False
    in_channel: bool = False
    audio_url: Optional[str] = None
    telegram_url: Optional[str] = None
    thumbnail_path: Optional[Path] = None
    thumbnail_url: Optional[str] = None
    local_video_path: Optional[Path] = None


def normalized_source_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def platform_for(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if "douyin.com" in host:
        return "抖音"
    if "bilibili.com" in host or host == "b23.tv":
        return "B站"
    if "youtube.com" in host or host == "youtu.be":
        return "YouTube"
    if "xiaohongshu.com" in host or host == "xhslink.com":
        return "小红书"
    if "twitter.com" in host or host == "x.com":
        return "X"
    return host.removeprefix("www.") or "视频"


def load_channel_videos(path: Path) -> List[ChannelVideo]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("videos", []) if isinstance(payload, dict) else payload
    videos: List[ChannelVideo] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("source_url"):
            continue
        published = datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        thumb = str(row.get("thumbnail_path") or "")
        videos.append(
            ChannelVideo(
                message_id=int(row["message_id"]),
                title=str(row.get("title") or "未命名视频"),
                author=str(row.get("author") or ""),
                platform=str(row.get("platform") or platform_for(str(row["source_url"]))),
                source_url=str(row["source_url"]),
                telegram_url=str(row.get("telegram_url") or ""),
                published_at=published,
                duration_seconds=float(row.get("duration_seconds") or 0),
                thumbnail_path=Path(thumb) if thumb else None,
            )
        )
    return videos


def merge_library(episodes: Iterable[Episode], channel_videos: Iterable[ChannelVideo]) -> List[LibraryItem]:
    items: Dict[str, LibraryItem] = {}
    for episode in episodes:
        if not episode.is_visible:
            continue
        key = normalized_source_url(episode.source_url)
        items[key] = LibraryItem(
            key=key,
            title=episode.title,
            author=episode.author,
            platform=platform_for(episode.source_url),
            source_url=episode.source_url,
            date=episode.imported_at,
            duration_seconds=episode.duration_seconds,
            in_podcast=True,
            audio_url=episode.public_audio_url,
            thumbnail_path=episode.artwork_path,
            local_video_path=episode.source_path,
        )

    for video in channel_videos:
        key = normalized_source_url(video.source_url)
        item = items.get(key)
        if item is None:
            items[key] = LibraryItem(
                key=key,
                title=video.title,
                author=video.author,
                platform=video.platform,
                source_url=video.source_url,
                date=video.published_at,
                duration_seconds=video.duration_seconds,
                in_channel=True,
                telegram_url=video.telegram_url or None,
                thumbnail_path=video.thumbnail_path,
            )
            continue
        item.in_channel = True
        item.telegram_url = video.telegram_url or item.telegram_url
        item.date = max(item.date, video.published_at)
        if video.title and (not item.title or item.title.endswith("…")):
            item.title = video.title
        if video.author and (not item.author or item.author == "en"):
            item.author = video.author
        if video.platform:
            item.platform = video.platform
        if video.thumbnail_path and video.thumbnail_path.exists():
            item.thumbnail_path = video.thumbnail_path
        if not item.duration_seconds and video.duration_seconds:
            item.duration_seconds = video.duration_seconds

    return sorted(items.values(), key=lambda item: item.date, reverse=True)
