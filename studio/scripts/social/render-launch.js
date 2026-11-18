#!/usr/bin/env node
// Render the launch set (studio/scripts/social/launch-posts.json) to post kits.
//   node studio/scripts/social/render-launch.js [filter] [--sheets]
// Output: studio/ready/launch/<slug>/<folder>/01..06.jpg + caption.txt
// Working files (graded photos, html, png, sheets): studio/renders/launch/
// Brand values come from studio/brand/brand.json. Photos are the licensed Pexels pool in the
// cheat-code-coaching repo (read only), graded here with the house recipe from grade-photo.sh.
const fs = require('fs'); const path = require('path'); const { execFileSync } = require('child_process');

const STUDIO = path.resolve(__dirname, '..', '..');
const BRAND_DIR = path.join(STUDIO, 'brand');
const BRAND = JSON.parse(fs.readFileSync(path.join(BRAND_DIR, 'brand.json'), 'utf8'));
const DATA = JSON.parse(fs.readFileSync(path.join(__dirname, 'launch-posts.json'), 'utf8'));
const CCC = path.join(process.env.HOME, 'Documents', 'Bobcat Football');   // cheat-code-coaching, read only
const RAW = path.join(CCC, 'social', 'photos', 'raw');
const MEDIA = path.join(CCC, 'bobcat-site', 'media');
const OUT = path.join(STUDIO, 'ready', 'launch');
const WORK = path.join(STUDIO, 'renders', 'launch');
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const [W, H] = BRAND.formats['4x5'];
const args = process.argv.slice(2); const sheets = args.includes('--sheets');
const filter = args.find(a => !a.startsWith('--')) || '';

const C = BRAND.colors; const CTA = BRAND.cta;
const font = f => `file://${path.join(BRAND_DIR, f)}`;
const logo = f => `file://${path.join(BRAND_DIR, f)}`;
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const br = s => esc(s).replace(/\n/g, '<br>');
const pad = n => String(n).padStart(2, '0');
fs.mkdirSync(path.join(WORK, 'photos'), { recursive: true });

// House grade (design/instagram-board.md): lifted blacks, 85% saturation, soft-light wash, grain.
function gradePhoto(file, wash, lift) {
  const src = path.join(RAW, file); const out = path.join(WORK, 'photos', file.replace(/\.jpg$/, `-${wash}${lift ? '-lift' : ''}.jpg`));
  if (fs.existsSync(out)) return out;
  const dims = execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', src]).toString().trim().split(',');
  const w = Math.floor(+dims[0] / 2) * 2, h = Math.floor(+dims[1] / 2) * 2;
  const base = `[0:v]scale=${w}:${h},colorlevels=romin=0.10:gomin=0.11:bomin=0.13,eq=saturation=0.85:contrast=0.97${lift ? ':brightness=0.08:gamma=1.25' : ''}`;
  const grad = wash === 'warm' ? `gradients=s=${w}x${h}:c0=0xE0A458:c1=0x000000:x0=0:y0=0:x1=0:y1=${h}:d=1,format=rgb24[g]` : `gradients=s=${w}x${h}:c0=0x000000:c1=0x2A5BD7:x0=0:y0=0:x1=0:y1=${h}:d=1,format=rgb24[g]`;
  const op = wash === 'warm' ? 0.28 : 0.22;
  const f = `${base},format=rgb24[b];${grad};[b][g]blend=all_mode=softlight:all_opacity=${op},noise=alls=8:allf=t+u,format=yuvj420p[v]`;
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-i', src, '-filter_complex', f, '-map', '[v]', '-frames:v', '1', '-q:v', '2', out]);
  return out;
}

