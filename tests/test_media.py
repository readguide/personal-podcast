import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from personal_podcast.config import AudioConfig
from personal_podcast.media import MediaProcessor, balanced_audio_plan
from personal_podcast.models import MediaInfo


def media_info(codec: str, bitrate: int = 96000) -> MediaInfo:
    return MediaInfo(
        codec_name=codec,
        duration_seconds=60,
        bit_rate=bitrate,
        sample_rate=48000,
        channels=2,
        has_video=False,
        format_name="test",
        tags={},
    )


class AudioPlanTests(unittest.TestCase):
    def test_aac_and_mp3_are_not_transcoded(self) -> None:
        self.assertEqual(balanced_audio_plan(media_info("aac"), 128).action, "remux")
        self.assertEqual(balanced_audio_plan(media_info("mp3"), 128).action, "remux")

    def test_low_bitrate_opus_does_not_use_128k(self) -> None:
        plan = balanced_audio_plan(media_info("opus", 64000), 128)
        self.assertEqual(plan.action, "transcode")
        self.assertEqual(plan.bitrate_kbps, 64)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg unavailable")
class MediaIntegrationTests(unittest.TestCase):
    def test_opus_source_is_converted_to_aac_m4a(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.opus"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.2",
                    "-c:a",
                    "libopus",
                    str(source),
                ],
                check=True,
            )
            processor = MediaProcessor(AudioConfig())
            result = processor.process(
                source,
                root / "final",
                "episode-test",
                "测试节目",
                "en",
                "收听库",
                "测试简介",
                Path(__file__).parents[1]
                / "src/personal_podcast/assets/podcast-cover.png",
            )
            info = processor.probe(result.path)
            self.assertEqual(result.path.suffix, ".m4a")
            self.assertEqual(info.codec_name, "aac")
            self.assertFalse(info.has_video)
            self.assertGreater(result.path.stat().st_size, 0)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type,codec_name",
                    "-of",
                    "json",
                    str(result.path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            streams = json.loads(probe.stdout)["streams"]
            self.assertEqual(streams, [{"codec_name": "aac", "codec_type": "audio"}])


if __name__ == "__main__":
    unittest.main()
