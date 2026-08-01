import argparse
import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from personal_podcast.config import AppConfig, DEFAULT_CONFIG_PATH, load_config, write_config
from personal_podcast.downloader import DownieDownloader
from personal_podcast.errors import ConfigError, PersonalPodcastError
from personal_podcast.feed import validate_feed
from personal_podcast.logging_setup import configure_logging
from personal_podcast.models import Episode
from personal_podcast.service import PersonalPodcastService


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-podcast",
        description="把一个公开视频链接加入个人播客“收听库”。",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("PERSONAL_PODCAST_CONFIG", DEFAULT_CONFIG_PATH)),
        help="配置文件路径",
    )
    parser.add_argument("--verbose", action="store_true", help="在终端显示详细日志")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="创建目录、配置、数据库和空 RSS")
    subparsers.add_parser("doctor", help="检查 Downie、ffmpeg、yt-dlp 和 gh")

    add_parser = subparsers.add_parser("add", help="导入一个链接")
    add_parser.add_argument("url", help="公开视频链接")
    add_parser.add_argument(
        "--publish",
        action="store_true",
        help="导入后立即发布到 GitHub Releases",
    )
    add_parser.add_argument(
        "--sync-site",
        action="store_true",
        help="发布后提交并推送 RSS 站点",
    )

    latest_parser = subparsers.add_parser(
        "add-latest", help="读取链接收件箱中的最新链接，仅导入视频"
    )
    latest_parser.add_argument(
        "--publish", action="store_true", help="导入后立即发布到 GitHub Releases"
    )
    latest_parser.add_argument(
        "--sync-site", action="store_true", help="发布后提交并推送 RSS 站点"
    )

    publish_parser = subparsers.add_parser("publish", help="发布一个节目到 GitHub Releases")
    publish_parser.add_argument("episode_id", help="节目编号")

    transcript_parser = subparsers.add_parser(
        "transcript", help="从 GitHub Release 下载转写稿并写入 RSS 简介"
    )
    transcript_parser.add_argument("episode_id", help="节目编号")
    transcript_parser.add_argument(
        "--sync-site", action="store_true", help="同时提交并推送更新后的 RSS"
    )

    subparsers.add_parser("generate", help="重新生成站点与 feed.xml")
    subparsers.add_parser("sync-site", help="提交并推送 RSS 站点")
    subparsers.add_parser("validate-feed", help="解析并校验生成的 feed.xml")

    list_parser = subparsers.add_parser("list", help="列出节目")
    list_parser.add_argument("--all", action="store_true", help="包含已删除节目")

    archive_parser = subparsers.add_parser("archive", help="从 RSS 归档节目")
    archive_parser.add_argument("episode_id", help="节目编号")

    restore_parser = subparsers.add_parser("restore", help="恢复已归档节目")
    restore_parser.add_argument("episode_id", help="节目编号")

    delete_parser = subparsers.add_parser("delete", help="手动删除节目记录或指定文件")
    delete_parser.add_argument("episode_id", help="节目编号")
    delete_parser.add_argument("--source", action="store_true", help="同时删除源文件")
    delete_parser.add_argument("--final", action="store_true", help="同时删除最终音频")
    delete_parser.add_argument("--release", action="store_true", help="同时删除 GitHub Release")

    cleanup_parser = subparsers.add_parser("cleanup", help="查看或清理已满 90 天的源文件")
    cleanup_parser.add_argument(
        "--delete",
        action="store_true",
        help="实际删除；省略时只预览",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"add", "add-latest"} and args.sync_site and not args.publish:
        parser.error("--sync-site 需要同时使用 --publish")
    try:
        if args.command not in {"init", "doctor"} and not args.config.exists():
            raise ConfigError(f"配置文件不存在，请先运行 init：{args.config}")
        config = load_config(args.config)
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "validate-feed":
            path = config.github.site_dir / "feed.xml"
            count = validate_feed(path)
            print(f"RSS 有效：{path}（{count} 期）")
            return 0

        configure_logging(config.storage.logs_dir, args.verbose)
        service = PersonalPodcastService(config)
        if args.command == "init":
            write_config(args.config, config)
            count = service.initialize()
            print(f"初始化完成：{config.storage.root}")
            print(f"配置文件：{args.config}")
            print(f"RSS：{config.github.site_dir / 'feed.xml'}（{count} 期）")
            return 0

        service.initialize()
        if args.command == "add":
            episode = service.add(args.url, publish=args.publish)
            if args.sync_site:
                service.sync_site(f"Publish podcast episode {episode.episode_id}")
            print(f"已导入：{episode.title}")
            print(f"节目编号：{episode.episode_id}")
            print(f"最终音频：{episode.audio_path}")
            print("发布状态：已发布" if episode.public_audio_url else "发布状态：仅本地")
        elif args.command == "add-latest":
            url, episode = service.add_latest(publish=args.publish)
            if episode is None:
                print(f"已跳过非视频链接：{url}")
            else:
                if args.sync_site:
                    service.sync_site(f"Publish podcast episode {episode.episode_id}")
                print(f"已导入：{episode.title}")
                print(f"节目编号：{episode.episode_id}")
                print(f"最终音频：{episode.audio_path}")
                print("发布状态：已发布" if episode.public_audio_url else "发布状态：仅本地")
        elif args.command == "publish":
            episode = service.publish(args.episode_id)
            print(f"已发布：{episode.public_audio_url}")
        elif args.command == "transcript":
            episode = service.import_transcript(args.episode_id)
            if args.sync_site:
                service.sync_site(f"Publish transcript for {episode.episode_id}")
            print(f"转写稿已保存：{episode.transcript_path}")
            print("RSS 简介已加入自动转写全文。")
        elif args.command == "generate":
            count = service.generate_site()
            print(f"已生成 RSS：{config.github.site_dir / 'feed.xml'}（{count} 期）")
        elif args.command == "sync-site":
            changed = service.sync_site()
            print("站点已推送到 GitHub。" if changed else "站点没有需要推送的变化。")
        elif args.command == "list":
            _print_episodes(service.list_episodes(include_deleted=args.all))
        elif args.command == "archive":
            episode = service.archive(args.episode_id)
            print(f"已归档：{episode.title}")
        elif args.command == "restore":
            episode = service.restore(args.episode_id)
            print(f"已恢复：{episode.title}")
        elif args.command == "delete":
            episode = service.delete(
                args.episode_id,
                delete_source=args.source,
                delete_final=args.final,
                delete_release=args.release,
            )
            print(f"已删除节目记录：{episode.title}")
            if not (args.source or args.final or args.release):
                print("本地文件和 GitHub Release 均已保留。")
        elif args.command == "cleanup":
            cleanable = service.cleanable()
            if not cleanable:
                print("没有已满保留期的源文件。")
            elif args.delete:
                cleaned = service.cleanup_sources()
                print(f"已清理 {len(cleaned)} 个源文件；最终音频未受影响。")
            else:
                print(f"有 {len(cleanable)} 个源文件可清理：")
                for episode in cleanable:
                    print(f"- {episode.episode_id}  {episode.source_path}")
                print("执行 cleanup --delete 后才会实际删除。")
        return 0
    except (PersonalPodcastError, OSError, ValueError, sqlite3.Error) as error:
        if logging.getLogger().handlers:
            LOGGER.exception("操作失败")
        print(f"错误：{error}", file=sys.stderr)
        return 1


