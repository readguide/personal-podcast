import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from personal_podcast.config import AppConfig, GitHubConfig, StorageConfig
from personal_podcast.feed import validate_feed, write_feed
from personal_podcast.models import Episode


def episode(
    episode_id: str,
    imported_at: datetime,
    *,
    published: bool = True,
    archived: bool = False,
) -> Episode:
    return Episode(
        episode_id=episode_id,
        source_url=f"https://example.com/{episode_id}?a=1&b=2",
        title=f"标题 {episode_id}",
        description="描述包含 & 和 < 字符",
        author="原作者",
        imported_at=imported_at,
        duration_seconds=3661,
        audio_path=Path(f"/tmp/{episode_id}.m4a"),
        audio_bytes=12345,
        audio_mime="audio/mp4",
        public_audio_url=(
            f"https://github.com/readguide/personal-podcast/releases/download/{episode_id}/{episode_id}.m4a"
            if published
            else None
        ),
        archived_at=imported_at if archived else None,
    )


class FeedTests(unittest.TestCase):
    def test_feed_is_valid_and_sorted_by_import_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "data"),
                github=GitHubConfig(site_dir=root / "site"),
            )
            older = episode("older", datetime(2026, 1, 1, tzinfo=timezone.utc))
            newer = episode("newer", datetime(2026, 1, 2, tzinfo=timezone.utc))
            local = episode("local", datetime(2026, 1, 3, tzinfo=timezone.utc), published=False)
            archived = episode(
                "archived", datetime(2026, 1, 4, tzinfo=timezone.utc), archived=True
            )
            feed_path = root / "site/feed.xml"
            write_feed(feed_path, config, [older, newer, local, archived])
            self.assertEqual(validate_feed(feed_path), 2)
            tree = ET.parse(feed_path)
            titles = [item.findtext("title") for item in tree.findall("./channel/item")]
            self.assertEqual(titles, ["标题 newer", "标题 older"])
            self.assertIn("&amp;", feed_path.read_text(encoding="utf-8"))

    def test_empty_feed_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "data"),
                github=GitHubConfig(site_dir=root / "site"),
            )
            feed_path = root / "site/feed.xml"
            write_feed(feed_path, config, [])
            self.assertEqual(validate_feed(feed_path), 0)

    def test_empty_feed_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "data"),
                github=GitHubConfig(site_dir=root / "site"),
            )
            feed_path = root / "site/feed.xml"
            write_feed(feed_path, config, [])
            first = feed_path.read_bytes()
            write_feed(feed_path, config, [])
            self.assertEqual(feed_path.read_bytes(), first)

    def test_invalid_xml_has_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            feed_path = Path(temporary) / "feed.xml"
            feed_path.write_text("<rss>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "RSS XML 无法解析"):
                validate_feed(feed_path)


if __name__ == "__main__":
    unittest.main()
