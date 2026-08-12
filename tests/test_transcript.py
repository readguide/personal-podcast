import unittest
from datetime import datetime, timezone
from pathlib import Path

from personal_podcast.models import Episode
from personal_podcast.transcript import (
    format_transcript,
    transcript_audio_text,
    transcript_filename,
)


def example_episode() -> Episode:
    return Episode(
        episode_id="youtube-example-123",
        source_url="https://www.youtube.com/watch?v=example",
        title="视频/名称：测试？",
        description="Description",
        author="原作者",
        imported_at=datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc),
        duration_seconds=60,
        audio_path=Path("/tmp/example.m4a"),
        audio_bytes=100,
        audio_mime="audio/mp4",
    )


class TranscriptTests(unittest.TestCase):
    def test_filename_contains_episode_id_and_safe_video_title(self) -> None:
        filename = transcript_filename(example_episode())
        self.assertEqual(filename, "2026-08-01-视频-名称：测试？.txt")
        self.assertNotIn("/", filename)

    def test_formatted_transcript_has_metadata_before_audio_text(self) -> None:
        text = format_transcript(example_episode(), "第一段\n第二段")
        self.assertTrue(text.startswith("视频名称：视频/名称：测试？\n作者：原作者\n"))
        self.assertIn("原链接：https://www.youtube.com/watch?v=example", text)
        self.assertIn("导入时间：2026-08-01 22:30:00", text)
        self.assertNotIn("+0800", text)
        self.assertTrue(text.endswith("音频文本：\n第一段\n第二段\n"))
        self.assertEqual(transcript_audio_text(text), "第一段\n第二段")

    def test_plain_github_transcript_remains_compatible(self) -> None:
        self.assertEqual(transcript_audio_text("原始转写\n"), "原始转写")


if __name__ == "__main__":
    unittest.main()
