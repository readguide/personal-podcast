import re
from datetime import timedelta, timezone

from personal_podcast.models import Episode


TRANSCRIPT_MARKER = "音频文本："
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
INVALID_FILENAME_CHARACTERS = re.compile(r"[\\/:*?\"<>|\x00]")


def transcript_filename(episode: Episode) -> str:
    """转录稿文件名: 日期-标题.txt (2026-08-12 用户要求可读命名)。"""
    title = " ".join(episode.title.split())
    title = INVALID_FILENAME_CHARACTERS.sub("-", title).strip(" .-") or "未命名视频"
    title = _truncate_utf8(title, 120)
    date_part = episode.imported_at.astimezone(CHINA_STANDARD_TIME).strftime("%Y-%m-%d")
    return f"{date_part}-{title}.txt"


def format_transcript(episode: Episode, audio_text: str) -> str:
    imported = episode.imported_at.astimezone(CHINA_STANDARD_TIME)
    return (
        f"视频名称：{episode.title}\n"
        f"作者：{episode.author}\n"
        f"原链接：{episode.source_url}\n"
        f"导入时间：{imported.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"\n{TRANSCRIPT_MARKER}\n"
        f"{transcript_audio_text(audio_text)}\n"
    )


def transcript_audio_text(text: str) -> str:
    _, marker, body = text.partition(TRANSCRIPT_MARKER)
    return (body if marker else text).strip()


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore").rstrip(" .-")
