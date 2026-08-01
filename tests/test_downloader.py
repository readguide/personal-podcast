import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from personal_podcast.downloader import build_downie_url
from personal_podcast.config import DownloadConfig
from personal_podcast.downloader import DownloadManager, DownloadResult
from personal_podcast.errors import DownloadError
from personal_podcast.identifiers import canonicalize_url, episode_id_for
from personal_podcast.models import EpisodeMetadata


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
        self.assertEqual(values["postprocessing"], ["audio"])

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
