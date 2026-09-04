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
POPUPS_SCRIPT = ASSETS_DIR / "downie-click-popups-loop.applescript"
if not POPUPS_SCRIPT.exists():
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

    def _close_downie_windows(self) -> None:
        """收尾: 只关闭 Downie 窗口, 不退出应用 (2026-08-10 用户确认, 2026-08-11 内建)。"""
        try:
            subprocess.run(
                ["osascript", "-e", f'tell application "{self.config.downie_app}" to close every window'],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception:
            pass

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
        # 链接给 Downie 后立即同步处理一次弹窗(不等循环, 尽快点掉播放窗口避免出声)
        try:
            time.sleep(0.5)
            if POPUPS_SCRIPT.exists():
                subprocess.run(
                    ["osascript", str(POPUPS_SCRIPT), "reuse", "8"],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
        except Exception:
            pass
        # 兼容有无弹窗: 后台常驻循环持续处理系统/Downie 弹窗, 直到下载完成/超时
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
            # 收尾: 只关窗口不退出应用 (2026-08-11 内建, 不再依赖手动 osascript)
            self._close_downie_windows()

    def _handle_popups(self, stop_event: threading.Event, destination: Path) -> None:
        """后台线程: 启动常驻 osascript 循环, 每 0.2s 扫描处理弹窗(兼容无弹窗/多弹窗)。

        弹窗判断: 系统「未设定打开 url 的应用程序」→点取消;
        Downie「已下载过,重新下载?」→ 有源文件点跳过(reuse)/无源文件点下载(redownload);
        播放视频弹窗 →点完成。无弹窗时静默跳过。
        下载完成/超时后由调用方置位 stop_event, 此处 terminate 常驻进程。
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
        proc = subprocess.Popen(
            ["osascript", str(POPUPS_SCRIPT), mode, "1800"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while not stop_event.wait(1):
                if proc.poll() is not None:
                    return
        finally:
            if proc.poll() is None:
                proc.terminate()

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


class DouyinApiDownloader:
    """抖音官方分享页 API 直链下载(2026-08-11 新增, Downie/yt-dlp 失败后的兜底)。

    背景: Downie 用桌面版 douyin.com 解析, 部分抖音视频页面无 play_addr 导致无法下载;
    但移动分享页 iesdouyin.com/share/video/<id> 的 ROUTER_DATA 里有无水印直链。
    """

    def __init__(self, config: DownloadConfig):
        self.config = config

    def is_available(self) -> bool:
        return executable_exists("curl")

    @staticmethod
    def _video_id(source_url: str) -> Optional[str]:
        import re as _re
        m = _re.search(r"(?:douyin|iesdouyin)\.com/video/(\d{15,20})", source_url)
        if m:
            return m.group(1)
        if "v.douyin.com" in source_url:
            try:
                result = run_checked(
                    [
                        "curl", "-s", "-L", "-m", "20",
                        "-o", "/dev/null", "-w", "%{url_effective}",
                        source_url,
                    ],
                    error_type=DownloadError,
                )
                final_url = result.stdout.strip()
                m = _re.search(r"/video/(\d{15,20})", final_url)
                if m:
                    return m.group(1)
            except DownloadError:
                return None
        return None

    def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
        import re as _re
        import json as _json

        video_id = self._video_id(source_url)
        if not video_id:
            raise DownloadError("无法从链接提取抖音视频 ID")
        share_url = f"https://www.iesdouyin.com/share/video/{video_id}"
        try:
            result = run_checked(
                [
                    "curl", "-s", "-L", "-m", "30",
                    "-A", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    share_url,
                ],
                error_type=DownloadError,
            )
        except DownloadError as error:
            raise DownloadError(f"抖音分享页抓取失败: {error}") from error
        html = result.stdout
        m = _re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\});?\s*</script>", html, _re.S)
        if not m:
            raise DownloadError("抖音分享页无 ROUTER_DATA(视频可能被删/私密/风控)")
        try:
            data = _json.loads(m.group(1))
        except _json.JSONDecodeError as error:
            raise DownloadError(f"抖音 ROUTER_DATA 解析失败: {error}") from error

        def _find_play_addr(obj, depth=0):
            found = []
            if depth > 8 or not isinstance(obj, (dict, list)):
                return found
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == "play_addr":
                        found.append(value)
                    found.extend(_find_play_addr(value, depth + 1))
            else:
                for item in obj:
                    found.extend(_find_play_addr(item, depth + 1))
            return found

        video_url = None
        for addr in _find_play_addr(data):
            if isinstance(addr, dict):
                urls = addr.get("url_list") or []
                if urls:
                    video_url = urls[0]
                    break
        if not video_url:
            raise DownloadError("抖音分享页无播放直链")
        destination.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(c for c in title if c not in "/\\:*?\"<>| ").strip() or "douyin"
        target = destination / f"{safe_title[:80]}.mp4"
        run_checked(
            [
                "curl", "-s", "-L", "-m", "600",
                "-A", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                "-o", str(target),
                video_url,
            ],
            error_type=DownloadError,
        )
        if not target.exists() or target.stat().st_size < 10000:
            raise DownloadError(f"抖音直链下载失败(文件过小或不存在): {target}")
        return DownloadResult(path=target.resolve(), downloader="douyin-api")


class MetadataReader:
    """读取链接元数据(标题/作者)。

    2026-08-12 修正: 下载始终用 Downie(用户定版), 但元数据读取仍用 yt-dlp
    (仅 --dump-single-json --skip-download, 不触发下载), 否则 YouTube 等
    平台拿不到标题/UP主名称, 会回退成文件名字符串(ID)。
    """

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
            "douyin-api": DouyinApiDownloader(config),
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
