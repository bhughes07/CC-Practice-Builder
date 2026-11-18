"""Shared helpers for driving Blender's Video Sequence Editor (VSE) headlessly.

Imported by the other studio scripts when they run inside Blender:
    blender --background --factory-startup --python studio/scripts/<script>.py -- <args>
"""
import json
import sys
from pathlib import Path

import bpy

STUDIO = Path(__file__).resolve().parents[1]
BRAND_DIR = STUDIO / "brand"
BRAND = json.loads((BRAND_DIR / "brand.json").read_text())


def script_args():
    """Arguments after the `--` separator on the blender command line."""
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def hex_rgba(h, a=1.0):
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return (*srgb, a)


def brand_path(key_or_rel):
    """Resolve a brand asset by brand.json key (logos/fonts) or relative path."""
    for group in ("logos", "fonts"):
        if key_or_rel in BRAND[group]:
            return str(BRAND_DIR / BRAND[group][key_or_rel])
    return str(BRAND_DIR / key_or_rel)


def new_scene(fmt="16x9", seconds=10.0, fps=None):
    fps = fps or BRAND["fps"]
    w, h = BRAND["formats"][fmt]
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start = 1
    scene.frame_end = int(round(seconds * fps))
    scene.view_settings.view_transform = "Standard"  # keep brand colours exact
    scene.sequence_editor_create()
    return scene


def strips(scene):
    se = scene.sequence_editor
    return se.strips if hasattr(se, "strips") else se.sequences


def new_effect(scene, name, kind, channel, start, length):
    """Blender 5.x takes `length`; 4.x takes `frame_end`."""
    try:
        return strips(scene).new_effect(name, kind, channel, start, length=length)
    except TypeError:
        return strips(scene).new_effect(name, kind, channel, start, frame_end=start + length)


def image(scene, path, channel, start, length, name=None):
    s = strips(scene).new_image(name or Path(path).stem, path, channel, start, fit_method="ORIGINAL")
    s.frame_final_duration = length
    s.blend_type = "ALPHA_OVER"
    return s


def movie(scene, path, channel, start, fit="FIT"):
    return strips(scene).new_movie(Path(path).stem, path, channel, start, fit_method=fit)


def sound(scene, path, channel, start, volume=1.0):
    s = strips(scene).new_sound(Path(path).stem, path, channel, start)
    s.volume = volume
    return s


def color(scene, rgba, channel, start, length, name="color"):
    s = new_effect(scene, name, "COLOR", channel, start, length)
    s.color = rgba[:3]
    s.blend_alpha = rgba[3]
    s.blend_type = "ALPHA_OVER"
    return s


def text(scene, txt, channel, start, length, *, font="display", size=120, col="#FFFFFF",
         x=0.5, y=0.5, anchor_x="CENTER", anchor_y="CENTER", shadow=False, box=None, wrap=0.0):
    s = new_effect(scene, txt[:20] or "text", "TEXT", channel, start, length)
    s.text = txt
    s.font = bpy.data.fonts.load(brand_path(font), check_existing=True)
    s.font_size = size
    s.color = hex_rgba(col)
    s.location = (x, y)
    s.anchor_x, s.anchor_y = anchor_x, anchor_y
    s.use_shadow = shadow
    if wrap:
        s.wrap_width = wrap
    if box:
        s.use_box = True
        s.box_color = hex_rgba(box[0], box[1] if len(box) > 1 else 1.0)[:4]
        s.box_margin = 0.012
    s.blend_type = "ALPHA_OVER"
    return s


# ---- keyframing ---------------------------------------------------------------

def _ease(fc, interp, easing):
    for kp in fc.keyframe_points:
        kp.interpolation = interp
        kp.easing = easing


def _fcurves(id_data):
    """F-curves of an ID's action: legacy `action.fcurves` (4.x) or slotted channelbag (5.x)."""
    ad = id_data.animation_data
    action = ad.action
    if hasattr(action, "fcurves"):
        return action.fcurves
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(action, ad.action_slot)
    return bag.fcurves if bag else []


def key(strip, path, frames_values, interp="BACK", easing="EASE_OUT", index=-1):
    """Keyframe strip property path (e.g. 'transform.offset_x', 'blend_alpha')."""
    owner_path, _, prop = path.rpartition(".")
    owner = strip.path_resolve(owner_path) if owner_path else strip
    for f, v in frames_values:
        setattr(owner, prop, v)
        owner.keyframe_insert(prop, frame=f, index=index)
    scene = strip.id_data
    full = owner.path_from_id(prop)
    for fc in _fcurves(scene):
        if fc.data_path == full:
            _ease(fc, interp, easing)


def fade(strip, fin=0, fout=0):
    a = strip.frame_final_start
    b = strip.frame_final_end
    pts = []
    if fin:
        pts += [(a, 0.0), (a + fin, 1.0)]
    if fout:
        pts += [(b - fout, 1.0), (b, 0.0)]
    if pts:
        key(strip, "blend_alpha", pts, interp="SINE", easing="EASE_IN_OUT")


def render_mp4(scene, out_path, crf="HIGH"):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = scene.render
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = crf
    r.ffmpeg.ffmpeg_preset = "GOOD"
    r.ffmpeg.gopsize = scene.render.fps
    r.ffmpeg.audio_codec = "AAC"
    r.ffmpeg.audio_bitrate = 256
    r.ffmpeg.audio_mixrate = 48000
    r.ffmpeg.audio_channels = "STEREO"
    r.filepath = str(out_path)
    r.use_file_extension = False
    bpy.ops.render.render(animation=True)
    print(f"[cc] rendered {out_path}")


def render_still(scene, frame, out_path):
    scene.frame_set(frame)
    r = scene.render
    r.image_settings.file_format = "PNG"
    r.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)


def save_blend(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=False)
