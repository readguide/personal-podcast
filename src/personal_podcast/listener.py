import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Union

from personal_podcast.commands import run_checked
from personal_podcast.config import AppConfig
from personal_podcast.errors import PersonalPodcastError


LISTENER_LABEL = "com.readguide.personal-podcast-clips"
PlistValue = Union[str, int, bool, List[str], Dict[str, str]]


def build_listener_plist(
    config: AppConfig,
    config_path: Path,
    source_path: Path,
    *,
    interval_seconds: int,
    python_executable: str,
) -> Dict[str, PlistValue]:
    logs = config.storage.logs_dir
    arguments = [
        python_executable,
        "-m",
        "personal_podcast.cli",
        "--config",
        str(config_path),
        "process-clips",
        str(source_path),
        "--publish",
        "--sync-site",
        "--sync-transcripts",
    ]
    return {
        "Label": LISTENER_LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "WatchPaths": [str(source_path)],
        "StartInterval": interval_seconds,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "WorkingDirectory": str(config.github.site_dir.parent),
        "StandardOutPath": str(logs / "clip-archive-listener.log"),
        "StandardErrorPath": str(logs / "clip-archive-listener-error.log"),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED": "1",
        },
    }


def install_listener(
    config: AppConfig,
    config_path: Path,
    source_path: Path,
    *,
    interval_seconds: int = 300,
    launch_agents_dir: Path = Path.home() / "Library/LaunchAgents",
) -> Path:
    if interval_seconds < 30:
        raise PersonalPodcastError("监听补偿检查间隔不能少于 30 秒")
    if not source_path.is_file():
        raise PersonalPodcastError(f"剪藏目录不存在: {source_path}")

    config.storage.logs_dir.mkdir(parents=True, exist_ok=True)
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents_dir / f"{LISTENER_LABEL}.plist"
    payload = build_listener_plist(
        config,
        config_path.resolve(),
        source_path.resolve(),
        interval_seconds=interval_seconds,
        python_executable=sys.executable,
    )
    temporary = plist_path.with_suffix(".plist.tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    os.chmod(temporary, 0o600)
    temporary.replace(plist_path)

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    run_checked(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        error_type=PersonalPodcastError,
    )
    run_checked(
        ["launchctl", "enable", f"{domain}/{LISTENER_LABEL}"],
        error_type=PersonalPodcastError,
    )
    run_checked(
        ["launchctl", "kickstart", "-k", f"{domain}/{LISTENER_LABEL}"],
        error_type=PersonalPodcastError,
    )
    return plist_path
