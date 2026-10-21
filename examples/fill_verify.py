#!/usr/bin/env python3
"""Fill this example's scaffold with reviewed, runnable todo recipes."""
import argparse
from pathlib import Path
import shutil


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("skill_dir", type=Path)
    args = ap.parse_args()
    fixtures = Path(__file__).resolve().parent / "fixtures/verify-todo"
    expected = {p.name for p in fixtures.glob("*.md")}
    found = {p.name for p in (args.skill_dir / "features").glob("*.md") if p.name != "README.md"}
    if found != expected:
        ap.error(f"scaffold feature inventory changed: expected {expected}, got {found}")
    index = args.skill_dir / "features/README.md"
    text = index.read_text()
    marker = '- TODO(verify): seed data the journeys need, created through the app\'s own routes or commands (or "none").'
    if marker not in text:
        ap.error("expected scaffold seed-data question is missing")
    index.write_text(text.replace(marker, '- None at launch. Each mutation recipe names its seed command; a fresh store starts IDs at 1.'))
    entry = args.skill_dir / 'SKILL.md'
    entry.write_text(entry.read_text().replace("--expect-json 'TODO(verify)'", "--expect-json '.=[]'"))
    for fixture in fixtures.glob("*.md"):
        shutil.copyfile(fixture, args.skill_dir / "features" / fixture.name)
    print(f"filled {len(expected)} feature recipes with the example's real commands")


if __name__ == "__main__":
    main()
