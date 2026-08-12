import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from personal_podcast.artwork import download_artwork
from personal_podcast.config import AppConfig
from personal_podcast.downloader import DownloadManager, MetadataReader
from personal_podcast.errors import PersonalPodcastError, PublishError
from personal_podcast.identifiers import canonicalize_url, episode_id_for
from personal_podcast.inbox import VideoLinkClassifier, latest_link
from personal_podcast.media import MediaProcessor
from personal_podcast.models import Episode, EpisodeMetadata
from personal_podcast.publisher import GitHubReleasePublisher, GitSitePublisher
from personal_podcast.site import SiteGenerator
from personal_podcast.store import EpisodeStore
from personal_podcast.transcript import format_transcript, transcript_filename


LOGGER = logging.getLogger(__name__)


def _readable_source_folder(
    imported_at: datetime,
    metadata: Optional[EpisodeMetadata],
    episode_id: str,
) -> str:
    """源文件目录名: 日期-标题(可读, 不再用 episode_id 裸 ID)。

    2026-08-12 用户要求: Source Media 下的目录要能直接看出内容。
    格式: YYYY-MM-DD-标题(去特殊字符, 截断 60 字); 标题缺失时回退 episode_id。
    """
    import re as _re
    date_part = imported_at.strftime("%Y-%m-%d")
    raw_title = (metadata.title if metadata and metadata.title else "").strip()
    if not raw_title:
        raw_title = episode_id
    safe = _re.sub(r"[\\/:*?\"<>|#\s]+", "-", raw_title).strip("-")
    safe = safe[:60].rstrip("-") or episode_id
    return f"{date_part}-{safe}"


def build_episode_description(
    original: str,
    *,
    title: str,
    author: str,
    source_url: str,
) -> str:
    body = original.strip()
    if not body or body == f"原始来源：{source_url}":
        body = f"{title}"
    details = []
    if author and f"作者：{author}" not in body:
        details.append(f"作者：{author}")
    if source_url not in body:
        details.append(f"原始来源：{source_url}")
    details_text = "\n".join(details)
    return f"{body}\n\n{details_text}" if details_text else body


