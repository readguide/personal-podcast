import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_podcast.models import Episode
from personal_podcast.store import EpisodeStore


class StoreTests(unittest.TestCase):
    def test_archive_restore_publish_and_cleanup_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = EpisodeStore(root / "podcast.db")
            store.initialize()
            now = datetime.now(timezone.utc)
            source = root / "source.opus"
            source.write_bytes(b"source")
            audio = root / "final.m4a"
            audio.write_bytes(b"final")
            store.save(
                Episode(
                    episode_id="episode-one",
                    source_url="https://example.com/one",
                    title="One",
                    description="Description",
                    author="Author",
                    imported_at=now,
                    duration_seconds=10,
                    audio_path=audio,
                    audio_bytes=5,
                    audio_mime="audio/mp4",
                    source_path=source,
                    source_cleanup_after=now - timedelta(seconds=1),
                )
            )
            store.set_published("episode-one", "https://example.com/audio.m4a", "tag-one")
            self.assertEqual(len(store.list_visible()), 1)
            store.set_archived("episode-one", now)
            self.assertEqual(store.list_visible(), [])
            store.set_archived("episode-one", None)
            self.assertEqual(len(store.list_visible()), 1)
            self.assertEqual(len(store.list_cleanable(now)), 1)
            store.mark_source_cleaned("episode-one", now)
            self.assertEqual(store.list_cleanable(now), [])


if __name__ == "__main__":
    unittest.main()
