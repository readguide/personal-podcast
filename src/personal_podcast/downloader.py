import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote, urlencode

from personal_podcast.commands import executable_exists, run_checked
from personal_podcast.config import DownloadConfig
from personal_podcast.errors import DownloadError
from personal_podcast.models import EpisodeMetadata, metadata_from_mapping


LOGGER = logging.getLogger(__name__)
MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mka",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
PARTIAL_EXTENSIONS = {".downiepart", ".part", ".ytdl"}


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    downloader: str


def build_downie_url(source_url: str, destination: Path, title: str) -> str:
    query = urlencode(
        {
            "url": source_url,
            "postprocessing": "audio",
            "destination": str(destination),
            "title": title,
        },
        quote_via=quote,
    )
    return f"downie://XUOpenURL?{query}"


def _media_files(destination: Path) -> Set[Path]:
    return {
        path.resolve()
        for path in destination.rglob("*")
        if path.is_file()
        and path.suffix.lower() in MEDIA_EXTENSIONS
        and path.suffix.lower() not in PARTIAL_EXTENSIONS
        and not path.name.startswith(".")
    }


def _snapshot(destination: Path) -> Dict[Path, Tuple[int, int]]:
    return {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in _media_files(destination)
    }


def _newest(paths: Iterable[Path]) -> Optional[Path]:
    candidates = list(paths)
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


class DownieDownloader:
    def __init__(self, config: DownloadConfig):
        self.config = config

    def is_available(self) -> bool:
        app_name = self.config.downie_app
        return any(
            candidate.exists()
            for candidate in (
                Path("/Applications") / f"{app_name}.app",
                Path.home() / "Applications" / f"{app_name}.app",
            )
        ) and executable_exists("open")

    def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
        if not self.is_available():
            raise DownloadError(f"未找到 {self.config.downie_app}")
        destination.mkdir(parents=True, exist_ok=True)
        before = _snapshot(destination)
        custom_url = build_downie_url(source_url, destination, title)
        run_checked(
            ["open", "-a", self.config.downie_app, custom_url],
            timeout=30,
            error_type=DownloadError,
        )
        path = self._wait_for_download(destination, before)
        return DownloadResult(path=path, downloader="downie")

    def _wait_for_download(
        self, destination: Path, before: Dict[Path, Tuple[int, int]]
    ) -> Path:
        deadline = time.monotonic() + self.config.downie_timeout_seconds
        stability: Dict[Path, Tuple[int, int]] = {}
        while time.monotonic() < deadline:
            candidates = {
                path
                for path in _media_files(destination)
                if path not in before
                or (path.stat().st_size, path.stat().st_mtime_ns) != before[path]
            }
            partials = [
                path
                for path in destination.rglob("*")
                if path.is_file() and path.suffix.lower() in PARTIAL_EXTENSIONS
            ]
            for path in candidates:
                size = path.stat().st_size
                previous_size, stable_count = stability.get(path, (-1, 0))
                stability[path] = (size, stable_count + 1 if size == previous_size else 0)
                if size > 0 and stability[path][1] >= 2 and not partials:
                    return path
            time.sleep(self.config.downie_poll_seconds)
        raise DownloadError(
            f"{self.config.downie_app} 在 {self.config.downie_timeout_seconds} 秒内未产生完整音频"
        )


class YtDlpDownloader:
    def __init__(self, config: DownloadConfig):
        self.config = config

    def is_available(self) -> bool:
        return executable_exists(self.config.yt_dlp_command)

    def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
        if not self.is_available():
            raise DownloadError(f"未找到备用下载器 {self.config.yt_dlp_command}")
        destination.mkdir(parents=True, exist_ok=True)
        before = _media_files(destination)
        output_template = str(destination / "%(title).180B [%(id)s].%(ext)s")
        result = run_checked(
            [
                self.config.yt_dlp_command,
                "--no-playlist",
                "--no-warnings",
                "--no-part",
                "--quiet",
                "--format",
                "bestaudio/best",
                "--print",
                "after_move:filepath",
                "--output",
                output_template,
                source_url,
            ],
            error_type=DownloadError,
        )
        for line in reversed(result.stdout.splitlines()):
            candidate = Path(line.strip())
            if candidate.exists() and candidate.is_file():
                return DownloadResult(path=candidate.resolve(), downloader="yt-dlp")
        candidate = _newest(_media_files(destination) - before)
        if candidate is None:
            raise DownloadError("yt-dlp 已结束，但没有找到下载文件")
        return DownloadResult(path=candidate, downloader="yt-dlp")


class MetadataReader:
    def __init__(self, config: DownloadConfig):
        self.config = config

    def read(self, source_url: str) -> Optional[EpisodeMetadata]:
        if not executable_exists(self.config.yt_dlp_command):
            return None
        try:
            result = run_checked(
                [
                    self.config.yt_dlp_command,
                    "--dump-single-json",
                    "--skip-download",
                    "--no-playlist",
                    "--no-warnings",
                    source_url,
                ],
                error_type=DownloadError,
            )
            payload = json.loads(result.stdout)
            return metadata_from_mapping(payload, fallback_title="未命名节目")
        except (DownloadError, json.JSONDecodeError, TypeError) as error:
            LOGGER.warning("无法读取链接元数据，将使用下载文件信息: %s", error)
            return None


class DownloadManager:
    def __init__(self, config: DownloadConfig):
        self.config = config
        self.downloaders = {
            "downie": DownieDownloader(config),
            "yt-dlp": YtDlpDownloader(config),
        }

    def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
        failures: List[str] = []
        for name in self.config.order:
            downloader = self.downloaders[name]
            try:
                LOGGER.info("尝试使用 %s 下载", name)
                return downloader.download(source_url, destination, title)
            except DownloadError as error:
                failures.append(f"{name}: {error}")
                LOGGER.warning("下载器 %s 失败: %s", name, error)
        raise DownloadError("所有下载方式均失败；" + "；".join(failures))
