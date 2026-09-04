import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from personal_podcast.errors import ConfigError


DEFAULT_ROOT = Path("/Users/en/Downloads/en/Personal Podcast")
DEFAULT_APP_DIR = DEFAULT_ROOT / "Application Data"
DEFAULT_CONFIG_PATH = DEFAULT_APP_DIR / "config.toml"


@dataclass(frozen=True)
class PodcastConfig:
    name: str = "收听库"
    author: str = "en"
    description: str = (
        "将公开视频转换为原始音频进行收听的个人播客库。保留原作者声音、标题、封面、简介和原始来源，"
        "不进行 AI 总结、改写或配音。"
    )
    language: str = "zh-CN"
    explicit: bool = False


@dataclass(frozen=True)
class StorageConfig:
    root: Path = DEFAULT_ROOT
    app_dir: Path = DEFAULT_APP_DIR
    source_retention_days: int = 90
    keep_final_audio: bool = True
    keep_redundant_source_audio: bool = False

    @property
    def database_path(self) -> Path:
        return self.app_dir / "podcast.db"

    @property
    def logs_dir(self) -> Path:
        return self.app_dir / "logs"

    @property
    def final_audio_dir(self) -> Path:
        return self.root / "Final Audio"

    @property
    def source_media_dir(self) -> Path:
        return self.root / "Source Media"

    @property
    def artwork_dir(self) -> Path:
        return self.root / "Artwork"

    @property
    def transcripts_dir(self) -> Path:
        return self.root / "Transcripts"

    @property
    def link_inbox_path(self) -> Path:
        return self.root / "Inbox" / "links.txt"

    @property
    def clip_archive_state_path(self) -> Path:
        return self.app_dir / "clip-archive-state.json"

    @property
    def clip_archive_lock_path(self) -> Path:
        return self.app_dir / "clip-archive.lock"

    @property
    def temp_dir(self) -> Path:
        return self.root / "Temp"

    @property
    def exports_dir(self) -> Path:
        return self.root / "Exports"


@dataclass(frozen=True)
class DownloadConfig:
    order: List[str] = field(default_factory=lambda: ["downie"])
    downie_app: str = "Downie 4"
    downie_timeout_seconds: int = 3600
    downie_poll_seconds: float = 2.0
    downie_fallback_directory: Path = field(
        default_factory=lambda: Path.home() / "Downloads"
    )
    yt_dlp_command: str = "yt-dlp"


@dataclass(frozen=True)
class AudioConfig:
    mode: str = "balanced"
    aac_bitrate_kbps: int = 128
    ffmpeg_command: str = "ffmpeg"
    ffprobe_command: str = "ffprobe"


@dataclass(frozen=True)
class GitHubConfig:
    repository: str = "readguide/personal-podcast"
    pages_base_url: str = "https://readguide.github.io/personal-podcast"
    site_dir: Path = DEFAULT_ROOT / "Repository/personal-podcast/site"
    gh_command: str = "gh"
    audio_host: str = "github"
    cloudflare_audio_base_url: str = ""


@dataclass(frozen=True)
class AppConfig:
    podcast: PodcastConfig = field(default_factory=PodcastConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)


def _strip_comment(line: str) -> str:
    quote: Optional[str] = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote:
            escaped = True
            continue
        if character in {"'", '"'}:
            quote = None if quote == character else character if quote is None else quote
        elif character == "#" and quote is None:
            return line[:index]
    return line


def _parse_value(raw: str, line_number: int) -> Any:
    value = raw.strip()
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError) as error:
        raise ConfigError(f"配置文件第 {line_number} 行的值无效: {value}") from error


