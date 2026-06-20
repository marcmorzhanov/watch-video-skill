#!/usr/bin/env python3
"""Delete a /watch working directory (extracted frames + downloaded video + audio).

Frames are NEVER kept by design — they live in a throwaway temp dir and must be
removed once Claude has read them. This is the final step of every /watch run.

Safety: refuses to delete a directory whose name does not contain "watch", so a
mistyped path can't wipe an unrelated folder. The pipeline always names its work
dir `watch-XXXX` (tempfile prefix) or whatever `--out-dir` the user passed.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def cleanup(target: Path, force: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.exists():
        print(f"[watch] nothing to clean: {target}")
        return 0
    if not target.is_dir():
        print(f"[watch] refusing to delete {target} (not a directory)", file=sys.stderr)
        return 3
    if not force and "watch" not in target.name.lower():
        print(
            f"[watch] refusing to delete {target} — name does not contain 'watch'. "
            f"Pass --force to override.",
            file=sys.stderr,
        )
        return 3
    shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        print(f"[watch] could not fully remove {target}", file=sys.stderr)
        return 1
    print(f"[watch] removed work dir: {target}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    if not args:
        print("usage: cleanup.py <work-dir> [--force]", file=sys.stderr)
        return 2
    return cleanup(Path(args[0]), force=force)


if __name__ == "__main__":
    raise SystemExit(main())
