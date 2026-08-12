import tempfile
import unittest
from pathlib import Path
from typing import Optional

from personal_podcast.config import AppConfig, GitHubConfig, StorageConfig
from personal_podcast.downloader import DownloadResult
from personal_podcast.feed import validate_feed
from personal_podcast.models import AudioResult, Episode, EpisodeMetadata, MediaInfo
from personal_podcast.service import PersonalPodcastService, build_episode_description


class FakeMetadataReader:
    def read(self, source_url: str) -> Optional[EpisodeMetadata]:
        return EpisodeMetadata(
            title="测试单集",
            description="原始简介",
            author="原作者",
            source_id="123",
            source_name="Example",
            canonical_url=source_url,
        )


class FakeDownloadManager:
    def download(self, source_url: str, destination: Path, title: str) -> DownloadResult:
        destination.mkdir(parents=True, exist_ok=True)
        source = destination / "source.opus"
        source.write_bytes(b"source audio")
        return DownloadResult(source, "fake")


class FakeMediaProcessor:
    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo("opus", 90.0, 64000, 48000, 2, False, "ogg", {})

    def process(
        self,
        source_path: Path,
        output_directory: Path,
        episode_id: str,
        title: str,
        author: str,
        album: str,
        description: str,
        artwork_path: Optional[Path] = None,
    ) -> AudioResult:
        output_directory.mkdir(parents=True, exist_ok=True)
        output = output_directory / f"{episode_id}.m4a"
        output.write_bytes(b"final audio")
        return AudioResult(output, "audio/mp4", "transcode")


class FakeReleasePublisher:
    def __init__(self) -> None:
        self.host = "github.example"

    def publish(self, episode: Episode):
        tag = f"episode-{episode.episode_id}"
        return tag, self.public_url(episode, tag)

    def public_url(self, episode: Episode, tag: str) -> str:
        return f"https://{self.host}/{tag}/{episode.audio_path.name}"


class ServiceTests(unittest.TestCase):
    def test_description_adds_author_and_source_to_original_summary(self) -> None:
        self.assertEqual(
            build_episode_description(
                "原始简介",
                title="测试单集",
                author="原作者",
                source_url="https://example.com/video",
            ),
            "原始简介\n\n作者：原作者\n原始来源：https://example.com/video",
        )

    def test_description_has_readable_fallback_when_metadata_is_missing(self) -> None:
        description = build_episode_description(
            "",
            title="测试单集",
            author="原作者",
            source_url="https://example.com/video",
        )
        self.assertIn("测试单集", description)
        self.assertIn("作者：原作者", description)
        self.assertIn("原始来源：https://example.com/video", description)
        self.assertNotIn("保留原视频内容", description)

    def test_download_filename_wins_over_generic_container_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "Application Data"),
                github=GitHubConfig(site_dir=root / "Repository/personal-podcast/site"),
            )
            service = PersonalPodcastService(config)
            service.metadata = type("NoMetadata", (), {"read": lambda self, url: None})()
            service.downloads = FakeDownloadManager()
            service.media = FakeMediaProcessor()
            service.initialize()
            episode = service.add("https://example.com/video")
            self.assertEqual(episode.title, "source")

    def test_single_link_publish_archive_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "Application Data"),
                github=GitHubConfig(site_dir=root / "Repository/personal-podcast/site"),
            )
            service = PersonalPodcastService(config)
            service.metadata = FakeMetadataReader()
            service.downloads = FakeDownloadManager()
            service.media = FakeMediaProcessor()
            service.publisher = FakeReleasePublisher()
            service.initialize()

            episode = service.add("https://example.com/watch?v=123", publish=True)
            self.assertEqual(episode.episode_id, "example-123")
            self.assertTrue(episode.audio_path.exists())
            self.assertTrue(episode.source_path and episode.source_path.exists())
            feed_path = config.github.site_dir / "feed.xml"
            self.assertEqual(validate_feed(feed_path), 1)

            service.publisher.host = "cloudflare.example"
            self.assertEqual(service.refresh_audio_urls(), 1)
            episode = service.store.get(episode.episode_id)
            self.assertTrue(
                episode.public_audio_url
                and episode.public_audio_url.startswith("https://cloudflare.example/")
            )

            episode.audio_path.write_bytes(b"updated final audio")
            episode = service.publish(episode.episode_id)
            self.assertEqual(episode.audio_bytes, len(b"updated final audio"))

            service.archive(episode.episode_id)
            self.assertEqual(validate_feed(feed_path), 0)
            service.restore(episode.episode_id)
            self.assertEqual(validate_feed(feed_path), 1)


if __name__ == "__main__":
    unittest.main()
