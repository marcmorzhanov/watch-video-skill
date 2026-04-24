# watch-video skill

A Claude Code skill that lets Claude "watch" videos by extracting a time-synced transcript plus still frames, then reading them together to produce structured notes.

## What it does

Claude models can see images but can't stream video. This skill fakes video comprehension:

1. Pulls a transcript — YouTube captions first, [Whisper](https://github.com/openai/whisper) as a local fallback.
2. Extracts still frames at a configurable interval via ffmpeg.
3. Aligns each frame with the sentence being spoken at that timestamp.
4. Claude reads frames + transcript and writes a markdown notes file (one-line summary, TL;DR, timestamped timeline, key quotes, visual notes).

Works with YouTube URLs and local video files.

## Install

Clone into your Claude skills folder:

```bash
# macOS / Linux
git clone https://github.com/<your-username>/watch-video-skill.git ~/.claude/skills/watch-video

# Windows
git clone https://github.com/<your-username>/watch-video-skill.git %USERPROFILE%\.claude\skills\watch-video
```

### Dependencies

- **ffmpeg** — [download](https://ffmpeg.org/download.html) or `brew install ffmpeg` / `choco install ffmpeg`
- **yt-dlp** — `python -m pip install --user yt-dlp`
- **openai-whisper** (optional, only needed for videos without captions) — `python -m pip install --user openai-whisper`

## Usage

Once installed, just ask Claude to watch a video:

```
Watch this: https://www.youtube.com/watch?v=...
Take notes on this reel: https://...
Summarize this video: /path/to/local/video.mp4
```

Claude invokes the skill automatically when the request matches.

## Direct CLI usage

The extractor can run standalone:

```bash
python scripts/extract_video.py "<url-or-path>" --output-dir ./out --interval 1.0
```

Flags:
- `--interval N` — seconds between frames (default 1.0)
- `--whisper-model MODEL` — `tiny` / `base` / `small` / `medium` / `large` (default `base`)
- `--no-whisper` — skip transcription fallback

Outputs a `manifest.json` with frame paths, timestamps, and aligned transcript segments.

## Troubleshooting

**Windows + broken Python pip:** On some Windows setups, Python 3.12 and 3.13 ship with pip installs that fail on yt-dlp / whisper. If your default Python chokes, install Python 3.9 via [python.org](https://www.python.org/downloads/) and invoke explicitly: `py -3.9 -m pip install --user yt-dlp`.

**yt-dlp fails on YouTube Shorts / age-gated content:** The script uses the `android` player client as a fallback, which works for most cases. Members-only and region-locked content may still fail — yt-dlp will surface a clear error.

**"No module named whisper":** Either install whisper (`python -m pip install --user openai-whisper`) or pass `--no-whisper` to get frames-only output.

## License

MIT — see [LICENSE](LICENSE).