const CSS = `
<link href="https://fonts.googleapis.com/css2?family=Reenie+Beanie&display=swap" rel="stylesheet">
<style>
@font-face{font-family:Anton;src:url("${font(BRAND.fonts.display)}")}
@font-face{font-family:Oswald;src:url("${font(BRAND.fonts.heading)}");font-weight:200 700}
@font-face{font-family:Inter;src:url("${font(BRAND.fonts.body)}");font-weight:100 900}
:root{--blue:${C.blue};--night:${C.night};--white:${C.white};--steel:${C.steel};--ink:#14161c;--paper:#fffdf7}
*{box-sizing:border-box}html,body{margin:0;width:${W}px;height:${H}px;overflow:hidden;background:var(--night);color:var(--white);font-family:Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.s{position:relative;width:${W}px;height:${H}px;overflow:hidden;background:var(--night)}
.anton{font-family:Anton,Impact,sans-serif;text-transform:uppercase;line-height:.9;letter-spacing:.004em;font-weight:400}
.osw{font-family:Oswald,sans-serif;font-weight:600;text-transform:uppercase;letter-spacing:.14em}
.hand{font-family:'Reenie Beanie','Marker Felt',cursive;font-weight:400}
.grain{position:absolute;inset:0;pointer-events:none;opacity:.08;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 .9 0'/></filter><rect width='220' height='220' filter='url(%23n)'/></svg>")}
/* the mark's cut corner, echoed on chips, cards and frames */
.cut{clip-path:polygon(0 0,calc(100% - 22px) 0,100% 22px,100% 100%,22px 100%,0 calc(100% - 22px))}
.chip{position:absolute;left:64px;top:64px;background:var(--blue);color:#fff;font-size:26px;line-height:1;padding:18px 26px 16px;max-width:760px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.count{position:absolute;right:64px;top:76px;color:rgba(255,255,255,.72);font-size:26px}
.mark{position:absolute;right:44px;top:50px;width:160px;height:auto}
/* cover */
.photo{position:absolute;inset:0;background-size:cover;background-position:center}
.shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(11,13,18,.38) 0%,rgba(11,13,18,.06) 30%,rgba(11,13,18,.55) 58%,rgba(11,13,18,.94) 100%)}
.cover .head{position:absolute;left:64px;right:64px;bottom:290px;font-size:156px;color:#fff;text-shadow:0 4px 30px rgba(0,0,0,.35)}
.cover .sub{position:absolute;left:64px;right:64px;bottom:120px;font-size:36px;line-height:1.38;color:rgba(255,255,255,.92);max-width:860px;font-weight:500}
.cover .swipe{position:absolute;right:64px;bottom:62px;color:#fff;font-size:24px}
.cover .bar{position:absolute;left:64px;bottom:66px;width:120px;height:8px;background:var(--blue)}
/* index card on night */
.card{position:absolute;left:64px;right:64px;top:176px;bottom:200px;background:var(--paper);box-shadow:0 40px 90px rgba(0,0,0,.55);overflow:hidden;color:var(--ink)}
.card .red{position:absolute;left:0;right:0;top:252px;height:4px;background:#d94a43;opacity:.8}
.card .lines{position:absolute;left:0;right:0;top:256px;bottom:0;background:repeating-linear-gradient(180deg,transparent 0,transparent 70px,#a9bedc 70px,#a9bedc 72px)}
.card .head{position:absolute;left:56px;right:56px;top:34px;font-size:112px;color:var(--ink)}
.card .body{position:absolute;left:56px;right:56px;top:256px;font-size:42px;line-height:72px;color:var(--ink);font-weight:500}
.card .hand{position:absolute;right:52px;bottom:34px;color:#1b3fbf;font-size:50px;transform:rotate(-2.5deg);max-width:620px;text-align:right;line-height:1}
.foot{position:absolute;left:64px;right:64px;bottom:92px;color:rgba(255,255,255,.7);font-size:26px;letter-spacing:.1em;line-height:1.35;max-width:900px}
.foot b{color:#fff;font-weight:600}
/* screenshot slide on night */
.shot .col{position:absolute;left:64px;right:64px;top:176px}
.shot .head{font-size:104px;color:#fff}
.shot .body{font-size:36px;line-height:1.4;color:rgba(255,255,255,.88);margin-top:28px;max-width:900px;font-weight:500}
.shot .phone{position:absolute;left:240px;top:620px;width:600px;border-radius:44px;border:8px solid #2a2f3a;box-shadow:0 40px 90px rgba(0,0,0,.6);overflow:hidden;background:#fff}
.shot .phone img{display:block;width:100%;height:auto}
.shot .desk{position:absolute;left:64px;top:600px;width:1080px;height:800px;overflow:hidden;border-radius:18px 0 0 0;border-top:6px solid #2a2f3a;border-left:6px solid #2a2f3a;box-shadow:0 40px 90px rgba(0,0,0,.6);background:#f5f6f8}
.shot .desk img{display:block;position:absolute;left:-300px;top:0;width:1600px;height:auto}
/* CTA */
.cta .mark{left:44px;right:auto;top:64px;width:200px}
.cta .rule{position:absolute;left:64px;top:360px;width:160px;height:10px;background:var(--blue)}
.cta .head{position:absolute;left:64px;right:64px;top:420px;color:#fff;font-size:184px}
.cta .line{position:absolute;left:64px;right:64px;top:900px;color:#fff;font-size:36px;line-height:1.55;max-width:900px;letter-spacing:.08em}
.cta .url{position:absolute;left:64px;bottom:88px;color:var(--blue);font-size:28px;letter-spacing:.14em;background:#fff;padding:16px 24px 14px}
/* quote */
.quote .stack{position:absolute;left:64px;right:64px;bottom:76px;display:flex;flex-direction:column;gap:34px}
.quote .bar{width:120px;height:10px;background:var(--blue)}
.quote .head{color:#fff;font-size:112px;line-height:.96;text-shadow:0 4px 30px rgba(0,0,0,.35)}
.quote .stack .foot{position:static;color:rgba(255,255,255,.75)}
</style>`;

