import html
import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List
from zoneinfo import ZoneInfo

from personal_podcast.config import AppConfig
from personal_podcast.feed import validate_feed, write_feed
from personal_podcast.library import LibraryItem, load_channel_videos, merge_library
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
        library_artwork_dir = artwork_dir / "library"
        episode_artwork_dir.mkdir(parents=True, exist_ok=True)
        library_artwork_dir.mkdir(parents=True, exist_ok=True)

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
        channel_videos = load_channel_videos(
            self.config.storage.app_dir / "channel-videos.json"
        )
        library_items = merge_library(episode_list, channel_videos)
        page_updated_at = max(
            [updated_at, *(item.date for item in library_items)]
        )
        for item in library_items:
            digest = hashlib.sha1(item.key.encode("utf-8")).hexdigest()[:16]
            generated_thumbnail = (
                self.config.storage.artwork_dir / "Library" / f"{digest}.jpg"
            )
            if not item.thumbnail_path or not item.thumbnail_path.exists():
                if generated_thumbnail.exists():
                    item.thumbnail_path = generated_thumbnail
                elif item.local_video_path and item.local_video_path.exists():
                    _extract_thumbnail(
                        item.local_video_path,
                        generated_thumbnail,
                        self.config.audio.ffmpeg_command,
                    )
                    if generated_thumbnail.exists():
                        item.thumbnail_path = generated_thumbnail
            if not item.thumbnail_path or not item.thumbnail_path.exists():
                continue
            suffix = item.thumbnail_path.suffix.lower() or ".jpg"
            destination = library_artwork_dir / f"{digest}{suffix}"
            shutil.copy2(item.thumbnail_path, destination)
            item.thumbnail_url = f"artwork/library/{destination.name}"
        (site_dir / ".nojekyll").touch()
        (site_dir / "index.html").write_text(
            self._render_index(library_items, page_updated_at), encoding="utf-8"
        )
        return item_count

    def _render_index(self, items: Iterable[LibraryItem], updated_at: datetime) -> str:
        library_items = list(items)
        cards = "\n".join(_render_card(item) for item in library_items)
        if not cards:
            cards = '<p class="empty">暂无已收藏视频</p>'
        china = ZoneInfo("Asia/Shanghai")
        updated = updated_at.astimezone(china).strftime("%Y-%m-%d %H:%M")
        name = html.escape(self.config.podcast.name)
        author = html.escape(self.config.podcast.author)
        feed_url = f"{self.config.github.pages_base_url}/feed.xml"
        podcast_count = sum(item.in_podcast for item in library_items)
        channel_count = sum(item.in_channel for item in library_items)
        return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="按时间整理的视频收藏，汇总播客节目与 Telegram channel。">
  <title>视频收藏库</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; --ink:#182019; --muted:#687269; --paper:#f3f1e9; --card:#fffefa; --line:#dcd9cf; --green:#1f654c; --orange:#c75b2a; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink); }}
    main {{ width:min(1180px, calc(100% - 36px)); margin:auto; padding:52px 0 72px; }}
    header {{ display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:28px; align-items:end; padding:0 0 36px; border-bottom:1px solid var(--line); }}
    .eyebrow {{ margin:0 0 10px; color:var(--orange); font-size:13px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }}
    h1 {{ margin:0; font-family:Georgia, "Songti SC", serif; font-size:clamp(42px, 7vw, 82px); font-weight:500; line-height:.98; letter-spacing:-.045em; }}
    .intro {{ max-width:680px; margin:20px 0 0; color:var(--muted); font-size:16px; line-height:1.7; }}
    .stats {{ display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; }}
    .stat {{ min-width:102px; padding:14px 16px; border:1px solid var(--line); border-radius:14px; background:rgba(255,255,255,.46); }}
    .stat strong {{ display:block; font:600 27px/1 Georgia,serif; }}
    .stat span {{ display:block; margin-top:6px; color:var(--muted); font-size:12px; }}
    .toolbar {{ position:sticky; top:0; z-index:5; display:flex; gap:12px; align-items:center; padding:18px 0; background:color-mix(in srgb, var(--paper) 92%, transparent); backdrop-filter:blur(14px); }}
    .filters {{ display:flex; gap:8px; flex-wrap:wrap; }}
    button, .rss {{ border:1px solid var(--line); border-radius:999px; background:var(--card); color:var(--ink); min-height:38px; padding:0 14px; font:650 13px/1 inherit; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }}
    button[aria-pressed="true"] {{ border-color:var(--green); background:var(--green); color:white; }}
    .search {{ margin-left:auto; width:min(300px, 32vw); min-height:40px; border:1px solid var(--line); border-radius:999px; background:var(--card); padding:0 16px; color:var(--ink); font:inherit; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; }}
    article {{ overflow:hidden; border:1px solid var(--line); border-radius:18px; background:var(--card); transition:transform .18s ease, box-shadow .18s ease; }}
    article:hover {{ transform:translateY(-3px); box-shadow:0 16px 34px rgba(45,42,34,.10); }}
    article[hidden] {{ display:none; }}
    .visual {{ position:relative; display:block; aspect-ratio:16/9; background:linear-gradient(145deg,#203d31,#c26738); overflow:hidden; }}
    .visual img {{ width:100%; height:100%; object-fit:cover; transition:transform .35s ease; }}
    article:hover .visual img {{ transform:scale(1.025); }}
    .no-image {{ position:absolute; inset:0; display:grid; place-items:center; color:rgba(255,255,255,.78); font:500 18px Georgia,serif; letter-spacing:.08em; }}
    .duration {{ position:absolute; right:10px; bottom:10px; padding:5px 8px; border-radius:7px; background:rgba(18,22,19,.82); color:#fff; font-size:12px; }}
    .card-body {{ padding:17px 17px 16px; }}
    .meta {{ display:flex; align-items:center; gap:7px; min-height:23px; margin-bottom:10px; flex-wrap:wrap; }}
    .badge {{ padding:4px 7px; border-radius:6px; font-size:11px; font-weight:750; }}
    .podcast {{ background:#dcebe4; color:#17543e; }} .channel {{ background:#f9e1d5; color:#9e421b; }}
    time {{ margin-left:auto; color:var(--muted); font-size:12px; }}
    h2 {{ margin:0; font-size:18px; line-height:1.45; letter-spacing:-.015em; }}
    h2 a {{ color:inherit; text-decoration:none; }}
    .author {{ margin:9px 0 0; color:var(--muted); font-size:13px; }}
    .actions {{ display:flex; gap:8px; margin-top:16px; padding-top:14px; border-top:1px solid #ebe8df; }}
    .actions a {{ color:var(--green); font-size:13px; font-weight:700; text-decoration:none; }}
    .actions a + a::before {{ content:"·"; margin-right:8px; color:#aaa79e; }}
    .empty {{ color:var(--muted); }}
    footer {{ display:flex; justify-content:space-between; gap:20px; margin-top:34px; padding-top:22px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} header {{ grid-template-columns:1fr; }} .stats {{ justify-content:flex-start; }} }}
    @media (max-width:600px) {{ main {{ width:min(100% - 24px,1180px); padding-top:28px; }} .grid {{ grid-template-columns:1fr; }} .toolbar {{ align-items:stretch; flex-direction:column; }} .search {{ order:-1; width:100%; margin:0; }} h1 {{ font-size:48px; }} .stat {{ flex:1; }} footer {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div><p class="eyebrow">{name} · by {author}</p><h1>视频收藏库</h1><p class="intro">把分享到播客与 Telegram channel 的视频放在同一条时间线上。每条都保留原视频链接，并标明它出现在哪里。</p></div>
      <div class="stats"><div class="stat"><strong>{len(library_items)}</strong><span>条独立视频</span></div><div class="stat"><strong>{podcast_count}</strong><span>播客</span></div><div class="stat"><strong>{channel_count}</strong><span>Channel</span></div></div>
    </header>
    <nav class="toolbar" aria-label="视频筛选"><div class="filters"><button data-filter="all" aria-pressed="true">全部</button><button data-filter="podcast" aria-pressed="false">播客</button><button data-filter="channel" aria-pressed="false">Channel</button></div><input class="search" type="search" placeholder="搜索标题或作者" aria-label="搜索标题或作者"><a class="rss" href="{html.escape(feed_url, quote=True)}">RSS</a></nav>
    <section class="grid" aria-live="polite">{cards}</section>
    <p class="empty" id="no-results" hidden>没有符合条件的视频。</p>
    <footer><span>更新于 {updated}（北京时间）</span><span>原视频版权归原作者所有</span></footer>
  </main>
  <script>
    const cards=[...document.querySelectorAll('article[data-sources]')];
    const buttons=[...document.querySelectorAll('button[data-filter]')];
    const search=document.querySelector('.search');
    let active='all';
    function apply(){{
      const q=search.value.trim().toLowerCase(); let shown=0;
      cards.forEach(card=>{{const source=card.dataset.sources.split(' '); const okSource=active==='all'||source.includes(active); const okText=!q||card.dataset.search.includes(q); card.hidden=!(okSource&&okText); if(!card.hidden) shown++;}});
      document.querySelector('#no-results').hidden=shown!==0;
    }}
    buttons.forEach(button=>button.addEventListener('click',()=>{{active=button.dataset.filter; buttons.forEach(item=>item.setAttribute('aria-pressed',String(item===button))); apply();}}));
    search.addEventListener('input',apply);
  </script>
</body>
</html>
'''


def _render_card(item: LibraryItem) -> str:
    badges = []
    sources = []
    if item.in_podcast:
        badges.append('<span class="badge podcast">播客</span>')
        sources.append("podcast")
    if item.in_channel:
        badges.append('<span class="badge channel">Channel</span>')
        sources.append("channel")
    date_text = item.date.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    image = (
        f'<img src="{html.escape(item.thumbnail_url, quote=True)}" alt="" loading="lazy">'
        if item.thumbnail_url
        else '<span class="no-image">VIDEO ARCHIVE</span>'
    )
    duration = (
        f'<span class="duration">{_duration_text(item.duration_seconds)}</span>'
        if item.duration_seconds
        else ""
    )
    actions = [
        f'<a href="{html.escape(item.source_url, quote=True)}">原视频</a>'
    ]
    if item.audio_url:
        actions.append(f'<a href="{html.escape(item.audio_url, quote=True)}">收听</a>')
    searchable = html.escape(f"{item.title} {item.author} {item.platform}".lower(), quote=True)
    author = html.escape(item.author or item.platform)
    return (
        f'<article data-sources="{" ".join(sources)}" data-search="{searchable}">'
        f'<a class="visual" href="{html.escape(item.source_url, quote=True)}">{image}{duration}</a>'
        f'<div class="card-body"><div class="meta">{"".join(badges)}<time datetime="{date_text}">{date_text}</time></div>'
        f'<h2><a href="{html.escape(item.source_url, quote=True)}">{html.escape(item.title)}</a></h2>'
        f'<p class="author">{author} · {html.escape(item.platform)}</p>'
        f'<div class="actions">{"".join(actions)}</div></div></article>'
    )


def _extract_thumbnail(source: Path, destination: Path, ffmpeg_command: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.jpg")
    try:
        subprocess.run(
            [
                ffmpeg_command,
                "-y",
                "-v",
                "error",
                "-ss",
                "5",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-2",
                "-q:v",
                "3",
                str(temporary),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )
        temporary.replace(destination)
    except (OSError, subprocess.SubprocessError):
        temporary.unlink(missing_ok=True)


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
