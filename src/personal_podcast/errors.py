class PersonalPodcastError(Exception):
    """Base error shown to command-line users without a traceback."""


class ConfigError(PersonalPodcastError):
    """The configuration is missing or invalid."""


class DependencyError(PersonalPodcastError):
    """A required external program is unavailable."""


class DownloadError(PersonalPodcastError):
    """Every configured downloader failed."""


class MediaError(PersonalPodcastError):
    """The source media could not be inspected or processed."""


class EpisodeNotFoundError(PersonalPodcastError):
    """The requested episode does not exist."""


class PublishError(PersonalPodcastError):
    """An episode could not be published to GitHub Releases."""
