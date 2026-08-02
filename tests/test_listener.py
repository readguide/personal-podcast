import tempfile
import unittest
from pathlib import Path

from personal_podcast.config import AppConfig, GitHubConfig, StorageConfig
from personal_podcast.listener import LISTENER_LABEL, build_listener_plist


class ListenerTests(unittest.TestCase):
    def test_listener_watches_only_selected_file_and_uses_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AppConfig(
                storage=StorageConfig(root=root, app_dir=root / "Application Data"),
                github=GitHubConfig(site_dir=root / "Repository/project/site"),
            )
            source = root / "clips.txt"
            payload = build_listener_plist(
                config,
                root / "Application Data/config.toml",
                source,
                interval_seconds=300,
                python_executable="/runtime/python",
            )
            self.assertEqual(payload["Label"], LISTENER_LABEL)
            self.assertEqual(payload["WatchPaths"], [str(source)])
            arguments = payload["ProgramArguments"]
            self.assertIn("process-clips", arguments)
            self.assertIn("--sync-transcripts", arguments)
            self.assertTrue(
                str(payload["StandardOutPath"]).startswith(str(root / "Application Data"))
            )


if __name__ == "__main__":
    unittest.main()
