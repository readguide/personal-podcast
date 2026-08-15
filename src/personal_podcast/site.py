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
        feed_url = f"{self.config.github.pages_base_url}/feed.xml"
        dates = sorted(
            {item.date.astimezone(china).date() for item in library_items}, reverse=True
        )
        newest_day = dates[0].toordinal() if dates else 0
        oldest_day = dates[-1].toordinal() if dates else 0
        span = max(1, newest_day - oldest_day)
        timeline_ticks = "".join(
            (
                '<button class="timeline-tick" data-date="{date}" '
                'style="--at:{position:.3f}%" aria-label="跳到 {label}"></button>'
            ).format(
                date=value.isoformat(),
                label=f"{value.month}月{value.day}日",
                position=(newest_day - value.toordinal()) / span * 100,
            )
            for value in dates
        )
        date_options = "".join(
            f'<option value="{value.isoformat()}">{value.month}月{value.day}日</option>'
            for value in dates
        )
        newest_label = f"{dates[0].month}月{dates[0].day}日" if dates else ""
        oldest_label = f"{dates[-1].month}月{dates[-1].day}日" if dates else ""
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
    main {{ width:min(1180px, calc(100% - 36px)); margin:auto; padding:22px 0 72px; }}
    header {{ display:flex; align-items:baseline; justify-content:space-between; gap:20px; min-height:44px; }}
    h1 {{ margin:0; font-family:Georgia, "Songti SC", serif; font-size:28px; font-weight:500; letter-spacing:-.025em; }}
    .count {{ color:var(--muted); font-size:12px; }}
    .toolbar {{ position:sticky; top:0; z-index:5; display:flex; gap:12px; align-items:center; padding:12px 0 14px; border-top:1px solid var(--line); background:color-mix(in srgb, var(--paper) 92%, transparent); backdrop-filter:blur(14px); }}
    .filters {{ display:flex; gap:8px; flex-wrap:wrap; }}
    button, .rss {{ border:1px solid var(--line); border-radius:999px; background:var(--card); color:var(--ink); min-height:38px; padding:0 14px; font:650 13px/1 inherit; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }}
    button[aria-pressed="true"] {{ border-color:var(--green); background:var(--green); color:white; }}
    .search {{ margin-left:auto; width:min(300px, 32vw); min-height:40px; border:1px solid var(--line); border-radius:999px; background:var(--card); padding:0 16px; color:var(--ink); font:inherit; }}
    .date-jump {{ display:none; min-height:40px; border:1px solid var(--line); border-radius:999px; background:var(--card); padding:0 32px 0 13px; color:var(--ink); font:650 13px/1 inherit; }}
    .time-scrubber {{ position:fixed; z-index:8; left:max(14px, calc((100vw - 1340px)/2)); top:120px; width:86px; height:min(62vh,560px); color:var(--muted); user-select:none; }}
    .timeline-track {{ position:absolute; left:19px; top:25px; bottom:25px; width:28px; cursor:ns-resize; touch-action:none; }}
    .timeline-track::before {{ content:""; position:absolute; top:0; bottom:0; left:13px; width:1px; background:#b9b8af; }}
    .timeline-tick {{ position:absolute; left:8px; top:var(--at); width:11px; min-height:0; height:1px; padding:0; border:0; border-radius:0; background:#92948d; transform:translateY(-50%); transition:width .12s, background .12s; }}
    .timeline-tick:nth-of-type(3n+1) {{ width:17px; }}
    .timeline-tick:hover {{ width:24px; background:var(--orange); }}
    .timeline-handle {{ position:absolute; z-index:2; left:7px; top:0; width:13px; height:13px; border:2px solid var(--paper); border-radius:50%; background:var(--orange); box-shadow:0 0 0 1px rgba(67,61,50,.24), 0 3px 9px rgba(40,36,29,.2); transform:translateY(-50%); pointer-events:none; }}
    .timeline-tooltip {{ position:absolute; left:25px; top:50%; min-width:72px; padding:6px 8px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink); font-size:12px; font-weight:700; white-space:nowrap; box-shadow:0 8px 20px rgba(45,42,34,.1); transform:translateY(-50%); }}
    .timeline-edge {{ position:absolute; left:0; width:72px; font-size:10px; color:#8b8d86; }}
    .timeline-edge.newest {{ top:0; }} .timeline-edge.oldest {{ bottom:0; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; }}
    article {{ overflow:hidden; scroll-margin-top:76px; border:1px solid var(--line); border-radius:18px; background:var(--card); transition:transform .18s ease, box-shadow .18s ease; }}
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
    @media (max-width:1279px) {{ .time-scrubber {{ display:none; }} .date-jump {{ display:block; }} }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width:600px) {{ main {{ width:min(100% - 24px,1180px); padding-top:14px; }} header {{ min-height:40px; }} h1 {{ font-size:24px; }} .grid {{ grid-template-columns:1fr; }} .toolbar {{ position:relative; display:grid; grid-template-columns:1fr auto; align-items:center; gap:9px; }} .search {{ grid-column:1/-1; grid-row:1; width:100%; margin:0; }} .filters {{ grid-column:1; grid-row:2; }} .rss {{ grid-column:2; grid-row:2; }} .date-jump {{ grid-column:1/-1; grid-row:3; width:100%; }} footer {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <aside class="time-scrubber" aria-label="按时间快速定位">
    <span class="timeline-edge newest">{newest_label}</span>
    <div class="timeline-track" role="slider" aria-label="视频时间" aria-orientation="vertical" tabindex="0">
      {timeline_ticks}<span class="timeline-handle"><span class="timeline-tooltip">{newest_label}</span></span>
    </div>
    <span class="timeline-edge oldest">{oldest_label}</span>
  </aside>
  <main>
    <header>
      <h1>视频收藏库</h1><span class="count">{len(library_items)} 条视频</span>
    </header>
    <nav class="toolbar" aria-label="视频筛选"><div class="filters"><button data-filter="all" aria-pressed="true">全部</button><button data-filter="podcast" aria-pressed="false">播客</button><button data-filter="channel" aria-pressed="false">Channel</button></div><select class="date-jump" aria-label="按日期跳转"><option value="">定位日期</option>{date_options}</select><input class="search" type="search" placeholder="搜索标题或作者" aria-label="搜索标题或作者"><a class="rss" href="{html.escape(feed_url, quote=True)}">RSS</a></nav>
    <section class="grid" aria-live="polite">{cards}</section>
    <p class="empty" id="no-results" hidden>没有符合条件的视频。</p>
    <footer><span>更新于 {updated}（北京时间）</span><span>原视频版权归原作者所有</span></footer>
  </main>
  <script>
    const cards=[...document.querySelectorAll('article[data-sources]')];
    const buttons=[...document.querySelectorAll('button[data-filter]')];
    const search=document.querySelector('.search');
    const dateJump=document.querySelector('.date-jump');
    const track=document.querySelector('.timeline-track');
    const handle=document.querySelector('.timeline-handle');
    const tooltip=document.querySelector('.timeline-tooltip');
    let active='all';
    let dragging=false, hovering=false, scrollFrame=0, programmaticUntil=0;
    const uniqueDates=[...new Set(cards.map(card=>card.dataset.date))].sort().reverse();
    const dateValue=date=>new Date(date+'T12:00:00+08:00').getTime();
    const maxDate=Math.max(...uniqueDates.map(dateValue)), minDate=Math.min(...uniqueDates.map(dateValue));
    const formatDate=date=>{{const [year,month,day]=date.split('-'); return `${{Number(month)}}月${{Number(day)}}日`;}};
    const visibleCards=()=>cards.filter(card=>!card.hidden);
    function nearestDate(target){{return uniqueDates.reduce((best,date)=>Math.abs(dateValue(date)-target)<Math.abs(dateValue(best)-target)?date:best,uniqueDates[0]);}}
    function setHandle(date){{if(!date||!handle)return; const ratio=maxDate===minDate?0:(maxDate-dateValue(date))/(maxDate-minDate); handle.style.top=`${{ratio*100}}%`; tooltip.textContent=formatDate(date); track.setAttribute('aria-valuetext',formatDate(date));}}
    function goToDate(date, smooth=true){{const candidates=visibleCards(); if(!candidates.length)return; const exact=candidates.find(card=>card.dataset.date===date); const target=exact||candidates.reduce((best,card)=>Math.abs(dateValue(card.dataset.date)-dateValue(date))<Math.abs(dateValue(best.dataset.date)-dateValue(date))?card:best,candidates[0]); programmaticUntil=Date.now()+(smooth?900:300); setHandle(target.dataset.date); target.scrollIntoView({{behavior:smooth?'smooth':'auto',block:'start'}});}}
    function scrub(event, navigate){{const rect=track.getBoundingClientRect(); const ratio=Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height)); const date=nearestDate(maxDate-ratio*(maxDate-minDate)); setHandle(date); if(navigate)goToDate(date,false);}}
    function syncFromScroll(){{if(hovering||dragging||Date.now()<programmaticUntil)return; const top=document.querySelector('.toolbar').getBoundingClientRect().bottom+8; const candidates=visibleCards().filter(card=>card.getBoundingClientRect().bottom>top); if(candidates.length)setHandle(candidates.reduce((best,card)=>Math.abs(card.getBoundingClientRect().top-top)<Math.abs(best.getBoundingClientRect().top-top)?card:best,candidates[0]).dataset.date);}}
    function apply(){{
      const q=search.value.trim().toLowerCase(); let shown=0;
      cards.forEach(card=>{{const source=card.dataset.sources.split(' '); const okSource=active==='all'||source.includes(active); const okText=!q||card.dataset.search.includes(q); card.hidden=!(okSource&&okText); if(!card.hidden) shown++;}});
      document.querySelector('#no-results').hidden=shown!==0;
      syncFromScroll();
    }}
    buttons.forEach(button=>button.addEventListener('click',()=>{{active=button.dataset.filter; buttons.forEach(item=>item.setAttribute('aria-pressed',String(item===button))); apply();}}));
    search.addEventListener('input',apply);
    dateJump.addEventListener('change',()=>{{if(dateJump.value)goToDate(dateJump.value);}});
    document.querySelectorAll('.timeline-tick').forEach(tick=>tick.addEventListener('click',()=>goToDate(tick.dataset.date)));
    track.addEventListener('pointerenter',()=>{{hovering=true;}});
    track.addEventListener('pointerleave',()=>{{if(!dragging){{hovering=false;syncFromScroll();}}}});
    track.addEventListener('pointermove',event=>scrub(event,dragging));
    track.addEventListener('pointerdown',event=>{{dragging=true;hovering=true;track.setPointerCapture(event.pointerId);scrub(event,true);}});
    track.addEventListener('pointerup',event=>{{dragging=false;hovering=false;track.releasePointerCapture(event.pointerId);syncFromScroll();}});
    track.addEventListener('keydown',event=>{{const current=tooltip.textContent; const index=uniqueDates.findIndex(date=>formatDate(date)===current); if(event.key==='ArrowDown'&&index<uniqueDates.length-1){{event.preventDefault();goToDate(uniqueDates[index+1]);}} if(event.key==='ArrowUp'&&index>0){{event.preventDefault();goToDate(uniqueDates[index-1]);}}}});
    window.addEventListener('scroll',()=>{{cancelAnimationFrame(scrollFrame);scrollFrame=requestAnimationFrame(syncFromScroll);}},{{passive:true}});
    setHandle(uniqueDates[0]);
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
        f'<article data-sources="{" ".join(sources)}" data-date="{date_text}" data-search="{searchable}">'
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
