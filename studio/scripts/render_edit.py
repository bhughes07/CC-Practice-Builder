"""Render an edit described by a JSON "edit decision list" into finished videos.

    blender -b --factory-startup --python studio/scripts/render_edit.py -- studio/edits/<name>.json \
        [--format 9x16] [--stills] [--blend-only]

Edit JSON (all times in seconds; title/caption times are on the BODY timeline,
i.e. 0 = first frame after the intro sting):

{
  "name": "practice-builder-promo",
  "formats": ["16x9", "9x16"],
  "intro_sting": true,               # Cheat Code logo sting before the body
  "outro_sting": true,               # ...and after it
  "logo_bug": true,                  # small mark in the corner during the body
  "transition": 0.3,                 # crossfade seconds between body items (0 = hard cut)
  "music": {"src": "footage/track.mp3", "volume": 0.35, "start": 0},
  "clips": [
    {"src": "footage/a.mp4", "in": 2.0, "out": 6.5, "volume": 1.0, "fit": "FILL",
     "src_9x16": "footage/a-vertical.mp4",                           # optional per-format source
     "scale_16x9": 1.3},                                             # optional punch-in (or "scale")
    {"image": "footage/photo.jpg", "dur": 3, "zoom": "in"},           # Ken Burns
    {"card": {"title": "BUILD PRACTICE", "subtitle": "in 5 minutes"}, "dur": 2.2}
  ],
  "titles": [
    {"text": "Coach Mike", "sub": "Head Coach, 10U", "at": 1.0, "dur": 3, "style": "lower_third"},
    {"text": "STOP WINGING PRACTICE", "at": 4, "dur": 2, "style": "punch"},
    {"text": "Short burned-in caption", "at": 7, "dur": 2, "style": "caption"}
  ],
  "captions": "footage/voiceover.srt"   # optional SRT, burned in as captions
}
Paths are relative to the studio/ folder (or absolute).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy  # noqa: E402
import cc_blender as cc  # noqa: E402

STUDIO = cc.STUDIO
CACHE = STUDIO / "renders" / ".cache"
BLUE = cc.BRAND["colors"]["blue"]


def P(rel):
    p = Path(rel)
    return p if p.is_absolute() else STUDIO / p


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    j = json.loads(out.stdout)
    return {"duration": float(j["format"]["duration"]),
            "audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def conform(src, fps):
    """Re-encode to constant frame rate, H.264 + 48k AAC so Blender timing is exact.
    Phone footage is usually variable-frame-rate, which drifts out of sync otherwise."""
    src = P(src)
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"{src.stem}-{fps}fps-{int(src.stat().st_mtime)}.mp4"
    if not dst.exists():
        info = probe(src)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
        if not info["audio"]:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
        cmd += ["-vf", f"fps={fps},format=yuv420p", "-c:v", "libx264", "-crf", "14", "-preset", "fast",
                "-g", str(fps), "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "256k", str(dst)]
        subprocess.run(cmd, check=True)
    return dst


def ensure_sting(fmt):
    path = STUDIO / "renders" / "brand" / f"cc-sting-{fmt}.mp4"
    if not path.exists():
        subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--python",
                        str(STUDIO / "scripts" / "sting.py"), "--", "--format", fmt, "--out", str(path)],
                       check=True)
    return path


def finish_audio(mp4):
    """Loudness-normalise to -14 LUFS / -1 dBTP (social platform standard), video untouched."""
    mp4 = Path(mp4)
    tmp = mp4.with_suffix(".tmp.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-c:v", "copy",
                    "-af", "loudnorm=I=-14:TP=-1.0:LRA=11", "-ar", "48000",
                    "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(mp4)


def parse_srt(path):
    def t(s):
        h, m, rest = s.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    blocks = re.split(r"\n\s*\n", Path(path).read_text(encoding="utf-8-sig").strip())
    out = []
    for b in blocks:
        lines = b.strip().splitlines()
        m = next((l for l in lines if "-->" in l), None)
        if not m:
            continue
        a, z = [x.strip() for x in m.split("-->")]
        txt = " ".join(lines[lines.index(m) + 1:])
        out.append({"text": txt, "at": t(a), "dur": t(z) - t(a), "style": "caption"})
    return out


def fit_size(text, base, W, frac=0.88, em=0.43):
    """Shrink display text so one line fits frac of the frame width (Anton ~0.43em/char)."""
    return int(min(base, W * frac / max(1, len(text)) / em))


def build(edit, fmt):
    fps = edit.get("fps", cc.BRAND["fps"])
    sc = cc.new_scene(fmt, 1, fps)
    W, H = sc.render.resolution_x, sc.render.resolution_y
    portrait = H > W
    F = lambda sec: int(round(sec * fps))  # noqa: E731
    xf = F(edit.get("transition", 0))
    pos = 1
    top_channel = 20

    # ---- intro ----
    if edit.get("intro_sting", True):
        s = ensure_sting(fmt)
        m = cc.movie(sc, str(s), 2, pos)
        snd = cc.sound(sc, str(s), 30, pos)
        pos = m.frame_final_end
    body_start = pos

    # ---- body ----
    ch_toggle = 0
    plate = str(cc.BRAND_DIR / f"plates/plate-{fmt}.png")
    for i, c in enumerate(edit["clips"]):
        ch = 3 + ch_toggle
        ch_toggle ^= 1
        start = pos - (xf if i > 0 else 0)
        if "src" in c:
            # per-format override, e.g. "src_9x16": a vertical recording of the same shot
            src = conform(c.get(f"src_{fmt}", c["src"]), fps)
            info = probe(src)
            a, b = F(c.get("in", 0)), F(c.get("out", info["duration"]))
            v = cc.movie(sc, str(src), ch, start - a, fit=c.get("fit", "FILL"))
            punch = c.get(f"scale_{fmt}", c.get("scale"))  # static punch-in, e.g. 1.3
            if punch:
                v.transform.scale_x = v.transform.scale_y = punch
            v.frame_final_start, v.frame_final_end = start, start - a + b
            snd = cc.sound(sc, str(src), 31 + ch_toggle, start - a, c.get("volume", 1.0))
            snd.frame_final_start, snd.frame_final_end = start, start - a + b
            item = v
        elif "image" in c:
            item = cc.strips(sc).new_image(Path(c["image"]).stem, str(P(c["image"])), ch, start,
                                           fit_method=c.get("fit", "FILL"))
            item.frame_final_duration = F(c.get("dur", 3)) + (xf if i > 0 else 0)
            z = c.get("zoom")
            if z:
                s0, s1 = (1.0, 1.12) if z == "in" else (1.12, 1.0)
                for prop in ("transform.scale_x", "transform.scale_y"):
                    cc.key(item, prop, [(item.frame_final_start, s0), (item.frame_final_end, s1)], interp="LINEAR")
        elif "card" in c:
            dur = F(c.get("dur", 2.5)) + (xf if i > 0 else 0)
            item = cc.image(sc, plate, ch, start, dur, "card")
            card = c["card"]
            size = fit_size(card["title"], int(W * (0.12 if portrait else 0.085)), W)
            t = cc.text(sc, card["title"], 12, start, dur, size=size, y=0.53)
            t.use_outline, t.outline_color, t.outline_width = True, (0, 0, 0, 1), 0.05
            cc.key(t, "transform.scale_x", [(start, 1.25), (start + 8, 1.0)], interp="EXPO")
            cc.key(t, "transform.scale_y", [(start, 1.25), (start + 8, 1.0)], interp="EXPO")
            cc.fade(t, fin=5, fout=4)
            if card.get("subtitle"):
                st = cc.text(sc, card["subtitle"].upper(), 13, start + 6, dur - 6, font="heading",
                             size=int(size * 0.38), col=BLUE, y=0.53 - size / H * 0.95)
                cc.fade(st, fin=6, fout=4)
        else:
            raise ValueError(f"clip {i}: needs src, image or card")
        if i > 0 and xf:
            cc.key(item, "blend_alpha", [(start, 0.0), (start + xf, 1.0)], interp="LINEAR")
        item.blend_type = "ALPHA_OVER"
        pos = item.frame_final_end
    body_end = pos

    # ---- logo bug ----
    if edit.get("logo_bug", True) and body_end > body_start:
        bug = cc.image(sc, cc.brand_path("mark"), 15, body_start, body_end - body_start, "logo_bug")
        bug.transform.scale_x = bug.transform.scale_y = (W * (0.16 if portrait else 0.075)) / 843
        margin = W * 0.035
        bw, bh = 843 * bug.transform.scale_x, 463 * bug.transform.scale_y
        # ORIGINAL fit centres the image; offsets move it to the top-right corner
        bug.transform.offset_x = W / 2 - margin - bw / 2
        bug.transform.offset_y = H / 2 - margin - bh / 2 - (H * 0.04 if portrait else 0)
        cc.fade(bug, fin=8, fout=8)
        bug.blend_alpha = 0.9

    # ---- titles & captions ----
    titles = list(edit.get("titles", []))
    if edit.get("captions"):
        titles += parse_srt(P(edit["captions"]))
    for t in titles:
        a = body_start + F(t["at"])
        d = F(t.get("dur", 3))
        style = t.get("style", "lower_third")
        if style == "lower_third":
            size = int(W * (0.07 if portrait else 0.04))
            y = 0.22 if portrait else 0.17
            x = 0.06
            main = cc.text(sc, t["text"].upper(), top_channel, a, d, size=size, x=x, y=y,
                           anchor_x="LEFT", box=(BLUE, 1.0))
            cc.key(main, "location", [(a, (x - 0.05, y)), (a + 10, (x, y))], interp="EXPO")
            cc.fade(main, fin=6, fout=6)
            if t.get("sub"):
                sub = cc.text(sc, t["sub"], top_channel + 1, a + 4, d - 4, font="heading",
                              size=int(size * 0.5), x=x + 0.004, y=y - size / H * 1.05,
                              anchor_x="LEFT", box=("#06080D", 0.85))
                cc.fade(sub, fin=6, fout=6)
        elif style == "punch":
            size = fit_size(t["text"], int(W * (0.13 if portrait else 0.095)), W)
            p = cc.text(sc, t["text"].upper(), top_channel, a, d, size=size, y=0.5)
            p.use_outline, p.outline_color, p.outline_width = True, (0, 0, 0, 1), 0.06
            p.use_shadow = True
            cc.key(p, "transform.scale_x", [(a, 1.4), (a + 7, 1.0)], interp="EXPO")
            cc.key(p, "transform.scale_y", [(a, 1.4), (a + 7, 1.0)], interp="EXPO")
            cc.fade(p, fin=3, fout=5)
            cc.sound(sc, str(cc.BRAND_DIR / "sfx/tick.wav"), 35, a, 0.3)
        elif style == "caption":
            size = int(W * (0.055 if portrait else 0.032))
            cap = cc.text(sc, t["text"], top_channel + 2, a, d, font="heading", size=size,
                          y=0.26 if portrait else 0.09, wrap=0.86, box=("#000000", 0.6))

    # ---- outro ----
    pos = body_end
    if edit.get("outro_sting", True):
        s = ensure_sting(fmt)
        m = cc.movie(sc, str(s), 2, pos)
        cc.sound(sc, str(s), 30, pos)
        pos = m.frame_final_end

    # ---- music under everything (fades in/out) ----
    mu = edit.get("music")
    if mu:
        ms = cc.sound(sc, str(P(mu["src"])), 38, 1 - F(mu.get("start", 0)), mu.get("volume", 0.35))
        ms.frame_final_start = 1
        ms.frame_final_end = min(ms.frame_final_end, pos)
        vol = mu.get("volume", 0.35)
        cc.key(ms, "volume", [(1, 0.0), (F(1.0), vol), (pos - F(1.5), vol), (pos, 0.0)], interp="SINE")

    sc.frame_end = pos - 1
    return sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edit")
    ap.add_argument("--format", help="render only this format")
    ap.add_argument("--stills", action="store_true", help="write preview frames only")
    ap.add_argument("--blend-only", action="store_true")
    a = ap.parse_args(cc.script_args())
    edit = json.loads(P(a.edit).read_text())
    name = edit.get("name", Path(a.edit).stem)
    fmts = [a.format] if a.format else edit.get("formats", ["16x9"])
    for fmt in fmts:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if not bpy.data.scenes:
            bpy.data.scenes.new("Scene")
        sc = build(edit, fmt)
        out_dir = STUDIO / "renders" / name
        out_dir.mkdir(parents=True, exist_ok=True)
        cc.save_blend(STUDIO / "projects" / f"{name}-{fmt}.blend")
        if a.blend_only:
            continue
        if a.stills:
            n = sc.frame_end
            for k in range(8):
                f = 1 + int(k * (n - 1) / 7)
                cc.render_still(sc, f, out_dir / "stills" / f"{fmt}-f{f:04d}.png")
        else:
            cc.render_mp4(sc, out_dir / f"{name}-{fmt}.mp4")
            finish_audio(out_dir / f"{name}-{fmt}.mp4")


if __name__ == "__main__":
    main()
