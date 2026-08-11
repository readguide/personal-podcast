import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from personal_podcast.downloader import (
    DownieDownloader,
    POPUPS_SCRIPT,
    build_downie_url,
)
from personal_podcast.config import DownloadConfig
from personal_podcast.downloader import DownloadManager, DownloadResult
from personal_podcast.errors import DownloadError
from personal_podcast.identifiers import canonicalize_url, episode_id_for
from personal_podcast.models import EpisodeMetadata


class PopupHandlingTests(unittest.TestCase):
    def test_popup_script_ships_with_package(self) -> None:
        self.assertTrue(POPUPS_SCRIPT.exists(), f"缺少弹窗处理脚本: {POPUPS_SCRIPT}")
        content = POPUPS_SCRIPT.read_text(encoding="utf-8")
        # 必须同时覆盖系统弹窗(取消)和 Downie 播放弹窗(完成), 兼容无弹窗静默
        self.assertIn("CoreServicesUIAgent", content)
        self.assertIn("完成", content)
        self.assertIn("无弹窗", content)

    def test_popup_thread_stops_on_event(self) -> None:
        import threading
        import tempfile
        from pathlib import Path

        downloader = DownieDownloader(DownloadConfig())
        with tempfile.TemporaryDirectory() as temporary:
            stop_event = threading.Event()
            stop_event.set()  # 预置停止 -> 线程应立即退出
            thread = threading.Thread(
                target=downloader._handle_popups,
                args=(stop_event, Path(temporary)),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "弹窗线程在停止事件后未退出")


class DownloaderTests(unittest.TestCase):
    def test_downie_url_encodes_source_and_destination(self) -> None:
        source = "https://example.com/watch?v=one&list=two"
        destination = Path("/tmp/Personal Podcast/Source Media")
        custom_url = build_downie_url(source, destination, "episode-one")
        parsed = urlsplit(custom_url)
        values = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "downie")
        self.assertEqual(parsed.netloc, "XUOpenURL")
        self.assertEqual(values["url"], [source])
        self.assertEqual(values["destination"], [str(destination)])
        # Downie 下载原始媒体（视频/音频），不请求下载后转音频；
        # 音频转换由 ffmpeg 完成，原始文件保留在 Source Media。
        self.assertNotIn("postprocessing", values)

    def test_episode_identifier_uses_source_metadata(self) -> None:
        metadata = EpisodeMetadata(
            title="Episode",
            source_name="YouTube",
            source_id="abc_123",
        )
        self.assertEqual(
            episode_id_for("https://youtube.com/watch?v=abc_123", metadata),
            "youtube-abc-123",
        )

    def test_youtube_identifier_does_not_require_remote_metadata(self) -> None:
        self.assertEqual(
            episode_id_for("https://www.youtube.com/watch?v=OcKl98ZQbMQ"),
            "youtube-ockl98zqbmq",
        )

    def test_downie_moves_only_new_fallback_file_to_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "source"
            fallback = root / "downloads"
            destination.mkdir()
            fallback.mkdir()
            existing = fallback / "existing.m4a"
            existing.write_bytes(b"existing")
            before = {existing.resolve(): (existing.stat().st_size, existing.stat().st_mtime_ns)}
            downloaded = fallback / "episode.mkv"
            downloaded.write_bytes(b"download")
            downloader = DownieDownloader(
                DownloadConfig(
                    downie_timeout_seconds=1,
                    downie_poll_seconds=0.01,
                    downie_fallback_directory=fallback,
                )
            )
            result = downloader._wait_for_download(
                destination, {}, fallback, before
            )
            self.assertEqual(result, (destination / downloaded.name).resolve())
            self.assertTrue(existing.exists())
            self.assertFalse(downloaded.exists())

    def test_canonical_url_removes_fragment(self) -> None:
        self.assertEqual(
            canonicalize_url(" HTTPS://Example.COM/video?q=1#chapter "),
            "https://example.com/video?q=1",
        )

    def test_manager_falls_back_after_downie_failure(self) -> None:
        class FailingDownloader:
            def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
                raise DownloadError("Downie failed")

        class SuccessfulDownloader:
            def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
                return DownloadResult(destination / "audio.opus", "yt-dlp")

        manager = DownloadManager(DownloadConfig())
        manager.downloaders = {
            "downie": FailingDownloader(),
            "yt-dlp": SuccessfulDownloader(),
        }
        with self.assertLogs("personal_podcast.downloader", level="WARNING"):
            result = manager.download(
                "https://example.com/video", Path("/tmp/source"), "episode"
            )
        self.assertEqual(result.downloader, "yt-dlp")


if __name__ == "__main__":
    unittest.main()
