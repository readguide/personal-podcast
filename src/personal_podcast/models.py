from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class EpisodeMetadata:
    title: str
    description: str = ""
    author: str = ""
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    canonical_url: Optional[str] = None
    thumbnail_url: Optional[str] = None


@dataclass(frozen=True)
class MediaInfo:
    codec_name: str
    duration_seconds: float
    bit_rate: Optional[int]
    sample_rate: Optional[int]
    channels: Optional[int]
    has_video: bool
    format_name: str
    tags: Dict[str, str]


@dataclass(frozen=True)
class AudioResult:
    path: Path
    mime_type: str
    action: str


@dataclass
class Episode:
    episode_id: str
    source_url: str
    title: str
    description: str
    author: str
    imported_at: datetime
    duration_seconds: float
    audio_path: Path
    audio_bytes: int
    audio_mime: str
    source_path: Optional[Path] = None
    source_cleanup_after: Optional[datetime] = None
    artwork_path: Optional[Path] = None
    transcript_path: Optional[Path] = None
    public_audio_url: Optional[str] = None
    release_tag: Optional[str] = None
    archived_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    source_cleaned_at: Optional[datetime] = None

    @property
    def is_visible(self) -> bool:
        return (
            self.public_audio_url is not None
            and self.archived_at is None
            and self.deleted_at is None
        )


def metadata_from_mapping(data: Dict[str, Any], fallback_title: str) -> EpisodeMetadata:
    uploader = data.get("uploader") or data.get("channel") or data.get("creator") or ""
    return EpisodeMetadata(
        title=str(data.get("title") or fallback_title),
        description=str(data.get("description") or ""),
        author=str(uploader),
        source_id=str(data["id"]) if data.get("id") is not None else None,
        source_name=str(data["extractor_key"] or data["extractor"])
        if data.get("extractor_key") or data.get("extractor")
        else None,
        canonical_url=str(data.get("webpage_url") or "") or None,
        thumbnail_url=str(data.get("thumbnail") or "") or None,
    )
