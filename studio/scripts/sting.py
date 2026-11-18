"""Cheat Code Coaching animated logo sting (intro / outro).

The white C and blue C slam together, the play-route draws on, then the
wordmark and tagline land. Sound design (whoosh, impact, ticks) is built in.

    blender -b --factory-startup --python studio/scripts/sting.py -- \
        --format 16x9 --out studio/renders/sting-16x9.mp4 [--blend out.blend] [--stills dir]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy  # noqa: E402
import cc_blender as cc  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--format", default="16x9")
p.add_argument("--out", required=True)
p.add_argument("--blend")
p.add_argument("--stills", help="write a few PNG frames here instead of the full video")
p.add_argument("--seconds", type=float, default=3.6)
a = p.parse_args(cc.script_args())

sc = cc.new_scene(a.format, a.seconds)
W, H = sc.render.resolution_x, sc.render.resolution_y
N = sc.frame_end
portrait = H > W

# layout: mark scale and vertical anchors for each aspect
mark_scale = (W * 0.70 if portrait else W * 0.34) / 843
mark_y = H * (0.11 if portrait else 0.165)          # px above centre
word_y = 0.5 - (0.06 if portrait else 0.165)       # fraction of height (0=bottom)
word_size = int(W * (0.115 if portrait else 0.062))
sub_size = int(word_size * 0.36)
tag_size = int(word_size * 0.32)
line_gap = word_size / H

# ---- layers ---------------------------------------------------------------
cc.image(sc, str(cc.BRAND_DIR / f"plates/plate-{a.format}.png"), 1, 1, N, "plate")

def mark_layer(key, ch):
    s = cc.image(sc, cc.brand_path(key), ch, 1, N, key)
    s.transform.scale_x = s.transform.scale_y = mark_scale
    s.transform.offset_y = mark_y
    return s

white = mark_layer("mark_white_c", 2)
blue = mark_layer("mark_blue_c", 3)
route = mark_layer("mark_route", 4)

slam = 14  # frame the two Cs meet
cc.key(white, "transform.offset_x", [(1, -W * 0.75), (slam, 0)], interp="QUAD", easing="EASE_IN")
cc.key(blue, "transform.offset_x", [(1, W * 0.75), (slam, 0)], interp="QUAD", easing="EASE_IN")
for s in (white, blue):
    cc.key(s, "blend_alpha", [(1, 0.0), (6, 1.0)], interp="LINEAR")
    # impact punch
    cc.key(s, "transform.scale_x", [(slam, mark_scale * 1.10), (slam + 9, mark_scale)], interp="BACK")
    cc.key(s, "transform.scale_y", [(slam, mark_scale * 1.10), (slam + 9, mark_scale)], interp="BACK")
    # slow push-in for the hold so it never feels static
    cc.key(s, "transform.offset_y", [(slam + 9, mark_y), (N, mark_y + H * 0.008)], interp="LINEAR")

# route draws on left -> right by animating the crop
route.blend_alpha = 1.0
cc.key(route, "crop.max_x", [(slam + 2, 843), (slam + 16, 0)], interp="SINE", easing="EASE_IN_OUT")
cc.key(route, "transform.offset_y", [(slam + 9, mark_y), (N, mark_y + H * 0.008)], interp="LINEAR")

# impact flash
flash = cc.color(sc, cc.hex_rgba("#FFFFFF", 0.0), 9, slam, 10, "flash")
cc.key(flash, "blend_alpha", [(slam, 0.55), (slam + 9, 0.0)], interp="EXPO", easing="EASE_OUT")

# ---- wordmark ---------------------------------------------------------------
t0 = slam + 12
gap = 0.012
cheat = cc.text(sc, "CHEAT", 5, t0, N - t0 + 1, size=word_size, x=0.5 - gap, y=word_y,
                anchor_x="RIGHT")
code = cc.text(sc, "CODE", 6, t0, N - t0 + 1, size=word_size, col=cc.BRAND["colors"]["blue"],
               x=0.5 + gap, y=word_y, anchor_x="LEFT")
for s, dx in ((cheat, -0.04), (code, 0.04)):
    s.use_outline = True
    s.outline_color = (0, 0, 0, 1)
    s.outline_width = 0.06
    x0 = s.location[0]
    cc.key(s, "location", [(t0, (x0 + dx, word_y)), (t0 + 10, (x0, word_y))], interp="EXPO")
    cc.key(s, "blend_alpha", [(t0, 0.0), (t0 + 6, 1.0)], interp="LINEAR")

t1 = t0 + 8
coaching = cc.text(sc, "C O A C H I N G", 7, t1, N - t1 + 1, font="heading", size=sub_size,
                   y=word_y - line_gap * 0.95)
cc.fade(coaching, fin=8)

t2 = t1 + 8
tag = cc.text(sc, cc.BRAND["tagline"].upper(), 8, t2, N - t2 + 1, font="heading", size=tag_size,
              col=cc.BRAND["colors"]["blue"], y=word_y - line_gap * 1.55)
cc.fade(tag, fin=10)

# fade to black at the tail
out = cc.color(sc, cc.hex_rgba("#000000", 0.0), 10, N - 10, 11, "fade_out")
cc.key(out, "blend_alpha", [(N - 10, 0.0), (N, 1.0)], interp="SINE")

# ---- sound -------------------------------------------------------------------
sfx = cc.BRAND_DIR / "sfx"
cc.sound(sc, str(sfx / "whoosh.wav"), 11, 1, 0.9)
cc.sound(sc, str(sfx / "impact.wav"), 12, slam - 1, 1.0)
cc.sound(sc, str(sfx / "tick.wav"), 13, t0, 0.35)
cc.sound(sc, str(sfx / "tick.wav"), 14, t1, 0.25)

if a.blend:
    bpy.ops.file.pack_all()
    cc.save_blend(a.blend)

if a.stills:
    for f in (6, slam + 2, slam + 10, t1 + 4, N - 14):
        cc.render_still(sc, f, Path(a.stills) / f"sting-{a.format}-f{f:03d}.png")
else:
    cc.render_mp4(sc, a.out)
