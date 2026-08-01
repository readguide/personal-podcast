import html
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from personal_podcast.config import AppConfig
from personal_podcast.feed import validate_feed, write_feed
from personal_podcast.models import Episode


class SiteGenerator:
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def bundled_cover(self) -> Path:
        return Path(__file__).parent / "assets/podcast-cover.png"

    def ensure_local_cover(self) -> Path:
        destination = self.config.storage.artwork_dir / "podcast-cover.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            if not self.bundled_cover.exists():
                raise FileNotFoundError(f"缺少内置播客封面: {self.bundled_cover}")
            shutil.copy2(self.bundled_cover, destination)
        return destination

    def generate(self, episodes: Iterable[Episode]) -> int:
        site_dir = self.config.github.site_dir
        artwork_dir = site_dir / "artwork"
        episode_artwork_dir = artwork_dir / "episodes"
        episode_artwork_dir.mkdir(parents=True, exist_ok=True)

        local_cover = self.ensure_local_cover()
        shutil.copy2(local_cover, artwork_dir / "podcast-cover.png")

        episode_list: List[Episode] = list(episodes)
        updated_at = _site_updated_at(episode_list)
        artwork_urls: Dict[str, str] = {}
        for episode in episode_list:
            if not episode.artwork_path or not episode.artwork_path.exists():
                continue
            destination = episode_artwork_dir / f"{episode.episode_id}{episode.artwork_path.suffix.lower()}"
            shutil.copy2(episode.artwork_path, destination)
            artwork_urls[episode.episode_id] = (
                f"{self.config.github.pages_base_url}/artwork/episodes/{destination.name}"
            )

        feed_path = site_dir / "feed.xml"
        write_feed(feed_path, self.config, episode_list, artwork_urls, now=updated_at)
        item_count = validate_feed(feed_path)
        (site_dir / ".nojekyll").touch()
        (site_dir / "index.html").write_text(
            self._render_index(episode_list, updated_at), encoding="utf-8"
        )
        return item_count

    def _render_index(self, episodes: Iterable[Episode], updated_at: datetime) -> str:
        visible = [episode for episode in episodes if episode.is_visible]
        episode_items = "\n".join(
            (
                '<li><div><a href="{audio}">{title}</a>'
                '<span>{date} · {duration}</span></div>'
                '<a class="source" href="{source}">原始来源</a></li>'
            ).format(
                audio=html.escape(episode.public_audio_url or "", quote=True),
                title=html.escape(episode.title),
                date=episode.imported_at.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                duration=_duration_text(episode.duration_seconds),
                source=html.escape(episode.source_url, quote=True),
            )
            for episode in visible
        )
        if not episode_items:
            episode_items = '<li class="empty">暂无已发布节目</li>'
        updated = updated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        name = html.escape(self.config.podcast.name)
        author = html.escape(self.config.podcast.author)
        description = html.escape(self.config.podcast.description)
        feed_url = f"{self.config.github.pages_base_url}/feed.xml"
        return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f3f4f1; color: #17201c; }}
    main {{ width: min(760px, calc(100% - 40px)); margin: 0 auto; padding: 48px 0 64px; }}
    header {{ display: grid; grid-template-columns: 152px 1fr; gap: 28px; align-items: center; padding-bottom: 32px; border-bottom: 1px solid #c7cbc6; }}
    img {{ width: 152px; aspect-ratio: 1; object-fit: cover; border-radius: 6px; }}
    h1 {{ margin: 0 0 8px; font-size: 36px; letter-spacing: 0; }}
    p {{ margin: 0 0 16px; line-height: 1.65; color: #4c5651; }}
    .byline {{ font-size: 14px; color: #69736e; }}
    .subscribe {{ display: inline-flex; align-items: center; min-height: 40px; padding: 0 14px; border-radius: 6px; background: #16684f; color: #fff; text-decoration: none; font-weight: 650; }}
    h2 {{ margin: 34px 0 12px; font-size: 20px; letter-spacing: 0; }}
    ul {{ list-style: none; padding: 0; margin: 0; border-top: 1px solid #d5d8d4; }}
    li {{ display: flex; justify-content: space-between; gap: 20px; padding: 18px 0; border-bottom: 1px solid #d5d8d4; }}
    li a {{ color: #17201c; font-weight: 650; text-decoration: none; }}
    li span {{ display: block; margin-top: 5px; color: #6a746f; font-size: 13px; }}
    li .source {{ align-self: center; color: #16684f; font-size: 14px; white-space: nowrap; }}
    .empty {{ color: #6a746f; }}
    footer {{ margin-top: 28px; color: #78817c; font-size: 12px; }}
    @media (max-width: 560px) {{
      main {{ width: min(100% - 28px, 760px); padding-top: 28px; }}
      header {{ grid-template-columns: 96px 1fr; gap: 18px; align-items: start; }}
      img {{ width: 96px; }}
      h1 {{ font-size: 27px; }}
      header p {{ grid-column: 1 / -1; margin-bottom: 0; }}
      .subscribe {{ grid-column: 1 / -1; justify-content: center; }}
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #151917; color: #edf2ee; }}
      p, .byline, li span, footer, .empty {{ color: #aab3ad; }}
      header, ul, li {{ border-color: #39413c; }}
      li a {{ color: #edf2ee; }}
      .subscribe {{ background: #2b8a68; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <img src="artwork/podcast-cover.png" alt="{name}封面" width="152" height="152">
      <div><h1>{name}</h1><div class="byline">作者：{author}</div></div>
      <p>{description}</p>
      <a class="subscribe" href="{html.escape(feed_url, quote=True)}">订阅 RSS</a>
    </header>
    <section>
      <h2>节目</h2>
      <ul>{episode_items}</ul>
    </section>
    <footer>更新于 {updated}</footer>
  </main>
</body>
</html>
'''


def _duration_text(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _site_updated_at(episodes: Iterable[Episode]) -> datetime:
    moments = []
    for episode in episodes:
        moments.extend(
            value
            for value in (episode.imported_at, episode.archived_at, episode.deleted_at)
            if value is not None
        )
    return max(moments) if moments else datetime(2026, 8, 1, tzinfo=timezone.utc)
