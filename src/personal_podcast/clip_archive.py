import fcntl
import hashlib
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set
from urllib.parse import urlsplit

from personal_podcast.errors import PersonalPodcastError
from personal_podcast.identifiers import canonicalize_url


LOGGER = logging.getLogger(__name__)
DATE_FORMAT = "%Y年%m月%d日 %H:%M"
TERMINAL_STATUSES = {"succeeded", "existing", "skipped-wechat", "skipped-non-media"}


@dataclass(frozen=True)
class ClipRecord:
    title: str
    source_url: str
    saved_at: datetime
    position: int

    @property
    def key(self) -> str:
        value = f"{self.source_url}\n{self.saved_at.isoformat(timespec='minutes')}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @property
    def saved_at_text(self) -> str:
        return self.saved_at.strftime(DATE_FORMAT)


@dataclass
class ClipProcessSummary:
    candidates: int = 0
    imported: List[str] = field(default_factory=list)
    existing: int = 0
    skipped_wechat: int = 0
    skipped_non_media: int = 0
    failed: int = 0
    initialized: bool = False
    baseline: Optional[ClipRecord] = None


def parse_clip_archive(text: str) -> List[ClipRecord]:
    records: List[ClipRecord] = []
    for position, block in enumerate(text.split("===")):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        source = _field(lines, "来源")
        saved_at_text = _field(lines, "保存日期")
        if not source or not saved_at_text:
            LOGGER.warning("跳过格式不完整的剪藏记录（位置 %s）", position)
            continue
        try:
            url = canonicalize_url(source)
            saved_at = datetime.strptime(saved_at_text, DATE_FORMAT)
        except ValueError as error:
            LOGGER.warning("跳过无效剪藏记录（位置 %s）: %s", position, error)
            continue
        title = next(
            (line for line in lines if not _is_field(line, "来源", "保存日期")),
            "未命名剪藏",
        )
        records.append(ClipRecord(title, url, saved_at, position))
    return records


def read_clip_archive(path: Path) -> List[ClipRecord]:
    if not path.exists():
        raise PersonalPodcastError(f"剪藏目录不存在: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except PermissionError as error:
        raise PersonalPodcastError(
            "无法读取剪藏目录，请在 macOS 的“隐私与安全性 → 文件与文件夹”中允许 iCloud Drive"
        ) from error
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
        else:
            possible = [record for record in records if record.saved_at > baseline.saved_at]

        entries = state.get("entries", {})
        if not isinstance(entries, dict):
            raise PersonalPodcastError("剪藏处理状态中的 entries 无效")
        seen: Set[str] = set()
        pending: List[ClipRecord] = []
        for record in sorted(possible, key=lambda item: (item.saved_at, item.position)):
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


def _field(lines: List[str], name: str) -> Optional[str]:
    for line in lines:
        for separator in ("：", ":"):
            prefix = f"{name}{separator}"
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
    return None


def _is_field(line: str, *names: str) -> bool:
    return any(
        line.startswith(f"{name}{separator}")
        for name in names
        for separator in ("：", ":")
    )


def _record_payload(record: ClipRecord) -> Dict[str, object]:
    return {
        "source_url": record.source_url,
        "saved_at": record.saved_at.isoformat(timespec="minutes"),
    }


def _baseline_from_state(state: Dict[str, object]) -> Optional[ClipRecord]:
    baseline = state.get("baseline")
    if not isinstance(baseline, dict):
        return None
    source_url = baseline.get("source_url")
    saved_at = baseline.get("saved_at")
    if not isinstance(source_url, str) or not isinstance(saved_at, str):
        raise PersonalPodcastError("剪藏处理状态中的 baseline 无效")
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
