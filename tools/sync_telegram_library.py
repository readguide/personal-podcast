#!/usr/bin/env python3
"""Export video metadata and thumbnails from a Telegram channel.

This intentionally downloads thumbnails only, never the video files.
"""

import argparse
import asyncio
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo, MessageEntityTextUrl


TAGGED_CAPTION = re.compile(r"^#(?P<platform>\S+)\s+#(?P<author>\S+)\s+(?P<title>.+)$", re.S)
HREF = re.compile(r'href=["\'](https?://[^"\']+)', re.I)


def parse_args() -> argparse.Namespace:
    home = Path.home()
    parser = argparse.ArgumentParser(description="同步 Telegram channel 视频索引与缩略图")
    parser.add_argument("--channel", type=int, default=-1003657440623)
    parser.add_argument("--session", type=Path, default=home / ".openclaw/telegram/owner")
    parser.add_argument("--creds", type=Path, default=home / ".openclaw/telegram/owner-creds.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=home / "Downloads/en/Personal Podcast/Application Data/channel-videos.json",
    )
    parser.add_argument(
        "--thumbnails",
        type=Path,
        default=home / "Downloads/en/Personal Podcast/Artwork/Channel",
    )
    parser.add_argument("--proxy-host", default="127.0.0.1")
    parser.add_argument("--proxy-port", type=int, default=7897)
    return parser.parse_args()


def source_url(message: Any) -> Optional[str]:
    for entity in message.entities or []:
        if isinstance(entity, MessageEntityTextUrl) and entity.url.startswith("http"):
            return entity.url
    literal = HREF.search(message.raw_text or "")
    return html.unescape(literal.group(1)) if literal else None


def caption_fields(raw: str) -> tuple[str, str, str]:
    cleaned = HREF.sub("", raw or "")
    cleaned = re.sub(r"</?a(?:\s+[^>]*)?>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*来源\s*$", "", cleaned).strip()
    cleaned = cleaned.replace("\\n", " ")
    cleaned = " ".join(cleaned.split())
    match = TAGGED_CAPTION.match(cleaned)
    if not match:
        return "", "", cleaned or "未命名视频"
    return match.group("platform"), match.group("author"), match.group("title").strip()


def duration_for(message: Any) -> float:
    document = message.document
    if not document:
        return 0
    attribute = next(
        (item for item in document.attributes if isinstance(item, DocumentAttributeVideo)),
        None,
    )
    return float(attribute.duration or 0) if attribute else 0


async def export(args: argparse.Namespace) -> Dict[str, Any]:
    credentials = json.loads(args.creds.read_text(encoding="utf-8"))
    client = TelegramClient(
        str(args.session),
        credentials["api_id"],
        credentials["api_hash"],
        proxy=("http", args.proxy_host, args.proxy_port),
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram 本人账号会话尚未登录")

    args.thumbnails.mkdir(parents=True, exist_ok=True)
    deduplicated: Dict[str, Dict[str, Any]] = {}
    async for message in client.iter_messages(args.channel):
        mime = getattr(message.document, "mime_type", "") if message.document else ""
        if not (message.video or mime.startswith("video/")):
            continue
        original = source_url(message)
        if not original or original in deduplicated:
            continue
        platform, author, title = caption_fields(message.raw_text or "")
        thumb_target = args.thumbnails / f"telegram-{message.id}.jpg"
        thumbnail_path = ""
        try:
            downloaded = await client.download_media(message, file=str(thumb_target), thumb=-1)
            thumbnail_path = str(Path(downloaded)) if downloaded else ""
        except Exception:
            thumbnail_path = ""
        deduplicated[original] = {
            "message_id": message.id,
            "title": title,
            "author": author,
            "platform": platform,
            "source_url": original,
            "telegram_url": f"https://t.me/c/{str(abs(args.channel))[3:]}/{message.id}",
            "published_at": message.date.astimezone(timezone.utc).isoformat(),
            "duration_seconds": duration_for(message),
            "thumbnail_path": thumbnail_path,
        }
    await client.disconnect()
    videos = sorted(deduplicated.values(), key=lambda row: row["published_at"], reverse=True)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": args.channel,
        "videos": videos,
    }


def main() -> int:
    args = parse_args()
    payload = asyncio.run(export(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"已同步 {len(payload['videos'])} 条 channel 视频：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
