import logging
import shutil
import subprocess
from typing import List, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from personal_podcast.errors import DependencyError, PersonalPodcastError


LOGGER = logging.getLogger(__name__)


def executable_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _redact(argument: str) -> str:
    parts = urlsplit(argument)
    if not parts.scheme or (not parts.netloc and parts.scheme != "downie"):
        return argument
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def run_checked(
    arguments: List[str],
    *,
    timeout: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
    error_type: type = PersonalPodcastError,
) -> subprocess.CompletedProcess:
    LOGGER.debug("运行外部程序: %s", " ".join(_redact(item) for item in arguments))
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # ffmpeg 等可能输出非 UTF-8 字节(如 GBK 标题), 替换而非崩溃
            timeout=timeout,
            env=dict(env) if env is not None else None,
        )
    except FileNotFoundError as error:
        raise DependencyError(f"未找到外部程序: {arguments[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise error_type(f"外部程序运行超时: {arguments[0]}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip()
        raise error_type(f"{arguments[0]} 执行失败: {detail}")
    return result
