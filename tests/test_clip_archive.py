import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from personal_podcast.clip_archive import (
    ClipArchiveProcessor,
    ClipArchiveStateStore,
    is_wechat_article,
    parse_clip_archive,
)


SAMPLE = """特稿｜王的猜想
来源：https://mp.weixin.qq.com/s/78iTVN6tNW_8iua-dJ-img
保存日期：2026年8月1日 17:08
===

理想高管全面复盘_哔哩哔哩_bilibili
来源：https://www.bilibili.com/video/BV1DMMuz7EDs/
保存日期：2026年8月1日 23:37
===

库克最会赚的钱，继任未必收得到
来源：https://mp.weixin.qq.com/s/example-latest-article
保存日期：2026年8月2日 11:45
===
"""


class FakeStore:
    def __init__(self) -> None:
        self.episodes = {}

    def find_by_source_url(self, url):
        return self.episodes.get(url)


class FakeClassifier:
    def __init__(self) -> None:
        self.checked = []

    def is_video(self, url):
        self.checked.append(url)
        return "bilibili.com/video/" in url


class FakeService:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.link_classifier = FakeClassifier()
        self.synced = 0

    def add(self, url, publish=False):
        episode = SimpleNamespace(
            episode_id="new-video", source_url=url, public_audio_url="published"
        )
        self.store.episodes[url] = episode
        return episode

    def publish(self, episode_id):
        raise AssertionError("test does not publish existing local episodes")

    def sync_site(self, message):
        self.synced += 1
        return True


class ClipArchiveTests(unittest.TestCase):
    def test_parse_archive_fields_and_dates(self) -> None:
        records = parse_clip_archive(SAMPLE)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[1].title, "理想高管全面复盘_哔哩哔哩_bilibili")
        self.assertEqual(records[1].saved_at, datetime(2026, 8, 1, 23, 37))
        self.assertTrue(is_wechat_article(records[0].source_url))

    def test_baseline_ignores_existing_records_and_falls_back_to_date(self) -> None:
        records = parse_clip_archive(SAMPLE)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            store = ClipArchiveStateStore(path, Path(temporary) / "clips.txt")
            store.initialize(records, baseline=records[1])
            state = store.load()
            self.assertEqual(store.candidates(records, state), [records[2]])
            self.assertEqual(store.candidates(records[2:], state), [records[2]])

    def test_processor_skips_wechat_and_deduplicates_completed_entries(self) -> None:
        baseline_text = """旧记录
来源：https://example.com/old
保存日期：2026年8月2日 11:45
===
"""
        new_text = baseline_text + """微信文章
来源：https://mp.weixin.qq.com/s/new-article
保存日期：2026年8月2日 12:00
===
新视频
来源：https://www.bilibili.com/video/BV1NEW123456/
保存日期：2026年8月2日 12:01
===
普通文章
来源：https://example.com/article
保存日期：2026年8月2日 12:02
===
"""
        records = parse_clip_archive(new_text)
        with tempfile.TemporaryDirectory() as temporary:
            store = ClipArchiveStateStore(
                Path(temporary) / "state.json", Path(temporary) / "clips.txt"
            )
            service = FakeService()
            processor = ClipArchiveProcessor(service, store)
            processor.initialize(records, baseline=records[0])

            summary = processor.process(records, publish=True, sync_site=True)
            self.assertEqual(summary.imported, ["new-video"])
            self.assertEqual(summary.skipped_wechat, 1)
            self.assertEqual(summary.skipped_non_media, 1)
            self.assertEqual(service.synced, 1)
            self.assertNotIn(
                "https://mp.weixin.qq.com/s/new-article",
                service.link_classifier.checked,
            )

            repeated = processor.process(records, publish=True, sync_site=True)
            self.assertEqual(repeated.candidates, 0)
            self.assertEqual(service.synced, 1)


if __name__ == "__main__":
    unittest.main()
