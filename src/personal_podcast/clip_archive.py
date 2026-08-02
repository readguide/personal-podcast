import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set
from urllib.parse import urlsplit

from personal_podcast.errors import PersonalPodcastError
from personal_podcast.identifiers import canonicalize_url
from personal_podcast.inbox import TRAILING_PUNCTUATION, URL_PATTERN, video_platform_for


LOGGER = logging.getLogger(__name__)
DATE_FORMAT = "%Y年%m月%d日 %H:%M"
DATE_PATTERNS = (
    (re.compile(r"\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}"), DATE_FORMAT),
    (re.compile(r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}"), "%Y-%m-%d %H:%M"),
)
TERMINAL_STATUSES = {
    "succeeded",
    "existing",
    "skipped-wechat",
    "skipped-unsupported",
    "skipped-non-media",
    "failed",
}


@dataclass(frozen=True)
class ClipRecord:
    title: str
    source_url: str
    saved_at: Optional[datetime]
    position: int

    @property
    def key(self) -> str:
        saved_at = self.saved_at.isoformat(timespec="minutes") if self.saved_at else ""
        value = f"{self.source_url}\n{saved_at}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @property
    def saved_at_text(self) -> str:
        return self.saved_at.strftime(DATE_FORMAT) if self.saved_at else "未提供日期"


@dataclass
class ClipProcessSummary:
    candidates: int = 0
    imported: List[str] = field(default_factory=list)
    existing: int = 0
    skipped_wechat: int = 0
    skipped_unsupported: int = 0
    skipped_non_media: int = 0
    failed: int = 0
    initialized: bool = False
    baseline: Optional[ClipRecord] = None


def parse_clip_archive(text: str) -> List[ClipRecord]:
    records: List[ClipRecord] = []
    position = 0
    for block in text.split("==="):
        lines = [line.strip() for line in block.splitlines()]
        if not lines:
            continue
        for line_index, line in enumerate(lines):
            for match in URL_PATTERN.finditer(line):
                source = match.group(0).rstrip(TRAILING_PUNCTUATION)
                try:
                    url = canonicalize_url(source)
                except ValueError as error:
                    LOGGER.warning("跳过无效链接（位置 %s）: %s", position, error)
                    continue
                title = _title_near_url(lines, line_index, match.start())
                saved_at = _date_near_url(lines, line_index)
                records.append(ClipRecord(title, url, saved_at, position))
                position += 1
    return records


def read_clip_archive(path: Path) -> List[ClipRecord]:
    if not path.exists():
        raise PersonalPodcastError(f"剪藏目录不存在: {path}")
    text: Optional[str] = None
    for attempt in range(5):
        try:
            text = path.read_text(encoding="utf-8-sig")
            break
        except PermissionError as error:
            raise PersonalPodcastError(
                "无法读取剪藏目录，请在 macOS 的“隐私与安全性 → 文件与文件夹”中允许 iCloud Drive"
            ) from error
        except OSError as error:
            if error.errno not in {errno.EAGAIN, errno.EBUSY} or attempt == 4:
                raise
            time.sleep(2)
    if text is None:
        raise PersonalPodcastError(f"无法读取剪藏目录: {path}")
    records = parse_clip_archive(text)
    if not records:
        raise PersonalPodcastError(f"剪藏目录中没有格式有效的记录: {path}")
    return records


def is_wechat_article(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == "mp.weixin.qq.com"


class ClipArchiveStateStore:
    def __init__(self, path: Path, source_path: Path):
        self.path = path
        self.source_path = source_path

    def load(self) -> Dict[str, object]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PersonalPodcastError(f"剪藏处理状态损坏: {self.path}") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise PersonalPodcastError(f"不支持的剪藏处理状态: {self.path}")
        recorded_source = payload.get("source_path")
        if recorded_source and recorded_source != str(self.source_path):
            raise PersonalPodcastError("剪藏状态属于另一个来源文件，拒绝混用")
        payload.setdefault("entries", {})
        return payload

    def save(self, state: Dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state["version"] = 1
        state["source_path"] = str(self.source_path)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def initialize(
        self, records: List[ClipRecord], baseline: Optional[ClipRecord] = None
    ) -> ClipRecord:
        state = self.load()
        current = _baseline_from_state(state)
        if current:
            return current
        selected = baseline or records[-1]
        state["baseline"] = _record_payload(selected)
        state["initialized_at"] = datetime.now().isoformat(timespec="seconds")
        self.save(state)
        return selected

    def candidates(
        self, records: List[ClipRecord], state: Dict[str, object]
    ) -> List[ClipRecord]:
        baseline = _baseline_from_state(state)
        if baseline is None:
            return []
        baseline_index: Optional[int] = None
        for index, record in enumerate(records):
            if record.key == baseline.key:
                baseline_index = index
        if baseline_index is not None:
            possible = records[baseline_index + 1 :]
        elif baseline.saved_at is None:
            LOGGER.warning("未找到无日期存量基线，为避免回溯处理，本次不导入")
            possible = []
        else:
            possible = [
                record
                for record in records
                if record.saved_at and record.saved_at > baseline.saved_at
            ]

        entries = state.get("entries", {})
        if not isinstance(entries, dict):
            raise PersonalPodcastError("剪藏处理状态中的 entries 无效")
        seen: Set[str] = set()
        pending: List[ClipRecord] = []
        for record in possible:
            if record.key in seen:
                continue
            seen.add(record.key)
            entry = entries.get(record.key, {})
            status = entry.get("status") if isinstance(entry, dict) else None
            if status not in TERMINAL_STATUSES:
                pending.append(record)
        return pending

    def record(
        self,
        state: Dict[str, object],
        item: ClipRecord,
        status: str,
        *,
        episode_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        entries = state.setdefault("entries", {})
        if not isinstance(entries, dict):
            raise PersonalPodcastError("剪藏处理状态中的 entries 无效")
        payload = _record_payload(item)
        payload.update(
            {
                "title": item.title,
                "status": status,
                "episode_id": episode_id,
                "error": error[:500] if error else None,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        entries[item.key] = payload
        self.save(state)

    def _empty(self) -> Dict[str, object]:
        return {
            "version": 1,
            "source_path": str(self.source_path),
            "baseline": None,
            "entries": {},
        }


class ClipArchiveProcessor:
    def __init__(self, service: object, state_store: ClipArchiveStateStore):
        self.service = service
        self.state_store = state_store

    def initialize(
        self, records: List[ClipRecord], baseline: Optional[ClipRecord] = None
    ) -> ClipProcessSummary:
        selected = self.state_store.initialize(records, baseline=baseline)
        return ClipProcessSummary(initialized=True, baseline=selected)

    def process(
        self,
        records: List[ClipRecord],
        *,
        publish: bool,
        sync_site: bool,
    ) -> ClipProcessSummary:
        state = self.state_store.load()
        if _baseline_from_state(state) is None:
            return self.initialize(records)

        summary = ClipProcessSummary()
        candidates = self.state_store.candidates(records, state)
        summary.candidates = len(candidates)
        awaiting_sync: List[ClipRecord] = []

        for item in candidates:
            entry = _entry(state, item.key)
            if entry.get("status") == "published":
                awaiting_sync.append(item)
                continue
            if is_wechat_article(item.source_url):
                self.state_store.record(state, item, "skipped-wechat")
                summary.skipped_wechat += 1
                continue
            if video_platform_for(item.source_url) is None:
                self.state_store.record(state, item, "skipped-unsupported")
                summary.skipped_unsupported += 1
                continue
            try:
                existing = self.service.store.find_by_source_url(item.source_url)
                if existing:
                    if publish and not existing.public_audio_url:
                        existing = self.service.publish(existing.episode_id)
                        self.state_store.record(
                            state, item, "published", episode_id=existing.episode_id
                        )
                        awaiting_sync.append(item)
                    else:
                        self.state_store.record(
                            state, item, "existing", episode_id=existing.episode_id
                        )
                        summary.existing += 1
                    continue
                if not self.service.link_classifier.is_video(item.source_url):
                    self.state_store.record(state, item, "skipped-non-media")
                    summary.skipped_non_media += 1
                    continue
                self.state_store.record(state, item, "running")
                episode = self.service.add(item.source_url, publish=publish)
                status = "published" if publish and sync_site else "succeeded"
                self.state_store.record(
                    state, item, status, episode_id=episode.episode_id
                )
                summary.imported.append(episode.episode_id)
                if status == "published":
                    awaiting_sync.append(item)
            except Exception as error:
                LOGGER.exception("处理剪藏记录失败: %s", item.title)
                self.state_store.record(state, item, "failed", error=str(error))
                summary.failed += 1

        if awaiting_sync and sync_site:
            self.service.sync_site(
                f"Publish {len(awaiting_sync)} podcast episode(s) from clip archive"
            )
            for item in awaiting_sync:
                episode_id = _entry(state, item.key).get("episode_id")
                self.state_store.record(
                    state,
                    item,
                    "succeeded",
                    episode_id=str(episode_id) if episode_id else None,
                )
        return summary


@contextmanager
def clip_archive_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _title_near_url(lines: List[str], line_index: int, url_start: int) -> str:
    same_line = lines[line_index][:url_start].strip(" ：:")
    if same_line and same_line not in {"来源", "链接", "URL", "url"}:
        return same_line
    for index in range(line_index - 1, max(-1, line_index - 6), -1):
        candidate = lines[index].strip()
        if not candidate or URL_PATTERN.search(candidate) or _parse_date(candidate):
            continue
        if candidate == "===" or candidate.startswith(("来源：", "来源:")):
            continue
        return candidate
    return "未命名剪藏"


def _date_near_url(lines: List[str], line_index: int) -> Optional[datetime]:
    indexes = list(range(line_index, min(len(lines), line_index + 5)))
    indexes.extend(range(line_index - 1, max(-1, line_index - 3), -1))
    for index in indexes:
        parsed = _parse_date(lines[index])
        if parsed:
            return parsed
    return None


def _parse_date(value: str) -> Optional[datetime]:
    for pattern, date_format in DATE_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        try:
            return datetime.strptime(match.group(0), date_format)
        except ValueError:
            continue
    return None


def _record_payload(record: ClipRecord) -> Dict[str, object]:
    return {
        "source_url": record.source_url,
        "saved_at": (
            record.saved_at.isoformat(timespec="minutes") if record.saved_at else None
        ),
    }


def _baseline_from_state(state: Dict[str, object]) -> Optional[ClipRecord]:
    baseline = state.get("baseline")
    if not isinstance(baseline, dict):
        return None
    source_url = baseline.get("source_url")
    saved_at = baseline.get("saved_at")
    if not isinstance(source_url, str) or (
        saved_at is not None and not isinstance(saved_at, str)
    ):
        raise PersonalPodcastError("剪藏处理状态中的 baseline 无效")
    parsed: Optional[datetime] = None
    if saved_at:
        try:
            parsed = datetime.fromisoformat(saved_at)
        except ValueError as error:
            raise PersonalPodcastError("剪藏处理状态中的基线日期无效") from error
    return ClipRecord("基线", source_url, parsed, -1)


def _entry(state: Dict[str, object], key: str) -> Dict[str, object]:
    entries = state.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    entry = entries.get(key, {})
    return entry if isinstance(entry, dict) else {}