def _doctor(config: AppConfig) -> int:
    checks = [
        ("Downie 4", DownieDownloader(config.download).is_available()),
        ("ffmpeg", shutil.which(config.audio.ffmpeg_command) is not None),
        ("ffprobe", shutil.which(config.audio.ffprobe_command) is not None),
        ("yt-dlp（备用）", shutil.which(config.download.yt_dlp_command) is not None),
        ("GitHub CLI", shutil.which(config.github.gh_command) is not None),
    ]
    for label, available in checks:
        print(f"{'✓' if available else '×'} {label}")
    print(f"存储目录：{config.storage.root}")
    print(f"RSS 地址：{config.github.pages_base_url}/feed.xml")
    required_ok = all(available for label, available in checks if label in {"Downie 4", "ffmpeg", "ffprobe", "GitHub CLI"})
    return 0 if required_ok else 1


def _print_episodes(episodes: List[Episode]) -> None:
    if not episodes:
        print("暂无节目。")
        return
    for episode in episodes:
        if episode.deleted_at:
            status = "已删除"
        elif episode.archived_at:
            status = "已归档"
        elif episode.public_audio_url:
            status = "已发布"
        else:
            status = "仅本地"
        imported = episode.imported_at.strftime("%Y-%m-%d %H:%M")
        print(f"{episode.episode_id}\t{status}\t{imported}\t{episode.title}")


if __name__ == "__main__":
    raise SystemExit(main())
