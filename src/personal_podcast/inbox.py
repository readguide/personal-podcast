import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

from personal_podcast.commands import executable_exists, run_checked
from personal_podcast.config import DownloadConfig
from personal_podcast.errors import DownloadError, PersonalPodcastError
from personal_podcast.identifiers import canonicalize_url, source_identity_for


LOGGER = logging.getLogger(__name__)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）】}"


def latest_link(path: Path) -> str:
    if not path.exists():
        raise PersonalPodcastError(f"链接收件箱不存在: {path}")
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        matches = URL_PATTERN.findall(line)
        if matches:
            return canonicalize_url(matches[-1].rstrip(TRAILING_PUNCTUATION))
    raise PersonalPodcastError(f"链接收件箱中没有可用链接: {path}")


def _known_video_url(url: str) -> bool:
    if source_identity_for(url):
        return True
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.lower()
    if host in {"b23.tv", "v.douyin.com"}:
        return True
    if host.endswith("bilibili.com"):
        return path.startswith("/video/") or path.startswith("/bangumi/play/")
    if host.endswith("douyin.com"):
        return path.startswith("/video/")
    if host in {"vimeo.com", "player.vimeo.com"}:
        return bool(re.search(r"/\d+", path))
    return False


class VideoLinkClassifier:
    def __init__(self, config: DownloadConfig):
        self.config = config

    def is_video(self, url: str) -> bool:
        canonical = canonicalize_url(url)
        if _known_video_url(canonical):
            return True
        if not executable_exists(self.config.yt_dlp_command):
            return False
        try:
            result = run_checked(
                [
                    self.config.yt_dlp_command,
                    "--dump-single-json",
                    "--skip-download",
                    "--no-playlist",
                    "--no-warnings",
                    canonical,
                ],
                error_type=DownloadError,
            )
            payload = json.loads(result.stdout)
        except (DownloadError, json.JSONDecodeError, TypeError) as error:
            LOGGER.info("链接未检测到可下载视频，按文章跳过: %s", error)
            return False
        return _payload_has_media(payload)


def _payload_has_media(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("duration") is not None or payload.get("is_live") is True:
        return True
    formats = payload.get("formats")
    if isinstance(formats, list) and any(isinstance(item, dict) for item in formats):
        return True
    entries = payload.get("entries")
    if isinstance(entries, list):
        return any(_payload_has_media(entry) for entry in entries)
    return False
