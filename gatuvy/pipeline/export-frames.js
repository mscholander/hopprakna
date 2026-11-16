#!/usr/bin/env node
// Steg 1 i filmkedjan: plocka ut nyckelbilder längs en rutt och spara dem som vanliga "rakt fram"-bilder.
//
//   node export-frames.js --from "Musée du Louvre, Paris" --to "Arc de Triomphe, Paris" --out work/paris --count 11
//
// Skriptet öppnar Gatuvy-sidan i en osynlig webbläsare (Playwright) och återanvänder dess bildval,
// stabilisering och 360°-omprojicering. Bilderna hämtas i originalupplösning för bästa skärpa.
// Flaggor: --spacing <meter> hur tätt rutten provtas (4; välj 2 för att få med alla foton), --start <index> första bild, --count <antal>, --step <n> var n:te bild, --size 1280x720,
//          --provider both|panoramax|mapillary, --token <Mapillary-token>, --curl-relay (hämta rutt via curl)
const fs = require('fs'), path = require('path'), http = require('http'), cp = require('child_process');
const { chromium } = require('playwright');
const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i < 0 ? d : (process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : true); };
const from = arg('from'), to = arg('to'), out = arg('out', 'work/route');
if (!from || !to) { console.error('Ange --from och --to'); process.exit(1); }
const start = +arg('start', 0), count = +arg('count', 11), step = +arg('step', 1);
const [W, H] = String(arg('size', '1280x720')).split('x').map(Number);
const provider = arg('provider', arg('token') ? 'both' : 'panoramax');

// liten filserver för sidan (mappen ovanför)
const root = path.resolve(__dirname, '..');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.webmanifest': 'application/json' };
const server = http.createServer((q, r) => {
  const f = path.join(root, decodeURIComponent(q.url.split('?')[0]).replace(/^\/+/, '') || 'index.html');
  if (!f.startsWith(root) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'content-type': types[path.extname(f)] || 'application/octet-stream' }); fs.createReadStream(f).pipe(r);
});

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: '127.0.0.1,localhost' } : undefined;
  const browser = await chromium.launch({ proxy, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await (await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 })).newPage();
  page.on('pageerror', e => console.error('sidfel:', e.message));
  if (arg('curl-relay')) {     // vissa nät släpper inte igenom ruttjänstens adresser från webbläsaren
    const relay = strip => r => { let u = r.request().url(); if (strip) u = u.replace(/&access_token=[^&]*/, '');
      cp.execFile('curl', ['-s', '-m', '60', u], { maxBuffer: 1 << 28, encoding: 'buffer' }, (e, body) =>
        (e ? r.fulfill({ status: 502, body: '' }) : r.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body })).catch(() => {})); };
    await page.route('**/route/v1/**', relay(false));
    if (arg('token') === 'proxy') await page.route('https://graph.mapillary.com/**', relay(true));
  }
  await page.goto(`http://127.0.0.1:${port}/index.html?x=`);
  const meta = await page.evaluate(async ({ from, to, provider, token, start, count, step, spacing }) => {
    if (token) document.getElementById('token').value = token;
    const a = await geocode(from); await new Promise(r => setTimeout(r, 1100)); const b = await geocode(to);
    const r = await route(a, b), samples = resample(r.geometry.coordinates, spacing);
    const { images } = await fetchImagesAlong(samples, provider, () => {});
    const { frames, coverage } = buildFrames(samples, images);
    window.__frames = frames.filter((_, i) => i >= start && (i - start) % step === 0).slice(0, count);
    return { km: r.distance / 1000, total: frames.length, coverage, picked: window.__frames.length };
  }, { from, to, provider, token: arg('token', ''), start, count, step, spacing: +arg('spacing', 4) });
  console.log(`Rutt ${meta.km.toFixed(1)} km, ${meta.total} bilder (täckning ${Math.round(meta.coverage * 100)} %), exporterar ${meta.picked}`);
  fs.mkdirSync(out, { recursive: true });
  const list = [];
  for (let i = 0; i < meta.picked; i++) {
    // en bild i taget: ladda originalet, rikta in det mot föregående bild och rita vyn rakt fram
    const r = await page.evaluate(async i => {
      const fr = window.__frames, f = fr[i], prev = i ? fr[i - 1] : null;
      await loadFrame(f, 'hd'); if (!f.ok) return null;
      align(prev && prev.ok ? prev : null, f);
      draw(f, null, 0, f.im.pano ? 1 : 1.3, 1, f.brg);
      const data = document.getElementById('view').toDataURL('image/jpeg', 0.95);
      if (prev && prev.ok && i > 1) { const pp = fr[i - 2]; if (pp.tex) { gl.deleteTexture(pp.tex); pp.tex = null; pp.img = null; } }
      return { data, lon: f.im.p[0], lat: f.im.p[1], bearing: f.brg, pano: f.im.pano, credit: f.im.credit, taken: f.im.t, src: f.im.hd,
        gap: prev ? dist(prev.im.p, f.im.p) : 0 };
    }, i);
    if (!r) { console.log(`  bild ${i}: kunde inte laddas, hoppar över`); continue; }
    const name = `key_${String(list.length).padStart(4, '0')}.jpg`;
    fs.writeFileSync(path.join(out, name), Buffer.from(r.data.split(',')[1], 'base64'));
    delete r.data; list.push({ file: name, ...r });
    console.log(`  ${name}  ${r.gap.toFixed(1)} m från förra  ${r.credit}`);
  }
  fs.writeFileSync(path.join(out, 'frames.json'), JSON.stringify({ from, to, size: [W, H], frames: list }, null, 1));
  await browser.close(); server.close();
})().catch(e => { console.error(e); process.exit(1); });
