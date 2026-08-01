import tempfile
import unittest
from pathlib import Path

from personal_podcast.config import (
    AppConfig,
    AudioConfig,
    ConfigError,
    DownloadConfig,
    GitHubConfig,
    PodcastConfig,
    StorageConfig,
    config_from_mapping,
    parse_toml,
    render_config,
)


class ConfigTests(unittest.TestCase):
    def test_defaults_match_confirmed_storage_and_podcast(self) -> None:
        config = AppConfig()
        self.assertEqual(config.podcast.name, "收听库")
        self.assertEqual(config.podcast.author, "en")
        self.assertEqual(
            config.storage.root,
            Path("/Users/en/Downloads/en/Personal Podcast"),
        )
        self.assertEqual(
            config.storage.app_dir,
            Path("/Users/en/Downloads/en/Personal Podcast/Application Data"),
        )
        self.assertEqual(config.storage.source_retention_days, 90)
        self.assertEqual(
            config.storage.transcripts_dir,
            Path("/Users/en/Downloads/en/Personal Podcast/Transcripts"),
        )
        self.assertEqual(config.download.order, ["downie", "yt-dlp"])
        self.assertEqual(config.download.downie_fallback_directory, Path.home() / "Downloads")

    def test_rendered_config_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = AppConfig(
                podcast=PodcastConfig(name="收听库", author="en", description="含有 # 与引号 \""),
                storage=StorageConfig(root=root, app_dir=root / "data"),
                download=DownloadConfig(downie_poll_seconds=0.25),
                audio=AudioConfig(),
                github=GitHubConfig(site_dir=root / "repo/site"),
            )
            loaded = config_from_mapping(parse_toml(render_config(original)))
        self.assertEqual(loaded, original)

    def test_unknown_downloader_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            config_from_mapping({"download": {"order": ["unknown"]}})

    def test_app_data_follows_a_custom_root(self) -> None:
        config = config_from_mapping({"storage": {"root": "/tmp/custom-podcast"}})
        self.assertEqual(config.storage.app_dir, Path("/tmp/custom-podcast/Application Data"))


if __name__ == "__main__":
    unittest.main()
