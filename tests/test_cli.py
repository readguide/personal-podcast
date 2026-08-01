import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from personal_podcast.cli import main


class CliSafetyTests(unittest.TestCase):
    def test_missing_config_does_not_create_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "missing/config.toml"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config_path), "list"])
            self.assertEqual(result, 1)
            self.assertIn("请先运行 init", stderr.getvalue())
            self.assertFalse(config_path.parent.exists())
