from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Dict, Iterable, Optional
from xml.etree import ElementTree as ET

from personal_podcast.config import AppConfig
from personal_podcast.models import Episode


ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("atom", ATOM)


def _itunes(name: str) -> str:
    return f"{{{ITUNES}}}{name}"


def _atom(name: str) -> str:
    return f"{{{ATOM}}}{name}"


def _rfc2822(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return format_datetime(aware.astimezone(timezone.utc))


def _duration(value: float) -> str:
    total = max(0, int(round(value)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _description(episode: Episode) -> str:
    description = episode.description.strip()
    if not episode.transcript_path or not episode.transcript_path.exists():
        return description
    transcript = episode.transcript_path.read_text(encoding="utf-8").strip()
    if not transcript:
        return description
    return (
        f"{description}\n\n自动转写全文（可能存在识别错误）：\n{transcript}"
    )


def build_feed(
    config: AppConfig,
    episodes: Iterable[Episode],
    episode_artwork_urls: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
) -> ET.ElementTree:
    artwork_urls = episode_artwork_urls or {}
    build_time = now or datetime(2026, 8, 1, tzinfo=timezone.utc)
    base_url = config.github.pages_base_url
    cover_url = f"{base_url}/artwork/podcast-cover.png"

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = config.podcast.name
    ET.SubElement(channel, "link").text = base_url
    ET.SubElement(channel, "description").text = config.podcast.description
    ET.SubElement(channel, "language").text = config.podcast.language
    ET.SubElement(channel, "lastBuildDate").text = _rfc2822(build_time)
    ET.SubElement(
        channel,
        _atom("link"),
        {
            "href": f"{base_url}/feed.xml",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )
    ET.SubElement(channel, _itunes("author")).text = config.podcast.author
    ET.SubElement(channel, _itunes("summary")).text = config.podcast.description
    ET.SubElement(channel, _itunes("explicit")).text = (
        "true" if config.podcast.explicit else "false"
    )
    ET.SubElement(channel, _itunes("image"), {"href": cover_url})

    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = cover_url
    ET.SubElement(image, "title").text = config.podcast.name
    ET.SubElement(image, "link").text = base_url

    for episode in sorted(episodes, key=lambda item: item.imported_at, reverse=True):
        if not episode.is_visible:
            continue
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode.title
        ET.SubElement(item, "link").text = episode.source_url
        ET.SubElement(item, "description").text = _description(episode)
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = f"personal-podcast:{episode.episode_id}"
        ET.SubElement(item, "pubDate").text = _rfc2822(episode.imported_at)
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": episode.public_audio_url or "",
                "length": str(episode.audio_bytes),
                "type": episode.audio_mime,
            },
        )
        ET.SubElement(item, _itunes("author")).text = episode.author or config.podcast.author
        ET.SubElement(item, _itunes("duration")).text = _duration(episode.duration_seconds)
        ET.SubElement(item, _itunes("explicit")).text = "false"
        artwork_url = artwork_urls.get(episode.episode_id, cover_url)
        ET.SubElement(item, _itunes("image"), {"href": artwork_url})
    return ET.ElementTree(rss)


def write_feed(
    output_path: Path,
    config: AppConfig,
    episodes: Iterable[Episode],
    episode_artwork_urls: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
) -> None:
    tree = build_feed(config, episodes, episode_artwork_urls, now)
    ET.indent(tree, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def validate_feed(path: Path) -> int:
    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        raise ValueError(f"RSS XML 无法解析: {error}") from error
    root = tree.getroot()
    if root.tag != "rss" or root.attrib.get("version") != "2.0":
        raise ValueError("RSS 根节点无效")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS 缺少 channel")
    required = ["title", "link", "description", "language"]
    missing = [name for name in required if not (channel.findtext(name) or "").strip()]
    if missing:
        raise ValueError(f"RSS 缺少字段: {', '.join(missing)}")
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.attrib.get("url"):
            raise ValueError("RSS 单集缺少 enclosure URL")
    return len(channel.findall("item"))
