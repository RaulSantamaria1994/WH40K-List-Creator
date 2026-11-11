#!/usr/bin/env python3
"""Keep the Claude Code copy of the skill identical to the canonical one.

Canonical:  .agents/skills/<skill>/   (Cursor, Gemini CLI, Codex / ChatGPT desktop read this)
Mirror:     .claude/skills/<skill>/   (Claude Code only reads this path)

    python tools/sync_skill.py          # copy canonical -> mirror
    python tools/sync_skill.py --check  # exit 1 if they differ (for hooks / CI)

Edit only the canonical copy. Standard library only.
"""
import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = "wh40k-list-builder"
SRC = ROOT / ".agents" / "skills" / SKILL
DST = ROOT / ".claude" / "skills" / SKILL
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def files(base: Path) -> set:
    out = set()
    for p in base.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            out.add(p.relative_to(base).as_posix())
    return out


def diff() -> list:
    if not DST.exists():
        return ["(mirror missing)"]
    a, b = files(SRC), files(DST)
    bad = [f"only in .agents: {f}" for f in sorted(a - b)]
    bad += [f"only in .claude: {f}" for f in sorted(b - a)]
    bad += [f"differs: {f}" for f in sorted(a & b)
            if not filecmp.cmp(SRC / f, DST / f, shallow=False)]
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="only verify, do not copy")
    args = ap.parse_args()
    if not SRC.is_dir():
        print(f"ERROR: canonical skill not found: {SRC}", file=sys.stderr)
        return 2
    if args.check:
        bad = diff()
        if bad:
            print("Mirror out of date; run: python tools/sync_skill.py", file=sys.stderr)
            print("\n".join(bad), file=sys.stderr)
            return 1
        print("OK: .claude/skills mirror matches .agents/skills")
        return 0
    if DST.exists():
        shutil.rmtree(DST)
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, DST, ignore=IGNORE)
    print(f"Synced {SRC.relative_to(ROOT)} -> {DST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
