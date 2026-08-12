import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from personal_podcast.commands import run_checked
from personal_podcast.config import AudioConfig
from personal_podcast.errors import MediaError
from personal_podcast.models import AudioResult, MediaInfo


INVALID_FILENAME_CHARACTERS = re.compile(r"[\\/:*?\"<>|\x00]")


def _safe_output_stem(title: str, episode_id: str) -> str:
    """音频输出文件名主干: 标题(去非法字符, 截断); 空时回退 episode_id。"""
    clean = " ".join(title.split())
    clean = INVALID_FILENAME_CHARACTERS.sub("-", clean).strip(" .-") or episode_id
    return clean[:120].rstrip(" .-") or episode_id


@dataclass(frozen=True)
class AudioPlan:
    extension: str
    mime_type: str
    action: str
    bitrate_kbps: Optional[int]


def balanced_audio_plan(info: MediaInfo, configured_bitrate_kbps: int) -> AudioPlan:
    codec = info.codec_name.lower()
    if codec == "aac":
        return AudioPlan("m4a", "audio/mp4", "remux", None)
    if codec == "mp3":
        return AudioPlan("mp3", "audio/mpeg", "remux", None)
    bitrate = configured_bitrate_kbps
    if info.bit_rate:
        bitrate = max(32, min(configured_bitrate_kbps, round(info.bit_rate / 1000)))
    return AudioPlan("m4a", "audio/mp4", "transcode", bitrate)


class MediaProcessor:
    def __init__(self, config: AudioConfig):
        self.config = config

    def probe(self, path: Path) -> MediaInfo:
        result = run_checked(
            [
                self.config.ffprobe_command,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            error_type=MediaError,
        )
        try:
            payload: Dict[str, Any] = json.loads(result.stdout)
            audio_stream = next(
                stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"
            )
        except (json.JSONDecodeError, StopIteration, TypeError) as error:
            raise MediaError(f"文件中没有可用音轨: {path}") from error
        streams = payload.get("streams", [])
        format_data = payload.get("format", {})
        tags = {
            str(key).lower(): str(value)
            for key, value in {**format_data.get("tags", {}), **audio_stream.get("tags", {})}.items()
        }
        return MediaInfo(
            codec_name=str(audio_stream.get("codec_name") or "unknown"),
            duration_seconds=_float(audio_stream.get("duration") or format_data.get("duration"), 0.0),
            bit_rate=_optional_int(audio_stream.get("bit_rate")),
            sample_rate=_optional_int(audio_stream.get("sample_rate")),
            channels=_optional_int(audio_stream.get("channels")),
            has_video=any(
                stream.get("codec_type") == "video"
                and not bool(stream.get("disposition", {}).get("attached_pic"))
                for stream in streams
            ),
            format_name=str(format_data.get("format_name") or ""),
            tags=tags,
        )

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
        info = self.probe(source_path)
        plan = balanced_audio_plan(info, self.config.aac_bitrate_kbps)
        output_directory.mkdir(parents=True, exist_ok=True)
        output_stem = _safe_output_stem(title, episode_id)
        output_path = output_directory / f"{output_stem}.{plan.extension}"
        arguments: List[str] = [self.config.ffmpeg_command, "-y", "-i", str(source_path)]
        has_artwork = bool(artwork_path and artwork_path.exists())
        embed_artwork = has_artwork and plan.extension == "mp3"
        if embed_artwork:
            arguments.extend(["-i", str(artwork_path)])
        arguments.extend(
            [
                "-map",
                "0:a:0",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
            ]
        )
        if embed_artwork:
            arguments.extend(
                ["-map", "1:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
            )
        if plan.action == "remux":
            arguments.extend(["-c:a", "copy"])
        else:
            arguments.extend(["-c:a", "aac", "-b:a", f"{plan.bitrate_kbps}k"])
        arguments.extend(
            [
                "-metadata",
                f"title={title}",
                "-metadata",
                f"artist={author}",
                "-metadata",
                f"album={album}",
                "-metadata",
                f"comment={description}",
            ]
        )
        if plan.extension == "m4a":
            arguments.extend(["-movflags", "+faststart"])
        else:
            arguments.extend(["-id3v2_version", "3"])
        arguments.append(str(output_path))
        run_checked(arguments, error_type=MediaError)
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise MediaError("音频处理结束，但成品文件不存在或为空")
        return AudioResult(path=output_path, mime_type=plan.mime_type, action=plan.action)


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
