import hashlib
import io
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from personal_podcast.config import GitHubConfig
from personal_podcast.models import Episode
from personal_podcast.publisher import (
    GitHubReleasePublisher,
    GitSitePublisher,
    github_cli_environment,
)


def run_git(arguments, directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments],
        cwd=str(directory),
        check=True,
        capture_output=True,
        text=True,
    )


class ReleasePublisherTests(unittest.TestCase):
    def test_github_environment_reads_dedicated_keychain_item(self) -> None:
        keychain_result = subprocess.CompletedProcess([], 0, stdout="secret-token\n")
        with patch.dict("os.environ", {"USER": "example"}, clear=True), patch(
            "personal_podcast.publisher.subprocess.run", return_value=keychain_result
        ) as run:
            environment = github_cli_environment()
        self.assertEqual(environment["GH_TOKEN"], "secret-token")
        self.assertIn("personal-podcast-github-token", run.call_args.args[0])

    def test_github_environment_keeps_existing_token(self) -> None:
        with patch.dict("os.environ", {"GH_TOKEN": "existing"}, clear=True), patch(
            "personal_podcast.publisher.subprocess.run"
        ) as run:
            environment = github_cli_environment()
        self.assertEqual(environment["GH_TOKEN"], "existing")
        run.assert_not_called()

    def test_release_url_is_versioned_with_audio_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "example-123.m4a"
            audio.write_bytes(b"audio")
            episode = Episode(
                episode_id="example-123",
                source_url="https://example.com/123",
                title="Episode",
                description="Description",
                author="Author",
                imported_at=datetime.now(timezone.utc),
                duration_seconds=10,
                audio_path=audio,
                audio_bytes=5,
                audio_mime="audio/mp4",
            )
            publisher = GitHubReleasePublisher(GitHubConfig())
            with patch("personal_podcast.publisher.executable_exists", return_value=True), patch(
                "personal_podcast.publisher.GitHubReleasePublisher._release_exists",
                return_value=False,
            ), patch("personal_podcast.publisher.run_checked") as run:
                tag, url = publisher.publish(episode)
            self.assertEqual(tag, "episode-example-123")
            self.assertEqual(
                url,
                "https://github.com/readguide/personal-podcast/releases/download/episode-example-123/"
                f"example-123.m4a?v={hashlib.sha256(b'audio').hexdigest()[:16]}",
            )
            self.assertEqual(run.call_args.args[0][1:3], ["release", "create"])

    def test_cloudflare_url_keeps_github_release_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "example-123.m4a"
            audio.write_bytes(b"audio")
            episode = Episode(
                episode_id="example-123",
                source_url="https://example.com/123",
                title="Episode",
                description="Description",
                author="Author",
                imported_at=datetime.now(timezone.utc),
                duration_seconds=10,
                audio_path=audio,
                audio_bytes=5,
                audio_mime="audio/mp4",
            )
            publisher = GitHubReleasePublisher(
                GitHubConfig(
                    audio_host="cloudflare",
                    cloudflare_audio_base_url="https://audio.example.workers.dev",
                )
            )
            self.assertEqual(
                publisher.public_url(episode, "episode-example-123"),
                "https://audio.example.workers.dev/audio/"
                f"example-123.m4a?v={hashlib.sha256(b'audio').hexdigest()[:16]}",
            )

    def test_github_pages_url_uses_direct_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "example-123.m4a"
            audio.write_bytes(b"audio")
            episode = Episode(
                episode_id="example-123",
                source_url="https://example.com/123",
                title="Episode",
                description="Description",
                author="Author",
                imported_at=datetime.now(timezone.utc),
                duration_seconds=10,
                audio_path=audio,
                audio_bytes=5,
                audio_mime="audio/mp4",
            )
            publisher = GitHubReleasePublisher(
                GitHubConfig(audio_host="github-pages")
            )
            self.assertEqual(
                publisher.public_url(episode, "episode-example-123"),
                "https://readguide.github.io/personal-podcast/audio/"
                f"example-123.m4a?v={hashlib.sha256(b'audio').hexdigest()[:16]}",
            )

    def test_download_transcript_uses_stable_episode_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            publisher = GitHubReleasePublisher(GitHubConfig())
            with patch(
                "personal_podcast.publisher.urlopen",
                return_value=io.BytesIO(b"Transcript"),
            ) as download:
                path = publisher.download_transcript(
                    "episode-example-123", "example-123", destination
                )
            self.assertEqual(path, destination / "example-123.txt")
            self.assertEqual(path.read_text(encoding="utf-8"), "Transcript")
            self.assertIn("example-123.txt", download.call_args.args[0].full_url)


class SitePublisherTests(unittest.TestCase):
    def test_sync_commits_only_site_and_pushes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            repository = root / "repository"
            remote.mkdir()
            repository.mkdir()
            run_git(["init", "--bare"], remote)
            run_git(["init", "-b", "main"], repository)
            run_git(["config", "user.name", "Test User"], repository)
            run_git(["config", "user.email", "test@example.com"], repository)
            (repository / "site").mkdir()
            (repository / "site/feed.xml").write_text("initial", encoding="utf-8")
            (repository / "unrelated.txt").write_text("initial", encoding="utf-8")
            run_git(["add", "."], repository)
            run_git(["commit", "-m", "Initial"], repository)
            run_git(["remote", "add", "origin", str(remote)], repository)
            run_git(["push", "-u", "origin", "main"], repository)

            (repository / "site/feed.xml").write_text("updated", encoding="utf-8")
            (repository / "unrelated.txt").write_text("not committed", encoding="utf-8")
            publisher = GitSitePublisher(GitHubConfig(site_dir=repository / "site"))
            self.assertTrue(publisher.sync("Update feed"))
            self.assertFalse(publisher.sync("No changes"))
            status = run_git(["status", "--short"], repository).stdout
            self.assertEqual(status.strip(), "M unrelated.txt")
            remote_subject = run_git(
                [
                    "--git-dir",
                    str(remote),
                    "log",
                    "-1",
                    "--format=%s",
                    "refs/heads/main",
                ],
                root,
            ).stdout.strip()
            self.assertEqual(remote_subject, "Update feed")


if __name__ == "__main__":
    unittest.main()
