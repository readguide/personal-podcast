# Personal Podcast Generator

把一个公开视频链接转换为个人播客的 macOS 工具。

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
- GitHub Actions 使用 whisper.cpp 生成中文 TXT/SRT/VTT 转写稿。
- TXT 转写稿永久保存在本地，RSS 简介自动附带全文。
- 可监听一个剪藏目录 TXT，只处理存量基线之后新增的视频链接。
- 微信文章直接跳过，其他文章在没有可下载媒体时跳过。
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

```bash
brew install ffmpeg gh yt-dlp
gh auth login
```

发布时程序优先使用 `GH_TOKEN` 或 `GITHUB_TOKEN`。如果 GitHub CLI 的浏览器登录不可用，macOS 也可以把令牌存入本程序专用的钥匙串项；令牌不会写入配置、日志或仓库：

```bash
read -s GITHUB_PODCAST_TOKEN
security add-generic-password -U \
  -s personal-podcast-github-token -a "$USER" -w "$GITHUB_PODCAST_TOKEN"
unset GITHUB_PODCAST_TOKEN
```

本程序不读取 macOS 音乐资料库。Downie 只通过其公开的 `downie://XUOpenURL` 自动化接口接收链接、目标目录和“仅音频”选项。

程序不会修改 Downie 的默认下载目录。每个链接都会请求写入独立的 `Source Media` 节目目录；如果 Downie 忽略单次目录，程序只会识别本次新增的一个顶层媒体文件，并从 `downie_fallback_directory` 移入对应节目目录，已有下载不会被移动。

## 安装

仓库建议位于播客存储根目录内。以下账号和路径仅为占位符：

```bash
PODCAST_ROOT="$HOME/Personal Podcast"
GITHUB_ACCOUNT="YOUR_GITHUB_ACCOUNT"
mkdir -p "$PODCAST_ROOT/Repository"
git clone "https://github.com/$GITHUB_ACCOUNT/personal-podcast.git" \
  "$PODCAST_ROOT/Repository/personal-podcast"
cd "$PODCAST_ROOT/Repository/personal-podcast"

python3 -m venv .venv
.venv/bin/pip install -e .
source .venv/bin/activate
personal-podcast init
personal-podcast doctor
```

新开终端后需再次运行 `source .venv/bin/activate`，再使用 `personal-podcast`。也可以由用户自行把虚拟环境中的命令入口加入已有的 `PATH` 目录。

`init` 会创建配置文件。默认位置可在配置或环境中调整，例如：

```text
$PODCAST_ROOT/Application Data/config.toml
```

也可以使用仓库中的 [`config.example.toml`](config.example.toml) 作为参考。

## 单链接使用

最简单的输入方式是把链接追加到 `$PODCAST_ROOT/Inbox/links.txt`，每行一个。程序只读取文件中最后出现的链接，不会清空或改写文件：

```bash
personal-podcast add-latest --publish --sync-site
```

明确的视频链接会进入下载和播客流程。其他链接会先用 `yt-dlp` 检查是否存在可下载媒体；只有文章而没有视频或音频流时，会提示跳过。

也可以直接在命令中输入链接。

只导入到本地：

```bash
personal-podcast add "https://example.com/video"
```

发布音频到 GitHub Releases，但暂不推送 RSS：

```bash
personal-podcast add "https://example.com/video" --publish
```

GitHub Releases 始终保存音频。配置 Cloudflare Worker 后，可以只切换 RSS 下载地址，不重复上传音频：

```bash
personal-podcast audio-host cloudflare \
  --cloudflare-url https://YOUR_WORKER.workers.dev \
  --sync-site

personal-podcast audio-host github-pages --sync-site

personal-podcast audio-host github --sync-site
```

一条命令完成下载、处理、Release 发布和站点推送：

```bash
personal-podcast add "https://example.com/video" --publish --sync-site
```

`--sync-site` 是显式开关。省略时程序不会提交或推送 GitHub Pages 内容。

## 剪藏目录自动监听

剪藏目录不要求固定格式。只追加一行视频链接即可；如果同时包含标题和保存日期，程序会一并读取：

```text
视频名称
来源：https://example.com/video
保存日期：2026年8月2日 12:30
===
```

首次安装监听时，程序把文件中当前最后一个链接设为存量基线，不处理基线及其之前的内容。之后扫描基线后新增的 URL；有保存日期时用日期辅助定位，没有日期时按文件中的先后顺序处理。处理状态使用“来源链接 + 保存日期”去重，没有日期时使用来源链接去重。

自动转换仅支持明确的视频页面：YouTube、Bilibili、抖音、小红书、TikTok、快手、微博视频、AcFun、Vimeo、X/Twitter 视频、Instagram Reels 和 Facebook 视频。微信、GitHub 及其他普通网页直接跳过，不会调用下载器试探。

