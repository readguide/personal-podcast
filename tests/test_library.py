import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from personal_podcast.library import (
    ChannelVideo,
    load_channel_videos,
    merge_library,
    normalized_source_url,
)
from personal_podcast.models import Episode
from personal_podcast.site import SiteGenerator
from personal_podcast.config import AppConfig, GitHubConfig, StorageConfig


def podcast_episode(url: str) -> Episode:
    return Episode(
        episode_id="example",
        source_url=url,
        title="播客标题…",
        description="",
        author="en",
        imported_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        duration_seconds=60,
        audio_path=Path("/tmp/example.m4a"),
        audio_bytes=1,
        audio_mime="audio/mp4",
        public_audio_url="https://example.com/audio.m4a",
    )


class LibraryTests(unittest.TestCase):
    def test_normalized_source_url_ignores_fragment_and_trailing_slash(self) -> None:
        self.assertEqual(
            normalized_source_url("HTTPS://Example.COM/video/#part"),
            "https://example.com/video",
        )

    def test_same_source_merges_podcast_and_channel(self) -> None:
        source = "https://example.com/video/"
        channel = ChannelVideo(
            message_id=42,
            title="完整的 Channel 标题",
            author="原作者",
            platform="抖音",
            source_url="https://example.com/video",
            telegram_url="https://t.me/c/1/42",
            published_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        items = merge_library([podcast_episode(source)], [channel])
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].in_podcast)
        self.assertTrue(items[0].in_channel)
        self.assertEqual(items[0].title, "完整的 Channel 标题")
        self.assertEqual(items[0].author, "原作者")

    def test_channel_json_is_optional_and_loads_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "channel-videos.json"
            self.assertEqual(load_channel_videos(path), [])
            path.write_text(
                json.dumps(
                    {
                        "videos": [
                            {
                                "message_id": 7,
                                "title": "标题",
                                "source_url": "https://youtu.be/abc",
                                "telegram_url": "https://t.me/c/1/7",
                                "published_at": "2026-08-15T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            videos = load_channel_videos(path)
            self.assertEqual(len(videos), 1)
            self.assertEqual(videos[0].platform, "YouTube")

    def test_homepage_has_compact_header_and_time_scrubber(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "data"),
                github=GitHubConfig(site_dir=root / "site"),
            )
            item = merge_library(
                [podcast_episode("https://example.com/video")], []
            )[0]
            rendered = SiteGenerator(config)._render_index(
                [item], datetime(2026, 8, 15, tzinfo=timezone.utc)
            )
            self.assertIn('class="time-scrubber"', rendered)
            self.assertIn('data-date="2026-08-12"', rendered)
            self.assertIn('class="timeline-tooltip"', rendered)
            self.assertIn('2026年8月12日 · 1条视频', rendered)
            self.assertNotIn('timeline-track::before', rendered)
            self.assertNotIn('class="timeline-handle"', rendered)
            self.assertIn('class="date-jump"', rendered)
            self.assertNotIn("把分享到播客", rendered)


if __name__ == "__main__":
    unittest.main()
