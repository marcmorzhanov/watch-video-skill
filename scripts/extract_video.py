#!/usr/bin/env python3
"""
Extract transcript + time-synced frames from a YouTube URL or local video file.

Pipeline:
  1. Resolve source: YouTube URL -> download via yt-dlp; local path -> use as-is.
  2. Try YouTube captions first (manual > auto). Fall back to Whisper if missing.
  3. Extract frames at a configurable interval via ffmpeg.
  4. Align each frame with the transcript segment spoken at that timestamp.
  5. Emit a JSON manifest describing the pipeline output.

This script is dumb on purpose: it produces raw data (frames + transcript + manifest).
The SKILL.md tells Claude how to analyze that data and write the human-readable .md
summary. Keeping presentation in the skill makes the pipeline testable in isolation.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------- small utilities ----------

def run(cmd, check=True, capture=True):
    """Run a subprocess, return CompletedProcess. Raises on non-zero when check=True.
    On failure, surface stderr/stdout to the caller's stderr so errors aren't swallowed."""
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        if capture:
            if e.stdout:
                print(e.stdout, file=sys.stderr)
            if e.stderr:
                print(e.stderr, file=sys.stderr)
        raise


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def slugify(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:maxlen] or "video"


# ---------- source resolution ----------

def download_youtube(url: str, workdir: Path) -> tuple[Path, dict]:
    """Download video + auto-subs via yt-dlp. Return (video_path, info_dict)."""
    # Dump metadata first so we know the title / duration before downloading
    info_proc = run([
        sys.executable, "-m", "yt_dlp",
        "--dump-single-json",
        "--no-warnings",
        url,
    ])
    info = json.loads(info_proc.stdout)

    out_template = str(workdir / "%(id)s.%(ext)s")
    # YouTube's current SABR/PO-token restrictions block most web-client formats,
    # so we fall back to the android player client which still serves format 18
    # (360p combined audio+video) without a PO token. 360p is fine for extracting
    # stills — we're not re-encoding or displaying the video.
    run([
        sys.executable, "-m", "yt_dlp",
        "--extractor-args", "youtube:player_client=android,web",
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*,en",
        "--convert-subs", "srt",
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-warnings",
        url,
    ])

    vid_id = info["id"]
    # yt-dlp merges to .mp4 but can also land as .mkv/.webm depending on formats
    for ext in ("mp4", "mkv", "webm"):
        candidate = workdir / f"{vid_id}.{ext}"
        if candidate.exists():
            return candidate, info
    die(f"yt-dlp finished but no video file found in {workdir}")


def get_duration_seconds(video: Path) -> float:
    proc = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video),
    ])
    return float(proc.stdout.strip())


# ---------- transcript: captions first, whisper fallback ----------

def parse_srt(srt_text: str) -> list[dict]:
    """Return [{'start': sec, 'end': sec, 'text': str}, ...]."""
    segments = []
    # Normalize line endings and strip BOM
    srt_text = srt_text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\n+", srt_text.strip())
    ts_re = re.compile(
        r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
    )
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        ts_line = lines[1] if ts_re.search(lines[1]) else (
            lines[0] if ts_re.search(lines[0]) else None
        )
        if not ts_line:
            continue
        m = ts_re.search(ts_line)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        # text lines come after the timestamp line
        ts_idx = lines.index(ts_line)
        text = " ".join(lines[ts_idx + 1:]).strip()
        # YouTube auto-caption formatting tags
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return dedupe_rolling(segments)


def dedupe_rolling(segments: list[dict]) -> list[dict]:
    """YouTube auto-captions emit rolling duplicates; merge adjacent repeats."""
    out = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        # Drop segments that are strict prefixes of the next one (rolling captions)
        if out and seg["text"].startswith(out[-1]["text"]) and len(seg["text"]) > len(out[-1]["text"]):
            out[-1] = seg
            continue
        out.append(seg)
    return out


def try_caption_files(workdir: Path) -> list[dict] | None:
    """Look for en subtitles yt-dlp wrote to workdir. Prefer manual over auto."""
    srt_files = sorted(workdir.glob("*.srt"))
    if not srt_files:
        return None
    # Prefer a file that does NOT contain 'auto' in its name (manual subs)
    manual = [p for p in srt_files if "auto" not in p.name.lower()]
    chosen = manual[0] if manual else srt_files[0]
    try:
        text = chosen.read_text(encoding="utf-8", errors="replace")
        segs = parse_srt(text)
        return segs if segs else None
    except Exception as e:
        print(f"WARN: failed to parse {chosen.name}: {e}", file=sys.stderr)
        return None


def transcribe_with_whisper(video: Path, model_name: str) -> list[dict]:
    """Fall back to local whisper. Requires openai-whisper installed."""
    try:
        import whisper  # type: ignore
    except ImportError:
        die(
            "No YouTube captions available and openai-whisper is not installed.\n"
            "Install with: py -3.9 -m pip install --user openai-whisper\n"
            "Or pass --no-whisper to skip transcript extraction."
        )
    print(f"Loading Whisper model '{model_name}'...", file=sys.stderr)
    model = whisper.load_model(model_name)
    print("Transcribing (this can take a while)...", file=sys.stderr)
    result = model.transcribe(str(video), verbose=False)
    return [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in result.get("segments", [])
    ]


# ---------- frame extraction ----------

def extract_frames(video: Path, out_dir: Path, interval_sec: float) -> list[dict]:
    """Extract one frame every interval_sec seconds. Return list of frame dicts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = 1.0 / interval_sec
    pattern = str(out_dir / "frame_%05d.jpg")
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps={fps},scale=640:-2",
        "-qscale:v", "3",
        pattern,
    ])
    frames = []
    for p in sorted(out_dir.glob("frame_*.jpg")):
        # frame_00001 => t=0s for first frame (ffmpeg fps filter)
        idx = int(p.stem.split("_")[1]) - 1
        timestamp = idx * interval_sec
        frames.append({"path": str(p), "timestamp": round(timestamp, 3)})
    return frames