```bash
personal-podcast install-clips-listener "/PATH/TO/剪藏目录.txt"
```

监听使用 macOS `launchd` 的精确文件 `WatchPaths`；文件变化时立即检查，每 5 分钟还会补偿检查一次。每次只运行一个串行任务，批量完成后统一更新站点。`mp.weixin.qq.com` 链接在媒体检测前直接跳过，程序不会修改剪藏目录原文件。

监听任务还会定期检查已发布节目的 GitHub Release。云端转写完成后，TXT 会自动下载到本地并加入 RSS 简介，不再需要逐期手动执行 `transcript`。

处理状态和日志分别保存在：

```text
$PODCAST_ROOT/Application Data/clip-archive-state.json
$PODCAST_ROOT/Application Data/logs/clip-archive-listener.log
$PODCAST_ROOT/Application Data/logs/clip-archive-listener-error.log
```

macOS 监听配置是系统要求的唯一外部运行文件：

```text
$HOME/Library/LaunchAgents/com.readguide.personal-podcast-clips.plist
```

## 节目管理

```bash
# 查看节目
personal-podcast list

# 从 RSS 隐藏，但保留所有文件
personal-podcast archive EPISODE_ID

# 恢复到 RSS
personal-podcast restore EPISODE_ID

# 标记删除，但保留本地文件与 Release
personal-podcast delete EPISODE_ID

# 明确删除源文件、最终音频和 Release
personal-podcast delete EPISODE_ID --source --final --release

# 先预览已满 90 天的源文件
personal-podcast cleanup

# 确认后实际清理；最终音频不受影响
personal-podcast cleanup --delete

# GitHub 转写完成后，下载 TXT 到本地并把全文写入 RSS 简介
personal-podcast transcript EPISODE_ID --sync-site
```

归档、恢复或删除后，可运行：

```bash
personal-podcast sync-site
```

## 本地目录

所有运行数据都集中在指定目录：

```text
$PODCAST_ROOT/
├── Application Data/
│   ├── config.toml
│   ├── clip-archive-state.json
│   ├── podcast.db
│   └── logs/
├── Final Audio/
│   └── YYYY/
├── Source Media/
│   └── YYYY/episode-id/
├── Artwork/
│   ├── podcast-cover.png
│   └── Episodes/
├── Transcripts/
│   └── YYYY/episode-id - 视频名称.txt
├── Inbox/
│   └── links.txt
├── Repository/
│   └── personal-podcast/
│       └── site/
├── Temp/
└── Exports/
```

若下载源音频与最终音频内容等价，默认不会再保留重复源文件。需要转码的源音频或包含视频的源文件会保留 90 天。

## GitHub Pages

仓库包含 [Pages 工作流](.github/workflows/pages.yml)，发布目录为 `site/`。首次使用时，在 GitHub 仓库的 **Settings → Pages** 中把 Source 设为 **GitHub Actions**。

预期地址：

```text
订阅页：https://YOUR_GITHUB_ACCOUNT.github.io/personal-podcast/
RSS：https://YOUR_GITHUB_ACCOUNT.github.io/personal-podcast/feed.xml
```

仓库和 Releases 都是公开的，因此订阅源与已发布音频也是公开链接。请只处理你有权下载和发布的内容。

## 自动转写

发布 Release 后，[转写工作流](.github/workflows/transcribe.yml) 会在 GitHub Actions 的 Linux 运行器上使用 whisper.cpp 多语言 `small` 模型生成 TXT、SRT 和 VTT，并上传回同一个 Release。也可以在 Actions 页面手动运行，填写 Release 标签并选择 `small` 或 `medium` 模型。

工作流完成后运行：

```bash
personal-podcast transcript EPISODE_ID --sync-site
```

TXT 会以 `episode-id - 视频名称.txt` 保存到 `$PODCAST_ROOT/Transcripts/YYYY/`。文件开头依次记录视频名称、作者、原链接和导入时间，再写入音频文本。节目原简介继续单独保留，生成 RSS 时只取 TXT 中的音频文本并附加误差提示，因此重复生成站点不会重复元数据或全文。

## 音频规则

| 源音频 | 处理 | 最终格式 |
| --- | --- | --- |
| AAC | 重新封装，不重编码 | M4A |
| MP3 | 重新封装，不重编码 | MP3 |
| Opus / Vorbis / FLAC / 其他 | AAC-LC 转码 | M4A |

程序不剪辑、不降噪、不调速，不改变声道或采样率。标题、作者、专辑和简介会写入成品音频。为提高 Apple Podcasts 等客户端的兼容性，M4A 只包含一条 AAC 音轨，不内嵌封面或额外数据轨；节目和单集封面仍通过 RSS 提供。每次音频内容变化时，RSS 下载地址会自动追加新的内容版本，避免客户端继续使用旧文件缓存。

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
