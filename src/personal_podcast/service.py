import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from personal_podcast.artwork import download_artwork
from personal_podcast.config import AppConfig
from personal_podcast.downloader import DownloadManager, MetadataReader
from personal_podcast.errors import PersonalPodcastError
from personal_podcast.identifiers import canonicalize_url, episode_id_for
from personal_podcast.inbox import VideoLinkClassifier, latest_link
from personal_podcast.media import MediaProcessor
from personal_podcast.models import Episode
from personal_podcast.publisher import GitHubReleasePublisher, GitSitePublisher
from personal_podcast.site import SiteGenerator
from personal_podcast.store import EpisodeStore


LOGGER = logging.getLogger(__name__)


def build_episode_description(
    original: str,
    *,
    title: str,
    author: str,
    source_url: str,
) -> str:
    body = original.strip()
    if not body or body == f"原始来源：{source_url}":
        body = (
            f"本期为《{title}》的音频收听版，保留原视频内容与原作者声音，"
            "便于在播客客户端中收听。"
        )
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
            / str(imported_at.year)
            / episode_id
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
        final_directory = self.config.storage.final_audio_dir / str(imported_at.year)
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
        published = self.store.set_published(episode_id, url, tag)
        self.generate_site()
        return published

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
        destination = self.config.storage.transcripts_dir / str(episode.imported_at.year)
        transcript_path = self.publisher.download_transcript(
            episode.release_tag, episode.episode_id, destination
        )
        updated = self.store.set_transcript_path(episode_id, transcript_path)
        self.generate_site()
        return updated

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
