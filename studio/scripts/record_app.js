// Screen-record the Practice Builder app (index.html) for promo footage.
// node studio/scripts/record_app.js  -> studio/footage/app/*.webm
const { chromium } = require('playwright');
const path = require('path');
const ROOT = path.resolve(__dirname, '../..');
const OUT = path.join(ROOT, 'studio/footage/app');
const URL = 'file://' + path.join(ROOT, 'index.html');
const HIDE = `iframe, .video, [class*="video"] { display:none !important; }
  html { scroll-behavior: auto !important; }`;

// Record at the exact output resolution with deviceScaleFactor 1 (Playwright never
// upscales high-DPI frames; it pads them with grey), then use CSS zoom to make the UI
// big and sharp. `mobile` forces the app's phone layout rules.
const DEVICES = {
  '16x9': { viewport: { width: 1920, height: 1080 }, zoom: 1.5, mobile: false },
  '9x16': { viewport: { width: 1080, height: 1920 }, zoom: 2.5, mobile: true },
};

async function take(name, fn, fmt = '16x9') {
  const d = DEVICES[fmt];
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: d.viewport, deviceScaleFactor: 1,
    recordVideo: { dir: OUT, size: d.viewport } });
  const p = await ctx.newPage();
  await p.goto(URL);
  await p.addStyleTag({ content: HIDE });
  await p.evaluate(([zoom, mobile]) => {
    document.documentElement.style.zoom = zoom;
    if (!mobile) return;
    let css = '';
    for (const sh of document.styleSheets) { let rules; try { rules = sh.cssRules; } catch { continue; }
      for (const r of rules) if (r.media && /max-width:\s*520px/.test(r.conditionText || r.media.mediaText))
        for (const x of r.cssRules) css += x.cssText + '\n'; }
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  }, [d.zoom, d.mobile]);
  await p.evaluate(() => window.scrollTo(0, 0));
  await p.waitForTimeout(800);
  await fn(p);
  const v = p.video();
  await ctx.close();
  await v.saveAs(path.join(OUT, `${name}-${fmt}.webm`));
  await v.delete();
  await b.close();
  console.log('recorded', name, fmt);
}

async function smoothScroll(p, to, ms) {
  await p.evaluate(async ([to, ms]) => {
    const from = window.scrollY, t0 = performance.now();
    await new Promise(r => { const step = t => { const k = Math.min(1, (t - t0) / ms);
      const e = k < .5 ? 2*k*k : 1 - Math.pow(-2*k + 2, 2) / 2;
      window.scrollTo(0, from + (to - from) * e); k < 1 ? requestAnimationFrame(step) : r(); };
      requestAnimationFrame(step); });
  }, [to, ms]);
}

(async () => {
  for (const fmt of ['16x9', '9x16']) {
    const k = fmt === '9x16' ? 1.6 : 0.85;  // tuned per layout
    await take('hero', async p => { await p.waitForTimeout(3000); await smoothScroll(p, 260 * k, 2500); await p.waitForTimeout(600); }, fmt);
    await take('plan-scroll', async p => { await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(1500);
      await smoothScroll(p, 2600 * k, 7000); await p.waitForTimeout(800); }, fmt);
    await take('drill-library', async p => {
      await p.getByRole('button', { name: 'Drill Library' }).first().click();
      await p.waitForTimeout(1500); await smoothScroll(p, 1400 * k, 4500); await p.waitForTimeout(800); }, fmt);
  }
})();