# ---------- alignment ----------

def segment_at(segments: list[dict], t: float) -> dict | None:
    """Find the transcript segment covering timestamp t (or nearest prior)."""
    if not segments:
        return None
    covering = [s for s in segments if s["start"] <= t <= s["end"]]
    if covering:
        return covering[0]
    # Nearest-prior fallback (gaps between segments)
    prior = [s for s in segments if s["end"] <= t]
    return prior[-1] if prior else None


def align(frames: list[dict], segments: list[dict]) -> list[dict]:
    for f in frames:
        seg = segment_at(segments, f["timestamp"])
        f["spoken_text"] = seg["text"] if seg else ""
        f["segment_start"] = seg["start"] if seg else None
        f["segment_end"] = seg["end"] if seg else None
    return frames


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Extract transcript + synced frames from a video.")
    ap.add_argument("source", help="YouTube URL or local video file path")
    ap.add_argument("--output-dir", required=True, help="Directory to write frames + manifest")
    ap.add_argument("--interval", type=float, default=1.0, help="Seconds between frames (default 1)")
    ap.add_argument("--whisper-model", default="base", help="Whisper model size (tiny/base/small/medium/large)")
    ap.add_argument("--no-whisper", action="store_true", help="Skip whisper fallback if captions are unavailable")
    args = ap.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Working directory for downloads + intermediate artifacts.
    # We keep this under out_dir/_work so the caller can inspect on failure.
    work_dir = out_dir / "_work"
    work_dir.mkdir(exist_ok=True)

    source = args.source
    info = {}

    if is_url(source):
        print(f"Downloading: {source}", file=sys.stderr)
        video, info = download_youtube(source, work_dir)
        title = info.get("title", "video")
        video_id = info.get("id", "")
        duration = info.get("duration") or get_duration_seconds(video)
        uploader = info.get("uploader", "")
    else:
        video = Path(source).resolve()
        if not video.exists():
            die(f"Video file not found: {video}")
        title = video.stem
        video_id = ""
        duration = get_duration_seconds(video)
        uploader = ""

    print(f"Video: {title} ({duration:.1f}s)", file=sys.stderr)

    # Transcript: try caption files first, then whisper
    segments = try_caption_files(work_dir) if is_url(source) else None
    transcript_source = "captions" if segments else None

    if not segments and not args.no_whisper:
        segments = transcribe_with_whisper(video, args.whisper_model)
        transcript_source = f"whisper-{args.whisper_model}"

    if not segments:
        segments = []
        transcript_source = "none"

    print(f"Transcript: {len(segments)} segments ({transcript_source})", file=sys.stderr)

    # Frames
    frames_dir = out_dir / "frames"
    print(f"Extracting frames every {args.interval}s...", file=sys.stderr)
    frames = extract_frames(video, frames_dir, args.interval)
    print(f"Extracted {len(frames)} frames", file=sys.stderr)

    # Align
    aligned = align(frames, segments)

    manifest = {
        "source": source,
        "title": title,
        "video_id": video_id,
        "uploader": uploader,
        "duration_sec": round(float(duration), 2),
        "interval_sec": args.interval,
        "transcript_source": transcript_source,
        "frames_dir": str(frames_dir),
        "frame_count": len(aligned),
        "transcript_segments": segments,
        "frames": aligned,
        "slug": slugify(title),
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Manifest: {manifest_path}", file=sys.stderr)
    print(json.dumps({"manifest": str(manifest_path), "frames_dir": str(frames_dir),
                      "frame_count": len(aligned), "transcript_source": transcript_source,
                      "duration_sec": manifest["duration_sec"], "title": title,
                      "slug": manifest["slug"]}))


if __name__ == "__main__":
    main()
