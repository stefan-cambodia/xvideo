# xvideo

[![Tests](https://github.com/stefan-cambodia/xvideo/actions/workflows/tests.yml/badge.svg)](https://github.com/stefan-cambodia/xvideo/actions/workflows/tests.yml)

Download the videos of **public** X (Twitter) posts from the command line.

```console
$ xvideo "https://x.com/KillaXBT/status/2107623471824138680"
X Video Downloader

Fetching post...
✓ Video found
  Resolution: 3840x2160
  Duration: 00:25
  Size: 34.3 MB

Downloading ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 34.3/34.3 MB 5.8 MB/s 0:00:00

✓ Saved:
  ./KillaXBT_2107623471824138680.mp4
```

## Responsible use

xvideo only downloads content that is **publicly accessible without signing in**.
It does not read cookies or credentials, and it has no way around private or
protected accounts, age-restricted posts, paywalls, geo-restrictions, rate limits,
anti-bot checks or CAPTCHAs. You are responsible for respecting X's Terms of Service
and the copyright of the content you download: only download videos you have the
right to keep, for example your own posts or content whose license allows it.

## Features

- Downloads the video of a post, or every video of a multi-video post
- Several URLs per run, or a URL file (`--file`). A failing URL never stops the batch
- Quality selection (`best`, `worst`, `1080p`, `720p`…). Falls back to the best lower quality
- Safe, portable file names (`USERNAME_POSTID.mp4`) with no collisions (`_1`, `_2`…)
- Optional metadata embedding (`--metadata`)
- Progress bar, `--quiet`, `--verbose` and machine-readable `--json` output
- Configurable timeout, retries with backoff, resumable downloads (`.part` files)
- Clear error messages. No stack traces unless `--verbose`
- Linux, macOS and Windows

## Requirements

- Python 3.11+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (installed automatically). It handles the X
  extraction.
- [ffmpeg](https://ffmpeg.org/download.html) (**optional**). It is only used when it is
  really needed:
  - to merge separate audio and video streams, when a quality is only offered that way
    (X normally offers ready-to-play MP4 files, which need no ffmpeg);
  - to embed metadata with `--metadata`.

## Installation

```bash
# Recommended: isolated install of the command
pipx install /path/to/xvideo

# Or in a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install /path/to/xvideo        # or `pip install -e ".[dev]"` for development
```

X changes its website and API regularly. If extraction stops working, update yt-dlp
first:

```bash
pip install -U yt-dlp
```

## Usage

```bash
# One post
xvideo "https://x.com/USERNAME/status/POST_ID"

# Several posts
xvideo URL1 URL2 URL3

# From a file (one URL per line, blank lines and '#' comments are ignored)
xvideo --file urls.txt
cat urls.txt | xvideo --file -

# Output directory (created if needed)
xvideo URL --output ./downloads

# Quality
xvideo URL --quality best      # default
xvideo URL --quality 1080p
xvideo URL --quality 720p
xvideo URL --quality worst

# Existing files
xvideo URL --overwrite         # replace instead of adding _1, _2…
xvideo URL --skip-existing     # do nothing if the file already exists

# Metadata, output modes
xvideo URL --metadata
xvideo URL --quiet
xvideo URL --verbose
xvideo URL --json
```

`python -m xvideo …` works too.

Accepted URLs: `x.com` and `twitter.com` (also with `www.`, `mobile.` or `m.`), for example
`https://x.com/user/status/123`, `https://twitter.com/i/web/status/123` and
`https://x.com/user/status/123/video/2` (only the 2nd video of the post). Query strings
such as `?s=20` are ignored. If the same post appears several times in a batch, it is
downloaded only once.

### Options

| Option | Description |
| --- | --- |
| `-f, --file PATH` | Read URLs from a file (`-` = stdin). Can be combined with URL arguments. |
| `-o, --output DIR` | Output directory (default: current directory). |
| `-q, --quality QUALITY` | `best` (default), `worst`, or a maximum resolution: `2160p`, `1440p`, `1080p`, `720p`, `480p`, `360p`, `4k`… |
| `--overwrite` | Overwrite existing files. |
| `--skip-existing` | Skip videos whose file already exists. |
| `-m, --metadata` | Embed metadata in the video (needs ffmpeg). |
| `--quiet` | No output except errors. |
| `-v, --verbose` | Debug logs, plus stack traces on errors. |
| `--json` | JSON Lines output on stdout. |
| `--timeout SECONDS` | Network timeout (default 30). |
| `--retries N` | Retries on network errors (default 3, exponential backoff). |
| `--ffmpeg-location PATH` | ffmpeg binary, or the directory that contains it (default: search `PATH`). |
| `-V, --version` | Show the version. |

### Quality selection

A resolution refers to the **short side** of the frame, so `720p` matches both
1280×720 and portrait 720×1280 videos. When the requested resolution doesn't exist,
xvideo picks the best one **lower than or equal to** it, and shows a warning. For
example, `480p` gives 640×360 if X only offers 270p, 360p, 720p and 1080p. If every
resolution is higher than the requested one, the lowest is used.

At equal resolution, a single progressive MP4 is preferred over separate HLS streams.
That way ffmpeg is only needed when it is actually required.

### File names

Files are named `USERNAME_POSTID.mp4` (for example `KillaXBT_2107623471824138680.mp4`).
Posts with several videos give `USERNAME_POSTID_v1.mp4`, `USERNAME_POSTID_v2.mp4`…

- Characters that are invalid on Windows, macOS or Linux are replaced. Reserved Windows
  names (`CON`, `NUL`…) and overly long names are handled too.
- An existing file is never overwritten unless you pass `--overwrite`. The new file gets
  a suffix instead: `KillaXBT_2107623471824138680_1.mp4`, then `_2`… Files written during
  the same run never collide either, even with `--overwrite`.

### Metadata

With `--metadata`, these MP4 tags are written without re-encoding, so the video stays
playable everywhere:

| Tag | Content |
| --- | --- |
| `title` | Post title (author name + beginning of the text) |
| `artist` | `Display Name (@handle)` |
| `date` | Post date (ISO 8601, UTC) |
| `description` / `synopsis` | Full post text |
| `comment` | Author, post ID, original URL, date, resolution, duration |

The file is rewritten into a temporary file, then atomically swapped in, so an error can
never leave a half-written video. If ffmpeg is missing, the video is still saved, and a
warning says the metadata was not embedded.

### JSON output

`--json` prints **one JSON object per line** (JSON Lines) on stdout, one per downloaded
video or failed URL. Nothing else is printed on stdout, and `--verbose` logs go to stderr.

```json
{"url": "https://x.com/KillaXBT/status/2107623471824138680", "success": true, "filename": "./downloads/KillaXBT_2107623471824138680.mp4", "path": "/home/me/downloads/KillaXBT_2107623471824138680.mp4", "resolution": "1920x1080", "width": 1920, "height": 1080, "duration": 25, "filesize": 9464003, "post_id": "2107623471824138680", "username": "KillaXBT", "video_index": 1, "video_count": 1, "format_id": "http-10368", "quality": "1080p", "metadata": false, "skipped": false, "warnings": []}
{"url": "https://x.com/jack/status/20", "success": false, "error": "The post does not contain an accessible video.", "error_type": "no_video"}
```

`duration` is in seconds and `filesize` in bytes. `error_type` is one of: `invalid_url`,
`not_found`, `no_video`, `private`, `unavailable`, `network`, `timeout`, `http_error`,
`rate_limited`, `ffmpeg_missing`, `ffmpeg_failed`, `unsupported_format`, `disk_full`,
`permission_denied`, `file_exists`, `extraction_failed`.

```bash
xvideo --file urls.txt --json | jq -r 'select(.success) | .path'
```

### Exit status

| Code | Meaning |
| --- | --- |
| 0 | Every video was downloaded (or skipped with `--skip-existing`) |
| 1 | At least one URL failed |
| 2 | Invalid usage (bad option, no URL, unreadable `--file`…) |
| 130 | Interrupted (Ctrl+C). Partial downloads resume on the next run. |

### Errors

Errors are explained in plain language:

```text
✗ Unable to download video

Reason:
The post does not contain an accessible video.

URL:
https://x.com/jack/status/20
```

The following cases are handled: invalid URL, missing or deleted post, post without
video, private, protected or age-restricted post, withheld video, network unavailable,
timeout, HTTP errors (including X rate limiting), ffmpeg missing, unsupported/DRM
format, disk full (checked before the download when the size is known), permission
denied, and extraction failures. `--verbose` adds the technical details and the stack
trace.

### Network

- `--timeout` sets the socket timeout. `--retries` sets how many times a network error,
  timeout or 5xx server error is retried during extraction and download, with
  exponential backoff. X rate limiting (HTTP 429) is reported and **not** retried
  aggressively.
- Interrupted downloads keep a `.part` file and resume from where they stopped.
- Exact file sizes are read from the server (HTTP `HEAD`). The bitrates X advertises
  overestimate sizes 2 to 3 times.
- yt-dlp's default HTTP headers are used. There is no User-Agent rotation or any other
  evasion technique.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest            # unit tests, no network access needed
ruff check .      # lint
ruff format .     # format
mypy              # strict type checking
```

### Architecture

```text
src/xvideo/
├── cli.py         Typer CLI: options, validation, exit codes, logging setup
├── console.py     Output: rich progress/console, --quiet and --json reporters
├── downloader.py  Orchestration: URL → post → format → file; DownloadListener events
├── extractor.py   Post/video extraction through yt-dlp, retries, size probing
├── selector.py    Quality → format selection (progressive MP4 vs HLS + merge)
├── ytdlp.py       yt-dlp options, logging bridge, error translation
├── models.py      Data classes: PostRef, Quality, VideoFormat, VideoInfo, DownloadResult
├── filenames.py   Portable file names and collision-free allocation
├── metadata.py    Metadata tags for --metadata
├── ffmpeg.py      Locating and running ffmpeg (metadata embedding)
├── exceptions.py  Error hierarchy with user messages and stable error codes
└── utils.py       URL parsing, URL files, size/duration formatting
```

`Downloader` never prints anything. It reports progress through a `DownloadListener`,
so the same code powers the terminal UI, `--quiet`, `--json` and use as a library:

```python
from pathlib import Path
from xvideo.downloader import Downloader, DownloadOptions
from xvideo.models import Quality

options = DownloadOptions(output_dir=Path("downloads"), quality=Quality.parse("720p"))
with Downloader(options) as downloader:
    for result in downloader.run(["https://x.com/USERNAME/status/POST_ID"]):
        print(result.to_dict())
```

## License

MIT, see [LICENSE](LICENSE).
