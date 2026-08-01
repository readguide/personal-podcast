import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_podcast.config import DownloadConfig
from personal_podcast.errors import PersonalPodcastError
from personal_podcast.inbox import VideoLinkClassifier, latest_link


class LinkInboxTests(unittest.TestCase):
    def test_latest_link_uses_last_url_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "links.txt"
            path.write_text(
                "https://example.com/old\n稍后看：https://youtu.be/OcKl98ZQbMQ。\n",
                encoding="utf-8",
            )
            self.assertEqual(latest_link(path), "https://youtu.be/OcKl98ZQbMQ")

    def test_empty_inbox_has_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "links.txt"
            path.write_text("没有链接", encoding="utf-8")
            with self.assertRaisesRegex(PersonalPodcastError, "没有可用链接"):
                latest_link(path)

    def test_known_youtube_url_does_not_need_remote_probe(self) -> None:
        classifier = VideoLinkClassifier(DownloadConfig())
        with patch("personal_podcast.inbox.executable_exists", return_value=False):
            self.assertTrue(
                classifier.is_video("https://www.youtube.com/watch?v=OcKl98ZQbMQ")
            )

    def test_plain_article_is_skipped_when_no_media_is_detected(self) -> None:
        classifier = VideoLinkClassifier(DownloadConfig())
        with patch("personal_podcast.inbox.executable_exists", return_value=False):
            self.assertFalse(classifier.is_video("https://example.com/article"))

    def test_unknown_site_is_accepted_when_probe_finds_media(self) -> None:
        classifier = VideoLinkClassifier(DownloadConfig())
        result = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"duration": 30, "formats": [{}]}), stderr=""
        )
        with patch("personal_podcast.inbox.executable_exists", return_value=True), patch(
            "personal_podcast.inbox.run_checked", return_value=result
        ):
            self.assertTrue(classifier.is_video("https://example.com/watch/123"))


if __name__ == "__main__":
    unittest.main()
