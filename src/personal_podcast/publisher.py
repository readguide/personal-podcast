import getpass
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from personal_podcast.commands import executable_exists, run_checked
from personal_podcast.config import GitHubConfig
from personal_podcast.errors import DependencyError, PublishError
from personal_podcast.models import Episode


KEYCHAIN_TOKEN_SERVICE = "personal-podcast-github-token"


def github_cli_environment() -> Dict[str, str]:
    environment = os.environ.copy()
    if environment.get("GH_TOKEN") or environment.get("GITHUB_TOKEN"):
        return environment
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_TOKEN_SERVICE,
                "-a",
                environment.get("USER") or getpass.getuser(),
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return environment
    token = result.stdout.strip()
    if result.returncode == 0 and token:
        environment["GH_TOKEN"] = token
    return environment


class GitHubReleasePublisher:
    def __init__(self, config: GitHubConfig):
        self.config = config

    def publish(self, episode: Episode) -> Tuple[str, str]:
        if not executable_exists(self.config.gh_command):
            raise DependencyError(f"未找到 GitHub 工具: {self.config.gh_command}")
        if not episode.audio_path.exists():
            raise PublishError(f"成品音频不存在: {episode.audio_path}")
        tag = episode.release_tag or f"episode-{episode.episode_id}"
        environment = github_cli_environment()
        # 资产名统一用 episode_id(纯 ASCII), 避免中文/特殊字符文件名在 GitHub 上被乱改
        # (2026-08-12: 曾因中文资产名导致 release 资产变成 AI.AI.m4a, feed 下载失败)
        asset_name = f"{episode.episode_id}{episode.audio_path.suffix}"
        upload_path = self._stage_asset(episode.audio_path, asset_name)
        try:
            if self._release_exists(tag):
                run_checked(
                    [
                        self.config.gh_command,
                        "release",
                        "upload",
                        tag,
                        str(upload_path),
                        "--clobber",
                        "--repo",
                        self.config.repository,
                    ],
                    env=environment,
                    error_type=PublishError,
                )
            else:
                notes = episode.description or f"原始来源：{episode.source_url}"
                run_checked(
                    [
                        self.config.gh_command,
                        "release",
                        "create",
                        tag,
                        str(upload_path),
                        "--repo",
                        self.config.repository,
                        "--title",
                        episode.title,
                        "--notes",
                        notes,
                    ],
                    env=environment,
                    error_type=PublishError,
                )
        finally:
            upload_path.unlink(missing_ok=True)
        return tag, self.public_url(episode, tag)

    @staticmethod
    def _stage_asset(source: Path, asset_name: str) -> Path:
        """复制音频到临时目录(文件名=纯资产名, 不带前缀), 供上传。"""
        import shutil as _shutil
        import tempfile as _tempfile
        temp_dir = Path(_tempfile.gettempdir()) / "podcast-assets"
        temp_dir.mkdir(parents=True, exist_ok=True)
        staged = temp_dir / asset_name
        _shutil.copy2(str(source), str(staged))
        return staged

    def public_url(self, episode: Episode, tag: str) -> str:
        # 资产名与上传一致: episode_id.后缀
        filename = quote(f"{episode.episode_id}{episode.audio_path.suffix}")
        if self.config.audio_host == "cloudflare":
            base_url = f"{self.config.cloudflare_audio_base_url}/audio/{filename}"
        elif self.config.audio_host == "github-pages":
            base_url = f"{self.config.pages_base_url}/audio/{filename}"
        else:
            base_url = (
                f"https://github.com/{self.config.repository}/releases/download/"
                f"{quote(tag)}/{filename}"
            )
        return f"{base_url}?v={_asset_version(episode.audio_path)}"

    def delete(self, tag: str) -> None:
        if not self._release_exists(tag):
            return
        run_checked(
            [
                self.config.gh_command,
                "release",
                "delete",
                tag,
                "--repo",
                self.config.repository,
                "--cleanup-tag",
                "--yes",
            ],
            env=github_cli_environment(),
            error_type=PublishError,
        )

    def download_transcript(
        self, tag: str, episode_id: str, destination: Path
    ) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        transcript_path = destination / f"{episode_id}.txt"
        temporary = transcript_path.with_suffix(".txt.tmp")
        url = (
            f"https://github.com/{self.config.repository}/releases/download/"
            f"{quote(tag, safe='')}/{quote(transcript_path.name, safe='')}"
        )
        last_error: Exception = PublishError("未知下载错误")
        for attempt in range(3):
            try:
                request = Request(
                    url, headers={"User-Agent": "personal-podcast-generator"}
                )
                with urlopen(request, timeout=45) as response, temporary.open(
                    "wb"
                ) as output:
                    shutil.copyfileobj(response, output)
                temporary.replace(transcript_path)
                break
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                last_error = error
                temporary.unlink(missing_ok=True)
                if isinstance(error, HTTPError) and error.code == 404:
                    break
                if attempt < 2:
                    time.sleep(2)
        else:
            raise PublishError(f"下载 Release 转写稿失败: {last_error}") from last_error
        if not transcript_path.exists() or transcript_path.stat().st_size == 0:
            transcript_path.unlink(missing_ok=True)
            raise PublishError(f"Release 中没有可用转写稿: {transcript_path.name}")
        return transcript_path

    def _release_exists(self, tag: str) -> bool:
        try:
            result = subprocess.run(
                [
                    self.config.gh_command,
                    "release",
                    "view",
                    tag,
                    "--repo",
                    self.config.repository,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=github_cli_environment(),
            )
        except FileNotFoundError as error:
            raise DependencyError(f"未找到 GitHub 工具: {self.config.gh_command}") from error
        except subprocess.TimeoutExpired as error:
            raise PublishError("检查 GitHub Release 时超时") from error
        return result.returncode == 0


def _asset_version(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


class GitSitePublisher:
    def __init__(self, config: GitHubConfig):
        self.config = config

    def sync(self, message: str) -> bool:
        repository_dir = self.config.site_dir.parent
        if not (repository_dir / ".git").exists():
            raise PublishError(f"站点目录不在 Git 仓库中: {repository_dir}")
        run_checked(
            ["git", "-C", str(repository_dir), "add", "--", "site"],
            error_type=PublishError,
        )
        diff = subprocess.run(
            ["git", "-C", str(repository_dir), "diff", "--cached", "--quiet", "--", "site"],
            check=False,
            capture_output=True,
            text=True,
        )
        if diff.returncode == 0:
            return False
        if diff.returncode != 1:
            raise PublishError((diff.stderr or "无法检查站点改动").strip())
        run_checked(
            [
                "git",
                "-C",
                str(repository_dir),
                "commit",
                "-m",
                message,
                "--",
                "site",
            ],
            error_type=PublishError,
        )
        run_checked(
            ["git", "-C", str(repository_dir), "push", "origin", "HEAD"],
            error_type=PublishError,
        )
        return True
