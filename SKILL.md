---
name: watch-video
description: Use when the user wants Claude to watch, analyze, review, or take notes on a video — whether a YouTube URL or a local video file. Extracts a time-synced transcript plus still frames at a configurable interval, then produces a detailed markdown notes/summary file describing what was said and shown throughout.
---

# Watch Video

Claude can't stream video directly. This skill fakes it: it pulls a transcript (YouTube captions first, Whisper fallback), extracts still frames every N seconds, aligns each frame with the sentence spoken at that timestamp, then Claude reads the frames + transcript and writes a markdown notes file.

## When to invoke

Any of these: "watch this video," "take notes on this YouTube video," "analyze this reel," "summarize this video," "what happens in this video," followed by a YouTube URL or a local video file path.

## Dependencies (verify before first run)

- **ffmpeg** on PATH (`ffmpeg -version`) — for frame extraction
- **yt-dlp** as a Python module (`python -m yt_dlp --version`) — for YouTube downloads and caption fetching
- **openai-whisper** (optional fallback, installed via `python -m pip install --user openai-whisper`) — only needed if a video has no captions

If any dependency is missing, surface the exact install command and stop — do not guess around a broken environment.

## Pipeline

The work happens in `scripts/extract_video.py`. It's a data-only tool: it writes frames + a `manifest.json` and exits. Claude does the understanding.

```
python scripts/extract_video.py \
  "<youtube-url-or-local-path>" \
  --output-dir "<working-dir>" \
  --interval 1.0
```

Flags:
- `--interval N` — seconds between frames. Default 1.0. For long videos (>10 min), bump this to 2 or 3 to keep frame count reasonable; for short reels, leave at 1.
- `--whisper-model MODEL` — `tiny` / `base` / `small` / `medium` / `large`. Only used when captions are missing. Default `base`.
- `--no-whisper` — skip transcription entirely if captions are missing. Produces frames-only output.

The script prints a one-line JSON to stdout on success:
```json
{"manifest": "...", "frames_dir": "...", "frame_count": N, "transcript_source": "captions|whisper-base|none", "duration_sec": ..., "title": "...", "slug": "..."}
```

## Step-by-step workflow

### 1. Pick a working directory

Default: a system temp directory (e.g. `$TMPDIR/watch-video/<slug>/` on macOS/Linux, `%TEMP%\watch-video\<slug>\` on Windows). Cleaned up at the end — see step 6.

If the user says "save notes somewhere permanent," ask where and use that path for the `.md` file specifically; frames still go to temp.

### 2. Run the extractor

Use the default 1-second interval unless the video is long:
- ≤ 5 min: `--interval 1`
- 5–15 min: `--interval 2`
- \> 15 min: `--interval 3` or ask the user

Parse the stdout JSON to get the manifest path, frame count, and transcript source.

### 3. Read the manifest

```
Read: <output-dir>/manifest.json
```

The manifest has `transcript_segments` (start/end/text) and `frames` (path, timestamp, spoken_text). Frame paths are absolute.

### 4. Look at the frames

**Sample, don't brute-force.** Reading every frame into context is wasteful for anything over a minute. Strategy:

- ≤ 30s video: read every frame.
- 30s–3min: read frames every ~5s plus any frame where the spoken_text suggests a topic shift.
- \> 3min: read frames every ~10s plus boundary frames at transcript topic shifts.

Use the `Read` tool on the frame paths — they're JPEGs and Claude will see them visually. Always pair each frame with the `spoken_text` field for that frame so you know what was being said.

### 5. Write the summary

Output file: `<output-dir>/<slug>-notes.md` (or the user's chosen path).

Structure the markdown like this:

```markdown
# <Title>

**Source:** <URL or local path>
**Duration:** <mm:ss>
**Uploader:** <if from YouTube>
**Transcript source:** <captions / whisper-base / none>

## One-line summary
<≤20 words — the core claim or hook of the video>

## TL;DR
<3–5 bullet points capturing the main arguments, moments, or beats>

## Timeline
- **[00:00]** <what's happening visually + the key line being said>
- **[00:15]** ...
<one row per meaningful beat, not per frame>

## Key quotes
> "<verbatim quote>" — [mm:ss]

## Visual notes
<what the video shows that the transcript alone would miss — setting, B-roll, on-screen text, graphics, transitions, subject's emotion>

## Takeaways (optional)
<Only include if the content is directly relevant to the user's domain or goals. Omit otherwise.>
```

### 6. Clean up

After the `.md` is written, delete the `_work/` subdirectory and the `frames/` subdirectory inside the working directory. Keep the `.md` and the `manifest.json` (manifest is small and useful if the user wants a second pass).

If the working directory was under a system temp path, deleting `_work/` and `frames/` is safe. If the user specified a non-temp output dir, ask before deleting frames.

## Common gotchas

- **YouTube Shorts / age-gated / members-only** — yt-dlp may fail. Surface the yt-dlp error verbatim; don't retry silently.
- **No captions + no whisper installed** — script exits with a clear error telling the user how to install whisper. Relay that message; don't try to work around it.
- **Local file with no audio track** — whisper will error. Pass `--no-whisper` to get frames-only output, and note "no transcript" in the summary.
- **Very long videos (>30 min)** — confirm with the user before running. A 60-min video at 1-second intervals = 3600 frames. Use `--interval 5` or higher.
- **Rolling auto-captions** — YouTube's auto-captions emit duplicate rolling text. The parser deduplicates, but if a transcript looks weirdly repetitive, that's the source.

## What NOT to do

- Don't attempt to "watch" a video by inventing content based on the title or thumbnail. If the pipeline fails, say so.
- Don't read every single frame for videos over a minute — sample intelligently.
- Don't write the summary before actually looking at frames. The transcript alone misses visual context (B-roll, on-screen text, emotion, graphics).
- Don't skip the cleanup step. Frame dumps are large.