def parse_toml(text: str) -> Dict[str, Dict[str, Any]]:
    """Parse the small TOML subset used by this application on Python 3.9."""
    result: Dict[str, Dict[str, Any]] = {}
    section: Optional[str] = None
    for line_number, original_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(original_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ConfigError(f"配置文件第 {line_number} 行的分组名为空")
            result.setdefault(section, {})
            continue
        if section is None or "=" not in line:
            raise ConfigError(f"配置文件第 {line_number} 行格式无效")
        key, raw_value = line.split("=", 1)
        result[section][key.strip()] = _parse_value(raw_value, line_number)
    return result


def _path(value: Any, fallback: Path) -> Path:
    if value is None:
        return fallback
    return Path(os.path.expandvars(str(value))).expanduser()


def _section(data: Mapping[str, Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    return data.get(name, {})


def config_from_mapping(data: Mapping[str, Mapping[str, Any]]) -> AppConfig:
    podcast = _section(data, "podcast")
    storage = _section(data, "storage")
    download = _section(data, "download")
    audio = _section(data, "audio")
    github = _section(data, "github")

    storage_root = _path(storage.get("root"), DEFAULT_ROOT)
    storage_config = StorageConfig(
        root=storage_root,
        app_dir=_path(storage.get("app_dir"), storage_root / "Application Data"),
        source_retention_days=int(storage.get("source_retention_days", 90)),
        keep_final_audio=bool(storage.get("keep_final_audio", True)),
        keep_redundant_source_audio=bool(storage.get("keep_redundant_source_audio", False)),
    )
    default_site = storage_config.root / "Repository/personal-podcast/site"
    config = AppConfig(
        podcast=PodcastConfig(
            name=str(podcast.get("name", "收听库")),
            author=str(podcast.get("author", "en")),
            description=str(podcast.get("description", PodcastConfig().description)),
            language=str(podcast.get("language", "zh-CN")),
            explicit=bool(podcast.get("explicit", False)),
        ),
        storage=storage_config,
        download=DownloadConfig(
            order=[str(item) for item in download.get("order", ["downie"])],
            downie_app=str(download.get("downie_app", "Downie 4")),
            downie_timeout_seconds=int(download.get("downie_timeout_seconds", 3600)),
            downie_poll_seconds=float(download.get("downie_poll_seconds", 2.0)),
            downie_fallback_directory=_path(
                download.get("downie_fallback_directory"), Path.home() / "Downloads"
            ),
            yt_dlp_command=str(download.get("yt_dlp_command", "yt-dlp")),
        ),
        audio=AudioConfig(
            mode=str(audio.get("mode", "balanced")),
            aac_bitrate_kbps=int(audio.get("aac_bitrate_kbps", 128)),
            ffmpeg_command=str(audio.get("ffmpeg_command", "ffmpeg")),
            ffprobe_command=str(audio.get("ffprobe_command", "ffprobe")),
        ),
        github=GitHubConfig(
            repository=str(github.get("repository", "readguide/personal-podcast")),
            pages_base_url=str(
                github.get("pages_base_url", "https://readguide.github.io/personal-podcast")
            ).rstrip("/"),
            site_dir=_path(github.get("site_dir"), default_site),
            gh_command=str(github.get("gh_command", "gh")),
            audio_host=str(github.get("audio_host", "github")),
            cloudflare_audio_base_url=str(
                github.get("cloudflare_audio_base_url", "")
            ).rstrip("/"),
        ),
    )
    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    if config.audio.mode != "balanced":
        raise ConfigError("第一版仅支持 audio.mode = \"balanced\"")
    if config.storage.source_retention_days < 1:
        raise ConfigError("source_retention_days 必须大于 0")
    if config.audio.aac_bitrate_kbps < 32:
        raise ConfigError("aac_bitrate_kbps 不能低于 32")
    if not config.download.order:
        raise ConfigError("download.order 至少需要一个下载器")
    unsupported = set(config.download.order) - {"downie", "yt-dlp", "douyin-api"}
    if unsupported:
        raise ConfigError(f"未知下载器: {', '.join(sorted(unsupported))}")
    if "/" not in config.github.repository:
        raise ConfigError("github.repository 应为 owner/repository 格式")
    if config.github.audio_host not in {"github", "github-pages", "cloudflare"}:
        raise ConfigError(
            "github.audio_host 必须是 github、github-pages 或 cloudflare"
        )
    if (
        config.github.audio_host == "cloudflare"
        and not config.github.cloudflare_audio_base_url.startswith("https://")
    ):
        raise ConfigError("Cloudflare 音频地址必须使用 https://")


def load_config(path: Optional[Path] = None) -> AppConfig:
    config_path = path or Path(os.environ.get("PERSONAL_PODCAST_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        return AppConfig()
    try:
        return config_from_mapping(parse_toml(config_path.read_text(encoding="utf-8")))
    except OSError as error:
        raise ConfigError(f"无法读取配置文件 {config_path}: {error}") from error


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_config(config: AppConfig) -> str:
    order = ", ".join(_quoted(item) for item in config.download.order)
    return f'''[podcast]
name = {_quoted(config.podcast.name)}
author = {_quoted(config.podcast.author)}
description = {_quoted(config.podcast.description)}
language = {_quoted(config.podcast.language)}
explicit = {str(config.podcast.explicit).lower()}

[storage]
root = {_quoted(str(config.storage.root))}
app_dir = {_quoted(str(config.storage.app_dir))}
source_retention_days = {config.storage.source_retention_days}
keep_final_audio = {str(config.storage.keep_final_audio).lower()}
keep_redundant_source_audio = {str(config.storage.keep_redundant_source_audio).lower()}

[download]
order = [{order}]
downie_app = {_quoted(config.download.downie_app)}
downie_timeout_seconds = {config.download.downie_timeout_seconds}
downie_poll_seconds = {config.download.downie_poll_seconds}
downie_fallback_directory = {_quoted(str(config.download.downie_fallback_directory))}
yt_dlp_command = {_quoted(config.download.yt_dlp_command)}

[audio]
mode = {_quoted(config.audio.mode)}
aac_bitrate_kbps = {config.audio.aac_bitrate_kbps}
ffmpeg_command = {_quoted(config.audio.ffmpeg_command)}
ffprobe_command = {_quoted(config.audio.ffprobe_command)}

[github]
repository = {_quoted(config.github.repository)}
pages_base_url = {_quoted(config.github.pages_base_url)}
site_dir = {_quoted(str(config.github.site_dir))}
gh_command = {_quoted(config.github.gh_command)}
audio_host = {_quoted(config.github.audio_host)}
cloudflare_audio_base_url = {_quoted(config.github.cloudflare_audio_base_url)}
'''


def write_config(path: Path, config: Optional[AppConfig] = None, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(config or AppConfig()), encoding="utf-8")