const FIT = `<script>
// Shrink any headline that would run past its box (3-line headlines, long quotes).
for (const [sel, max] of [['.card .head', 236], ['.cover .head', 480], ['.quote .head', 560], ['.shot .head', 240]]) {
  for (const el of document.querySelectorAll(sel)) {
    let fs = parseFloat(getComputedStyle(el).fontSize);
    while (el.scrollHeight > max && fs > 56) { fs -= 4; el.style.fontSize = fs + 'px'; }
  }
}
</script>`;
const whiteMark = logo(BRAND.logos.mark_white_c);
const chip = p => p.tag ? `<div class="osw chip cut">${esc(p.tag)}</div>` : '';
const count = (i, t) => `<div class="osw count">${pad(i)} / ${pad(t)}</div>`;

function coverSlide(p, photo, total) {
  const s = p.slides[0];
  return `<div class="s cover"><div class="photo" style="background-image:url('file://${photo}');background-position:${p.pos || 'center'}"></div><div class="shade"></div><div class="grain"></div>
    ${chip(p)}<img class="mark" src="${whiteMark}">
    <div class="anton head">${br(s.headline)}</div><div class="sub">${esc(s.body)}</div>
    <div class="bar"></div><div class="osw swipe">1 / ${total} &nbsp;→</div></div>`;
}
function cardSlide(p, s, i, total) {
  const hand = i === 2 && p.handwriting ? `<div class="hand">${esc(p.handwriting)}</div>` : '';
  return `<div class="s"><div class="grain"></div>${chip(p)}${count(i, total)}
    <div class="card cut"><div class="red"></div><div class="lines"></div><div class="anton head">${br(s.headline)}</div><div class="body">${esc(s.body)}</div>${hand}</div>
    <div class="osw foot">${esc(p.question || '')}</div></div>`;
}
function shotSlide(p, s, i, total) {
  const img = `file://${path.join(MEDIA, s.shot)}`;
  const frame = s.crop === 'desk' ? `<div class="desk"><img src="${img}"></div>` : `<div class="phone cut"><img src="${img}"></div>`;
  return `<div class="s shot"><div class="grain"></div>${chip(p)}${count(i, total)}
    <div class="col"><div class="anton head">${br(s.headline)}</div><div class="body">${esc(s.body)}</div></div>${frame}</div>`;
}
function ctaSlide() {
  return `<div class="s cta"><div class="grain"></div><img class="mark" src="${whiteMark}"><div class="rule"></div>
    <div class="anton head">${esc(CTA.headline)}</div><div class="osw line">${esc(CTA.line)}</div>
    <div class="osw url">cheatcodecoaching.app</div></div>`;
}
function quoteSlide(p, photo) {
  return `<div class="s cover quote"><div class="photo" style="background-image:url('file://${photo}');background-position:${p.pos || 'center'}"></div><div class="shade"></div><div class="grain"></div>
    <img class="mark" src="${whiteMark}"><div class="stack"><div class="bar"></div><div class="anton head">${esc(p.headline)}</div>
    <div class="osw foot">Cheat Code Coaching</div></div></div>`;
}

