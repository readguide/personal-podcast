# Personal Podcast Generator

把一个公开视频链接转换为个人播客“收听库”的 macOS 工具。

第一版以单链接流程为核心：Downie 4 优先下载，失败时使用 `yt-dlp`；`ffmpeg` 按平衡模式生成音频；GitHub Releases 保存音频，GitHub Pages 发布 `feed.xml`、封面和订阅页。

## 已实现

- 单链接导入，按导入时间倒序排列。
- Downie 4 自动化下载，指定“仅音频”和本地目标目录。
- `yt-dlp` 备用下载及元数据读取。
- AAC 和 MP3 不重编码；Opus、Vorbis 等转为 AAC-LC M4A。
- 默认 AAC 上限 128 kbps，源码率更低时不主动提高。
- 最终音频永久保留。
- 非重复源音频及源视频保留 90 天，到期后仅标记为可清理。
- 手动归档、恢复、删除和源文件清理。
- GitHub Release 发布、删除，以及显式站点同步。
- RSS 2.0 + Apple Podcasts 标签、稳定 GUID、封面和文件长度。
- SQLite 节目记录、轮转日志、中文错误提示和基础测试。

## 环境

- macOS 11 或更高版本
- Python 3.9 或更高版本
- Downie 4（已按 4.12.11 的自动化接口实现）
- `ffmpeg` 与 `ffprobe`
- GitHub CLI `gh`
- `yt-dlp`（建议安装，作为备用并用于读取标题、简介和封面）

缺少命令行工具时可用 Homebrew 安装：


若下载源音频与最终音频内容等价，默认不会再保留重复源文件。需要转码的源音频或包含视频的源文件会保留 90 天。

## GitHub Pages

仓库包含 [Pages 工作流](.github/workflows/pages.yml)，发布目录为 `site/`。首次使用时，在 GitHub 仓库的 **Settings → Pages** 中把 Source 设为 **GitHub Actions**。

预期地址：

```text
订阅页：https://readguide.github.io/personal-podcast/
RSS：https://readguide.github.io/personal-podcast/feed.xml
```

仓库和 Releases 都是公开的，因此订阅源与已发布音频也是公开链接。请只处理你有权下载和发布的内容。

## 音频规则

| 源音频 | 处理 | 最终格式 |
| --- | --- | --- |
| AAC | 重新封装，不重编码 | M4A |
| MP3 | 重新封装，不重编码 | MP3 |
| Opus / Vorbis / FLAC / 其他 | AAC-LC 转码 | M4A |

程序不剪辑、不降噪、不调速，不改变声道或采样率。标题、作者、专辑、简介和封面会写入成品音频。

## 开发与验证

项目的 Python 代码只使用标准库。运行测试：

```bash
PYTHONPYCACHEPREFIX=/tmp/personal-podcast-pycache \
  python3 -m unittest discover -s tests -v
```

单独校验生成的 RSS：

```bash
personal-podcast validate-feed
```