class PersonalPodcastService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.store = EpisodeStore(config.storage.database_path)
        self.metadata = MetadataReader(config.download)
        self.downloads = DownloadManager(config.download)
        self.media = MediaProcessor(config.audio)
        self.link_classifier = VideoLinkClassifier(config.download)
        self.publisher = GitHubReleasePublisher(config.github)
        self.site_publisher = GitSitePublisher(config.github)
        self.site = SiteGenerator(config)

    def initialize(self) -> int:
        for directory in (
            self.config.storage.final_audio_dir,
            self.config.storage.source_media_dir,
            self.config.storage.artwork_dir / "Episodes",
            self.config.storage.transcripts_dir,
            self.config.storage.link_inbox_path.parent,
            self.config.storage.temp_dir,
            self.config.storage.exports_dir,
            self.config.storage.app_dir / "logs",
            self.config.github.site_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.config.storage.link_inbox_path.touch(exist_ok=True)
        self.store.initialize()
        return self.generate_site()

    def add(self, source_url: str, publish: bool = False) -> Episode:
        url = canonicalize_url(source_url)
        existing = self.store.find_by_source_url(url)
        if existing:
            raise PersonalPodcastError(f"这个链接已导入，节目编号是 {existing.episode_id}")

        imported_at = datetime.now(timezone.utc)
        metadata = self.metadata.read(url)
        episode_id = episode_id_for(url, metadata)
        source_directory = (
            self.config.storage.source_media_dir
            / _readable_source_folder(imported_at, metadata, episode_id)
        )
        download = self.downloads.download(url, source_directory, episode_id)
        source_info = self.media.probe(download.path)

        fallback_title = download.path.stem or source_info.tags.get("title") or episode_id
        title = (metadata.title if metadata else "") or fallback_title
        author = (metadata.author if metadata else "") or self.config.podcast.author
        description = build_episode_description(
            metadata.description if metadata else "",
            title=title,
            author=author,
            source_url=url,
        )
        artwork = download_artwork(
            metadata.thumbnail_url if metadata else None,
            self.config.storage.artwork_dir / "Episodes" / episode_id,
        )
        embedded_artwork = artwork or self.site.ensure_local_cover()
        final_directory = self.config.storage.final_audio_dir
        audio = self.media.process(
            source_path=download.path,
            output_directory=final_directory,
            episode_id=episode_id,
            title=title,
            author=author,
            album=self.config.podcast.name,
            description=description,
            artwork_path=embedded_artwork,
        )

        retained_source: Optional[Path] = download.path
        if (
            not self.config.storage.keep_redundant_source_audio
            and audio.action == "remux"
            and not source_info.has_video
        ):
            download.path.unlink(missing_ok=True)
            retained_source = None
            self._remove_empty_parent(source_directory)

        episode = Episode(
            episode_id=episode_id,
            source_url=url,
            title=title,
            description=description,
            author=author,
            imported_at=imported_at,
            duration_seconds=source_info.duration_seconds,
            audio_path=audio.path,
            audio_bytes=audio.path.stat().st_size,
            audio_mime=audio.mime_type,
            source_path=retained_source,
            source_cleanup_after=(
                imported_at + timedelta(days=self.config.storage.source_retention_days)
                if retained_source
                else None
            ),
            artwork_path=artwork,
        )
        self.store.save(episode)
        if publish:
            episode = self.publish(episode_id)
        else:
            self.generate_site()
        LOGGER.info("节目已导入: %s (%s)", episode.title, episode.episode_id)
        return episode

    def publish(self, episode_id: str) -> Episode:
        episode = self.store.get(episode_id)
        tag, url = self.publisher.publish(episode)
        episode.audio_bytes = episode.audio_path.stat().st_size
        self.store.save(episode)
        published = self.store.set_published(episode_id, url, tag)
        self.generate_site()
        return published

    def refresh_audio_urls(self) -> int:
        updated = 0
        for episode in self.store.list(include_deleted=True):
            if not episode.public_audio_url or not episode.release_tag:
                continue
            url = self.publisher.public_url(episode, episode.release_tag)
            self.store.set_published(episode.episode_id, url, episode.release_tag)
            updated += 1
        self.generate_site()
        return updated

    def add_latest(self, publish: bool = False) -> Tuple[str, Optional[Episode]]:
        url = latest_link(self.config.storage.link_inbox_path)
        if not self.link_classifier.is_video(url):
            return url, None
        return url, self.add(url, publish=publish)

    def archive(self, episode_id: str) -> Episode:
        episode = self.store.set_archived(episode_id, datetime.now(timezone.utc))
        self.generate_site()
        return episode

    def restore(self, episode_id: str) -> Episode:
        episode = self.store.set_archived(episode_id, None)
        self.generate_site()
        return episode

    def delete(
        self,
        episode_id: str,
        *,
        delete_source: bool = False,
        delete_final: bool = False,
        delete_release: bool = False,
    ) -> Episode:
        episode = self.store.get(episode_id)
        if delete_release and episode.release_tag:
            self.publisher.delete(episode.release_tag)
            episode = self.store.clear_published(episode_id)
        if delete_source and episode.source_path:
            episode.source_path.unlink(missing_ok=True)
            self.store.mark_source_cleaned(episode_id, datetime.now(timezone.utc))
        if delete_final:
            episode.audio_path.unlink(missing_ok=True)
        deleted = self.store.set_deleted(episode_id, datetime.now(timezone.utc))
        self.generate_site()
        return deleted

    def cleanable(self) -> List[Episode]:
        return self.store.list_cleanable(datetime.now(timezone.utc))

    def cleanup_sources(self) -> List[Episode]:
        cleaned: List[Episode] = []
        now = datetime.now(timezone.utc)
        for episode in self.store.list_cleanable(now):
            if episode.source_path:
                episode.source_path.unlink(missing_ok=True)
                self._remove_empty_parent(episode.source_path.parent)
            cleaned.append(self.store.mark_source_cleaned(episode.episode_id, now))
        return cleaned

    def import_transcript(self, episode_id: str) -> Episode:
        episode = self.store.get(episode_id)
        if not episode.release_tag:
            raise PersonalPodcastError("节目尚未发布，无法从 GitHub 下载转写稿")
        destination = self.config.storage.transcripts_dir
        download_directory = (
            self.config.storage.temp_dir / "Transcripts" / episode.episode_id
        )
        raw_path = self.publisher.download_transcript(
            episode.release_tag, episode.episode_id, download_directory
        )
        destination.mkdir(parents=True, exist_ok=True)
        transcript_path = destination / transcript_filename(episode)
        raw_text = raw_path.read_text(encoding="utf-8")
        transcript_path.write_text(
            format_transcript(episode, raw_text), encoding="utf-8"
        )
        updated = self.store.set_transcript_path(episode_id, transcript_path)
        raw_path.unlink(missing_ok=True)
        self._remove_empty_parent(download_directory)
        previous_path = episode.transcript_path
        if (
            previous_path
            and previous_path != transcript_path
            and previous_path.parent == destination
        ):
            previous_path.unlink(missing_ok=True)
        self._sync_transcript_to_kb(episode, transcript_path)
        self.generate_site()
        return updated

    def _sync_transcript_to_kb(self, episode: Episode, transcript_path: Path) -> None:
        """转写完成后,自动同步一份简体 markdown 到 Obsidian 知识库播客文件夹
        (简介不再包含转写全文,转写文本独立存放于知识库)"""
        try:
            import shutil
            import re as _re
            from datetime import datetime as _dt
            try:
                import opencc
            except ImportError:
                LOGGER.warning("opencc 不可用,转录稿同步跳过(繁体未转换)")
                return
            cc = opencc.OpenCC("t2s")
            kb_dir = (
                Path.home()
                / "Library/Mobile Documents/com~apple~CloudDocs/00en/en/Obsidian/知识库/3-Resources/播客"
            )
            kb_dir.mkdir(parents=True, exist_ok=True)
            raw = transcript_path.read_text(encoding="utf-8")
            title = cc.convert(episode.title.strip())
            author = cc.convert(episode.author.strip() or "")
            body = raw.split("音频文本：", 1)[-1].split("音频文本:", 1)[-1].strip()
            body = cc.convert(body)
            if not body:
                return
            now = _dt.now().strftime("%Y-%m-%dT%H:%M:%S")
            fname = _re.sub(r"[\\/:*?\"<>|#]", " ", title).strip()[:80] or "未命名"
            md = [
                "---",
                f"created: {now}",
                f"updated: {now}",
                f"source: {episode.source_url}",
            ]
            if author:
                md.append(f"author: {author}")
            md += ["tags:", "  - 播客", "  - 转录稿", "---", "", f"# {title}", ""]
            if author:
                md.append(f"> **作者**: {author} | [原始来源]({episode.source_url})")
                md.append("")
            md += ["## 全文转录", "", body, ""]
            out = kb_dir / f"{fname}.md"
            for attempt in range(5):
                try:
                    out.write_text("\n".join(md), encoding="utf-8")
                    break
                except OSError:
                    import time
                    time.sleep(3)
            LOGGER.info("转录稿已同步知识库: %s", out.name)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("转录稿同步知识库失败: %s", error)

    def import_ready_transcripts(self) -> List[Episode]:
        imported: List[Episode] = []
        for episode in self.store.list():
            if not episode.release_tag:
                continue
            if episode.transcript_path and episode.transcript_path.exists():
                continue
            try:
                imported.append(self.import_transcript(episode.episode_id))
            except PublishError as error:
                LOGGER.info("转写稿尚未可用，稍后重试: %s (%s)", episode.episode_id, error)
        return imported

    def generate_site(self) -> int:
        return self.site.generate(self.store.list(include_deleted=True))

    def sync_site(self, message: str = "Update podcast feed") -> bool:
        self.generate_site()
        return self.site_publisher.sync(message)

    def list_episodes(self, include_deleted: bool = False) -> List[Episode]:
        return self.store.list(include_deleted=include_deleted)

    @staticmethod
    def _remove_empty_parent(directory: Path) -> None:
        try:
            directory.rmdir()
        except OSError:
            pass