function render(html, out) {
  const base = out.replace(/\.jpg$/, ''); const tmp = path.join(WORK, 'html', path.basename(path.dirname(out)) + '-' + path.basename(base) + '.html');
  fs.mkdirSync(path.dirname(tmp), { recursive: true });
  fs.writeFileSync(tmp, `<!doctype html><html><head><meta charset="utf-8">${CSS}</head><body>${html}${FIT}</body></html>`);
  const png = tmp.replace(/\.html$/, '.png');
  for (let tries = 0; ; tries++) {
    try { execFileSync(CHROME, ['--headless=new', '--hide-scrollbars', '--disable-gpu', '--run-all-compositor-stages-before-draw', '--allow-file-access-from-files', `--window-size=${W},${H}`, '--force-device-scale-factor=1', '--virtual-time-budget=6000', `--screenshot=${png}`, `file://${tmp}`], { stdio: 'ignore' }); break; }
    catch (e) { if (tries >= 3) throw e; console.warn('  chrome failed, retrying', path.basename(png)); }
  }
  execFileSync('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', '90', png, '--out', out], { stdio: 'ignore' });
  // Headless Chrome occasionally leaves the bottom tile unpainted (a cream band). Detect by
  // sampling the bottom-left corner, which is always night or photo, never paper, and retry.
  const raw = execFileSync('ffmpeg', ['-loglevel', 'error', '-i', out, '-vf', 'crop=40:8:0:1340,format=gray', '-f', 'rawvideo', '-']);
  const mean = raw.reduce((a, b) => a + b, 0) / raw.length;
  if (mean > 235) { console.warn('  unpainted band, re-rendering', path.basename(out)); return render(html, out); }
}

function caption(p) {
  const tags = (DATA.hashtags[p.hashtags] || []).slice(0, 8).map(t => '#' + t).join(' ');
  return `${p.caption.trim()}\n\n${CTA.caption}\n\n${tags}\n`;
}

const posts = DATA.posts.filter(p => !filter || `${p.slug}/${p.folder}`.includes(filter));
const covers = [];
for (const p of posts) {
  const dir = path.join(OUT, p.slug, p.folder); fs.mkdirSync(dir, { recursive: true });
  const photo = gradePhoto(p.photo, p.wash || 'warm', p.lift);
  const files = [];
  if (p.kind === 'carousel') {
    const total = p.slides.length + 1;
    p.slides.forEach((s, idx) => {
      const n = idx + 1; const out = path.join(dir, `${pad(n)}.jpg`);
      render(n === 1 ? coverSlide(p, photo, total) : s.shot ? shotSlide(p, s, n, total) : cardSlide(p, s, n, total), out); files.push(out);
    });
    const out = path.join(dir, `${pad(total)}.jpg`); render(ctaSlide(), out); files.push(out);
  } else {
    const out = path.join(dir, '01.jpg'); render(quoteSlide(p, photo), out); files.push(out);
  }
  fs.writeFileSync(path.join(dir, 'caption.txt'), caption(p));
  covers.push(files[0]);
  if (sheets && files.length > 1) {
    const sheet = path.join(WORK, 'sheets', `${p.slug}--${p.folder}.jpg`); fs.mkdirSync(path.dirname(sheet), { recursive: true });
    const inputs = files.flatMap(f => ['-i', f]);
    execFileSync('ffmpeg', ['-loglevel', 'error', '-y', ...inputs, '-filter_complex', `${files.map((_, i) => `[${i}]`).join('')}concat=n=${files.length}:v=1:a=0,scale=360:450,tile=${files.length}x1:padding=6:margin=6`, sheet]);
  }
  console.log(`${p.slug}/${p.folder}: ${files.length} image(s)`);
}
if (sheets && covers.length > 1) {
  const sheet = path.join(WORK, 'sheets', 'all-covers.jpg'); fs.mkdirSync(path.dirname(sheet), { recursive: true });
  const cols = 5, rows = Math.ceil(covers.length / cols);
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', ...covers.flatMap(f => ['-i', f]), '-filter_complex', `${covers.map((_, i) => `[${i}]`).join('')}concat=n=${covers.length}:v=1:a=0,scale=360:450,tile=${cols}x${rows}:padding=8:margin=8`, sheet]);
  console.log('covers sheet:', sheet);
}
