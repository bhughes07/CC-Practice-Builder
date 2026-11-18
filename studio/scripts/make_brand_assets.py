"""Generate background plates and sound effects for the Cheat Code studio.
Run with system python (needs numpy + Pillow):  python3 studio/scripts/make_brand_assets.py
"""
import json, wave
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
BRAND = json.loads((ROOT / "brand/brand.json").read_text())
BLUE = np.frombuffer(bytes.fromhex(BRAND["colors"]["blue"][1:]), np.uint8).astype(np.float32) / 255
BASE = np.frombuffer(bytes.fromhex(BRAND["colors"]["night"][1:]), np.uint8).astype(np.float32) / 255


def plate(w, h, out):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2, h * 0.36
    r = np.sqrt(((x - cx) / w) ** 2 + ((y - cy) / h) ** 2 * (h / w) ** 2 * 1.0)
    glow = np.exp(-(r / 0.33) ** 2)[..., None]
    vign = np.clip(1 - 0.55 * (np.sqrt(((x - w/2)/w)**2 + ((y - h/2)/h)**2) / 0.7) ** 2, 0, 1)[..., None]
    img = BASE + glow * BLUE * 0.22
    img = img * vign
    im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB")
    # faint yard lines, perspective-free, tilted slightly for energy
    lines = Image.new("L", (w * 2, h * 2), 0)
    d = ImageDraw.Draw(lines)
    step = max(w, h) // 9
    for i in range(-20, 40):
        X = i * step
        d.line([(X, 0), (X, h * 2)], fill=26, width=max(2, w // 640))
        for j in range(0, h * 2, step // 5):  # hash marks
            d.line([(X + step // 2 - 8, j), (X + step // 2 + 8, j)], fill=14, width=2)
    lines = lines.rotate(-12, resample=Image.BICUBIC).crop((w // 2, h // 2, w // 2 + w, h // 2 + h))
    lines = lines.filter(ImageFilter.GaussianBlur(0.6))
    white = Image.new("RGB", (w, h), (255, 255, 255))
    im = Image.composite(white, im, lines)
    # film grain-ish dither to stop gradient banding
    n = (np.random.default_rng(1).normal(0, 2.2, (h, w, 1))).astype(np.float32)
    a = np.clip(np.asarray(im).astype(np.float32) + n, 0, 255).astype(np.uint8)
    Image.fromarray(a).save(out)


def write_wav(path, sig, sr=48000):
    sig = np.clip(sig / (np.abs(sig).max() + 1e-9) * 0.89, -1, 1)
    st = np.stack([sig, sig], 1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes((st * 32767).astype("<i2").tobytes())


def sfx():
    sr = 48000
    rng = np.random.default_rng(7)
    # whoosh: band-passed noise with rising then falling centre frequency
    T = 0.55; t = np.arange(int(sr * T)) / sr
    noise = rng.normal(0, 1, t.size)
    spec = np.fft.rfft(noise); f = np.fft.rfftfreq(t.size, 1 / sr)
    out = np.zeros_like(t)
    # do it in short overlapping blocks for a sweep
    blk = 1024; hop = 512; win = np.hanning(blk)
    for s in range(0, t.size - blk, hop):
        c = 300 + 2600 * np.sin(np.pi * s / t.size) ** 2
        seg = np.fft.rfft(noise[s:s + blk] * win)
        ff = np.fft.rfftfreq(blk, 1 / sr)
        seg *= np.exp(-((np.log(ff + 1) - np.log(c)) / 0.6) ** 2)
        out[s:s + blk] += np.fft.irfft(seg, blk)
    env = np.sin(np.pi * t / T) ** 2
    write_wav(ROOT / "brand/sfx/whoosh.wav", out * env)
    # impact: pitched-down sine thump + click + short noise tail
    T = 1.4; t = np.arange(int(sr * T)) / sr
    freq = 38 + 90 * np.exp(-t * 18)
    thump = np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 4.0)
    click = rng.normal(0, 1, t.size) * np.exp(-t * 90) * 0.5
    tail = rng.normal(0, 1, t.size) * np.exp(-t * 7) * 0.08
    write_wav(ROOT / "brand/sfx/impact.wav", thump + click + tail)
    # tick: short bright blip for text pops
    T = 0.12; t = np.arange(int(sr * T)) / sr
    write_wav(ROOT / "brand/sfx/tick.wav", np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 60))


def music_bed(seconds=60, bpm=112):
    """Royalty-free placeholder hype bed: kick/snare/hat groove + sub bass + pad."""
    sr = 48000; rng = np.random.default_rng(3)
    n = int(sr * seconds); out = np.zeros(n)
    beat = 60 / bpm
    def place(sig, t, g=1.0):
        i = int(t * sr); j = min(n, i + sig.size)
        if i < n: out[i:j] += sig[:j - i] * g
    tk = np.arange(int(sr * 0.35)) / sr
    kick = np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-tk * 30)) / sr) * np.exp(-tk * 9)
    ts = np.arange(int(sr * 0.22)) / sr
    snare = (rng.normal(0, 1, ts.size) * 0.6 + np.sin(2 * np.pi * 190 * ts)) * np.exp(-ts * 18)
    th = np.arange(int(sr * 0.05)) / sr
    hat = np.diff(rng.normal(0, 1, th.size + 1)) * np.exp(-th * 80)
    roots = [41.2, 41.2, 49.0, 36.7]  # E1 E1 G1 D1
    bars = int(seconds / (4 * beat)) + 1
    for bar in range(bars):
        t0 = bar * 4 * beat
        for k in (0, 1.5, 2.5):
            place(kick, t0 + k * beat, 1.0)
        for k in (1, 3):
            place(snare, t0 + k * beat, 0.55)
        for k in range(8):
            place(hat, t0 + k * beat / 2, 0.18 if k % 2 else 0.28)
        f = roots[bar % 4]
        tb = np.arange(int(sr * 4 * beat)) / sr
        bass = np.tanh(2.2 * np.sin(2 * np.pi * f * tb)) * 0.35 * (1 - np.exp(-tb * 30))
        pad = sum(np.sin(2 * np.pi * f * 4 * r * tb + r) for r in (1, 1.26, 1.5)) * 0.03
        place(bass + pad, t0)
    fade = int(sr * 2)
    out[-fade:] *= np.linspace(1, 0, fade)
    write_wav(ROOT / "brand/sfx/music-bed.wav", out)


if __name__ == "__main__":
    for name, (w, h) in BRAND["formats"].items():
        plate(w, h, ROOT / f"brand/plates/plate-{name}.png")
    sfx()
    music_bed()
    print("brand assets written")
