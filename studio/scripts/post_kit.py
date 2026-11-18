"""Package a rendered edit into a ready-to-post kit (Brad posts these by hand while
Instagram API publishing is parked).

    python3 studio/scripts/post_kit.py studio/edits/<name>.json [--cover-at 4.5]

Writes studio/ready/<name>/:
    reel-9x16.mp4      Instagram/Facebook Reel, Stories, TikTok, Shorts
    video-16x9.mp4     Facebook feed, YouTube
    cover-9x16.jpg     Reel cover (frame from the hook)
    caption.txt        caption + the fixed CTA (appended if missing) + hashtags (max 8)
    README.txt         posting checklist
"""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
BRAND = json.loads((STUDIO / "brand/brand.json").read_text())

ap = argparse.ArgumentParser()
ap.add_argument("edit")
ap.add_argument("--cover-at", type=float, default=4.5, help="seconds into the 9:16 video")
a = ap.parse_args()

edit_path = Path(a.edit)
edit = json.loads(edit_path.read_text())
name = edit.get("name", edit_path.stem)
src = STUDIO / "renders" / name
out = STUDIO / "ready" / name
out.mkdir(parents=True, exist_ok=True)

files = {"9x16": "reel-9x16.mp4", "16x9": "video-16x9.mp4", "1x1": "feed-1x1.mp4", "4x5": "feed-4x5.mp4"}
made = []
for fmt, dst in files.items():
    f = src / f"{name}-{fmt}.mp4"
    if f.exists():
        shutil.copy2(f, out / dst)
        made.append(dst)

reel = src / f"{name}-9x16.mp4"
if reel.exists():
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(a.cover_at), "-i", str(reel),
                    "-frames:v", "1", "-q:v", "2", str(out / "cover-9x16.jpg")], check=True)
    made.append("cover-9x16.jpg")

cta = BRAND["cta"]["caption"]
caption = edit.get("caption", "").strip()
if cta not in caption:
    caption = (caption + "\n\n" + cta).strip()
tags = edit.get("hashtags", [])[:8]
if tags:
    caption += "\n\n" + " ".join(tags)
if len(caption) > 2200:
    raise SystemExit(f"caption is {len(caption)} chars; Instagram's limit is 2,200")
(out / "caption.txt").write_text(caption + "\n")
made.append("caption.txt")

(out / "README.txt").write_text(f"""{name}: post kit

1. Instagram Reel: reel-9x16.mp4, cover cover-9x16.jpg, paste caption.txt.
   Add trending audio in the app only if you want it (the API can't attach it).
2. Facebook Page: video-16x9.mp4 (or the Reel), same caption.
3. Story: reel-9x16.mp4 with link sticker "Sign up as a coach" -> cheatcodecoaching.app,
   text "Nobody gets skipped."

Before posting: no real child's face, name or number; no price, "free" or trial terms;
no claim we can't back up.
""")
made.append("README.txt")
print(f"post kit -> {out.relative_to(STUDIO.parent)}: " + ", ".join(made))
