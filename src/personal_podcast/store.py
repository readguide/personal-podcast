import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from personal_podcast.errors import EpisodeNotFoundError
from personal_podcast.models import Episode


def _datetime_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _text_to_datetime(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _path_to_text(value: Optional[Path]) -> Optional[str]:
    return str(value) if value else None


class EpisodeStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    author TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    audio_path TEXT NOT NULL,
                    audio_bytes INTEGER NOT NULL,
                    audio_mime TEXT NOT NULL,
                    source_path TEXT,
                    source_cleanup_after TEXT,
                    artwork_path TEXT,
                    public_audio_url TEXT,
                    release_tag TEXT,
                    archived_at TEXT,
                    deleted_at TEXT,
                    source_cleaned_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS episodes_imported_at ON episodes(imported_at DESC)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path))
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, episode: Episode) -> None:
        values = (
            episode.episode_id,
            episode.source_url,
            episode.title,
            episode.description,
            episode.author,
            _datetime_to_text(episode.imported_at),
            episode.duration_seconds,
            str(episode.audio_path),
            episode.audio_bytes,
            episode.audio_mime,
            _path_to_text(episode.source_path),
            _datetime_to_text(episode.source_cleanup_after),
            _path_to_text(episode.artwork_path),
            episode.public_audio_url,
            episode.release_tag,
            _datetime_to_text(episode.archived_at),
            _datetime_to_text(episode.deleted_at),
            _datetime_to_text(episode.source_cleaned_at),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO episodes (
                    episode_id, source_url, title, description, author, imported_at,
                    duration_seconds, audio_path, audio_bytes, audio_mime, source_path,
                    source_cleanup_after, artwork_path, public_audio_url, release_tag,
                    archived_at, deleted_at, source_cleaned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    title=excluded.title,
                    description=excluded.description,
                    author=excluded.author,
                    imported_at=excluded.imported_at,
                    duration_seconds=excluded.duration_seconds,
                    audio_path=excluded.audio_path,
                    audio_bytes=excluded.audio_bytes,
                    audio_mime=excluded.audio_mime,
                    source_path=excluded.source_path,
                    source_cleanup_after=excluded.source_cleanup_after,
                    artwork_path=excluded.artwork_path,
                    public_audio_url=excluded.public_audio_url,
                    release_tag=excluded.release_tag,
                    archived_at=excluded.archived_at,
                    deleted_at=excluded.deleted_at,
                    source_cleaned_at=excluded.source_cleaned_at
                """,
                values,
            )

    def get(self, episode_id: str) -> Episode:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
        if row is None:
            raise EpisodeNotFoundError(f"未找到节目: {episode_id}")
        return self._episode_from_row(row)

    def find_by_source_url(self, source_url: str) -> Optional[Episode]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE source_url = ?", (source_url,)
            ).fetchone()
        return self._episode_from_row(row) if row else None

    def list(self, include_deleted: bool = False) -> List[Episode]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM episodes {where} ORDER BY imported_at DESC"
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def list_visible(self) -> List[Episode]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM episodes
                WHERE public_audio_url IS NOT NULL
                  AND archived_at IS NULL
                  AND deleted_at IS NULL
                ORDER BY imported_at DESC
                """
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def list_cleanable(self, now: datetime) -> List[Episode]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM episodes
                WHERE source_path IS NOT NULL
                  AND source_cleaned_at IS NULL
                  AND source_cleanup_after <= ?
                ORDER BY source_cleanup_after ASC
                """,
                (_datetime_to_text(now),),
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def set_archived(self, episode_id: str, archived_at: Optional[datetime]) -> Episode:
        self.get(episode_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE episodes SET archived_at = ? WHERE episode_id = ?",
                (_datetime_to_text(archived_at), episode_id),
            )
        return self.get(episode_id)

    def set_deleted(self, episode_id: str, deleted_at: datetime) -> Episode:
        self.get(episode_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE episodes SET deleted_at = ? WHERE episode_id = ?",
                (_datetime_to_text(deleted_at), episode_id),
            )
        return self.get(episode_id)

    def set_published(self, episode_id: str, public_url: str, release_tag: str) -> Episode:
        self.get(episode_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE episodes
                SET public_audio_url = ?, release_tag = ?
                WHERE episode_id = ?
                """,
                (public_url, release_tag, episode_id),
            )
        return self.get(episode_id)

    def clear_published(self, episode_id: str) -> Episode:
        self.get(episode_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE episodes
                SET public_audio_url = NULL, release_tag = NULL
                WHERE episode_id = ?
                """,
                (episode_id,),
            )
        return self.get(episode_id)

    def mark_source_cleaned(self, episode_id: str, cleaned_at: datetime) -> Episode:
        self.get(episode_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE episodes
                SET source_cleaned_at = ?, source_path = NULL
                WHERE episode_id = ?
                """,
                (_datetime_to_text(cleaned_at), episode_id),
            )
        return self.get(episode_id)

    @staticmethod
    def _episode_from_row(row: sqlite3.Row) -> Episode:
        return Episode(
            episode_id=row["episode_id"],
            source_url=row["source_url"],
            title=row["title"],
            description=row["description"],
            author=row["author"],
            imported_at=_text_to_datetime(row["imported_at"]),
            duration_seconds=float(row["duration_seconds"]),
            audio_path=Path(row["audio_path"]),
            audio_bytes=int(row["audio_bytes"]),
            audio_mime=row["audio_mime"],
            source_path=Path(row["source_path"]) if row["source_path"] else None,
            source_cleanup_after=_text_to_datetime(row["source_cleanup_after"]),
            artwork_path=Path(row["artwork_path"]) if row["artwork_path"] else None,
            public_audio_url=row["public_audio_url"],
            release_tag=row["release_tag"],
            archived_at=_text_to_datetime(row["archived_at"]),
            deleted_at=_text_to_datetime(row["deleted_at"]),
            source_cleaned_at=_text_to_datetime(row["source_cleaned_at"]),
        )
