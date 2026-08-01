import logging
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen


LOGGER = logging.getLogger(__name__)
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def download_artwork(url: Optional[str], output_stem: Path) -> Optional[Path]:
    if not url:
        return None
    try:
        request = Request(url, headers={"User-Agent": "PersonalPodcastGenerator/0.1"})
        with urlopen(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            extension = CONTENT_TYPE_EXTENSIONS.get(content_type, ".jpg")
            data = response.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("封面大于 20 MB")
        output_path = output_stem.with_suffix(extension)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
        return output_path
    except (OSError, ValueError) as error:
        LOGGER.warning("无法下载单集封面，将使用播客封面: %s", error)
        return None
