import json
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote, urlencode

from personal_podcast.commands import executable_exists, run_checked
from personal_podcast.config import DownloadConfig
from personal_podcast.errors import DownloadError
from personal_podcast.identifiers import inferred_metadata_for_url
from personal_podcast.models import EpisodeMetadata, metadata_from_mapping


LOGGER = logging.getLogger(__name__)
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
POPUPS_SCRIPT = ASSETS_DIR / "downie-click-popups.applescript"
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


def _top_level_snapshot(destination: Path) -> Dict[Path, Tuple[int, int]]:
    if not destination.exists():
        return {}
    return {
        path.resolve(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in destination.iterdir()
        if path.is_file()
        and path.suffix.lower() in MEDIA_EXTENSIONS
        and not path.name.startswith(".")
    }


def _changed_paths(
    current: Dict[Path, Tuple[int, int]], before: Dict[Path, Tuple[int, int]]
) -> Set[Path]:
    return {
        path
        for path, signature in current.items()
        if path not in before or signature != before[path]
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
        fallback = self.config.downie_fallback_directory.expanduser().resolve()
        fallback_before = (
            _top_level_snapshot(fallback)
            if fallback != destination.resolve()
            else {}
        )
        custom_url = build_downie_url(source_url, destination, title)
        run_checked(
            ["open", "-a", self.config.downie_app, custom_url],
            timeout=30,
            error_type=DownloadError,
        )
        # 兼容有无弹窗: 后台线程持续处理系统/Downie 弹窗, 直到下载完成/超时
        stop_event = threading.Event()
        popup_thread = threading.Thread(
            target=self._handle_popups,
            args=(stop_event, destination),
            daemon=True,
        )
        popup_thread.start()
        try:
            path = self._wait_for_download(
                destination, before, fallback, fallback_before
            )
            return DownloadResult(path=path, downloader="downie")
        finally:
            stop_event.set()
            popup_thread.join(timeout=3)

    def _handle_popups(self, stop_event: threading.Event, destination: Path) -> None:
        """后台线程: 每 2 秒轮询处理系统/Downie 弹窗(兼容无弹窗/多弹窗)。

        弹窗判断: 系统「未设定打开 url 的应用程序」→点取消;
        Downie「已下载过,重新下载?」→ 有源文件点跳过(reuse)/无源文件点下载(redownload);
        播放视频弹窗 →点完成。无弹窗时脚本静默返回,不影响下载。
        """
        if not POPUPS_SCRIPT.exists():
            LOGGER.warning("弹窗处理脚本缺失: %s", POPUPS_SCRIPT)
            return
        has_source = bool(
            destination
            and destination.exists()
            and any(
                p.is_file()
                and p.suffix.lower() in MEDIA_EXTENSIONS
                and not p.name.startswith(".")
                for p in destination.iterdir()
            )
        )
        mode = "reuse" if has_source else "redownload"
        while not stop_event.is_set():
            try:
                subprocess.run(
                    ["osascript", str(POPUPS_SCRIPT), mode],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            except Exception:  # 弹窗处理失败不影响下载主流程
                pass
            stop_event.wait(2)

    def _wait_for_download(
        self,
        destination: Path,
        before: Dict[Path, Tuple[int, int]],
        fallback: Optional[Path] = None,
        fallback_before: Optional[Dict[Path, Tuple[int, int]]] = None,
    ) -> Path:
        deadline = time.monotonic() + self.config.downie_timeout_seconds
        stability: Dict[Path, Tuple[int, int]] = {}
        while time.monotonic() < deadline:
            candidates = _changed_paths(_snapshot(destination), before)
            partials = [
                path
                for path in destination.rglob("*")
                if path.suffix.lower() in PARTIAL_EXTENSIONS
            ]
            ready = self._stable_candidate(candidates, partials, stability)
            if ready:
                return ready

            if fallback and fallback != destination.resolve():
                fallback_candidates = _changed_paths(
                    _top_level_snapshot(fallback), fallback_before or {}
                )
                fallback_partials = [
                    path
                    for path in fallback.iterdir()
                    if path.suffix.lower() in PARTIAL_EXTENSIONS
                ] if fallback.exists() else []
                ready = self._stable_candidate(
                    fallback_candidates, fallback_partials, stability
                )
                if ready:
                    target = destination / ready.name
                    if target.exists():
                        raise DownloadError(f"指定目录已有同名文件: {target}")
                    shutil.move(str(ready), str(target))
                    LOGGER.warning(
                        "%s 忽略了单次目标目录，已将本次下载移入 %s",
                        self.config.downie_app,
                        destination,
                    )
                    return target.resolve()
            time.sleep(self.config.downie_poll_seconds)
        raise DownloadError(
            f"{self.config.downie_app} 在 {self.config.downie_timeout_seconds} 秒内未产生完整音频"
        )

    @staticmethod
    def _stable_candidate(
        candidates: Set[Path],
        partials: List[Path],
        stability: Dict[Path, Tuple[int, int]],
    ) -> Optional[Path]:
        if len(candidates) != 1 or partials:
            return None
        path = next(iter(candidates))
        size = path.stat().st_size
        previous_size, stable_count = stability.get(path, (-1, 0))
        stability[path] = (size, stable_count + 1 if size == previous_size else 0)
        return path if size > 0 and stability[path][1] >= 2 else None


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
            return inferred_metadata_for_url(source_url)
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
            return inferred_metadata_for_url(source_url)


class DownloadManager:
    def __init__(self, config: DownloadConfig):
        self.config = config
        self.downloaders = {
            "downie": DownieDownloader(config),
            "yt-dlp": YtDlpDownloader(config),
        }

    def _existing_source(self, destination: Path) -> Optional[Path]:
        """目标目录已有媒体文件时直接复用, 不触发重新下载。"""
        if not destination.exists():
            return None
        candidates = [
            path
            for path in destination.iterdir()
            if path.is_file()
            and path.suffix.lower() in MEDIA_EXTENSIONS
            and not path.name.startswith(".")
        ]
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
        existing = self._existing_source(destination)
        if existing is not None:
            LOGGER.info("目标目录已有源文件 %s, 跳过下载", existing.name)
            return DownloadResult(path=existing, downloader="existing")
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
