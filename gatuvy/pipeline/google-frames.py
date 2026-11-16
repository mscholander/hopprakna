#!/usr/bin/env python3
"""Nyckelbilder från Google Street View via Map Tiles API, i samma format som export-frames.js.

    python3 google-frames.py --from "Musée du Louvre, Paris" --to "Arc de Triomphe, Paris" --out work/paris_google --gap 11

Kräver en Google-nyckel med "Map Tiles API" aktiverat, antingen
  - som nätverkshemlighet i molnmiljön (rubriken X-Goog-Api-Key för tile.googleapis.com), eller
  - i miljövariabeln GOOGLE_MAPS_API_KEY.

OBS: Googles villkor tillåter inte att Street View-bilder sparas eller görs om till nya verk som video.
Använd bara för privata tester.

Steg: rutt (OSRM) -> punkter var --gap meter -> närmaste panorama per punkt (panoIds) -> panoramats riktning
(metadata) -> bitarna (tiles) sätts ihop till en 360°-bild -> vyn rakt fram i färdriktningen sparas som JPG.
"""
import argparse, io, json, math, os, sys, time, urllib.parse, urllib.request, urllib.error
import concurrent.futures as cf
import numpy as np
from PIL import Image

TILES = 'https://tile.googleapis.com/v1'
KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')
stats = {'tiles': 0, 'calls': 0}


def http(url, body=None, timeout=60):
    h = {'User-Agent': 'gatuvy-pipeline'}
    if KEY and 'googleapis.com' in url: h['X-Goog-Api-Key'] = KEY
    if body is not None: h['Content-Type'] = 'application/json'; body = json.dumps(body).encode()
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8', 'replace')[:400]
            if e.code in (429, 500, 502, 503) and attempt < 5: time.sleep(2 * attempt); continue
            raise RuntimeError(f'{url.split("?")[0]} svarade {e.code}: {msg}')
        except (urllib.error.URLError, TimeoutError):
            if attempt < 5: time.sleep(2 * attempt); continue
            raise


def geocode(q):
    u = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + urllib.parse.quote(q)
    j = json.loads(http(u)); time.sleep(1.1)
    if not j: raise RuntimeError(f'Hittade inte "{q}"')
    return float(j[0]['lon']), float(j[0]['lat'])


def route(a, b):
    for base in ('https://router.project-osrm.org', 'https://routing.openstreetmap.de/routed-car'):
        try:
            j = json.loads(http(f'{base}/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}?overview=full&geometries=geojson'))
            if j.get('code') == 'Ok': return j['routes'][0]['geometry']['coordinates']
        except Exception as e: print('rutt:', e)
    raise RuntimeError('Kunde inte räkna ut en rutt')


def dist(a, b):
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2)); y = math.radians(b[1] - a[1])
    return math.hypot(x, y) * 6371000


def bearing(a, b):
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2)); y = math.radians(b[1] - a[1])
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def resample(coords, step):
    out, carry = [], 0.0
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]; L = dist(a, b)
        if L < 0.01: continue
        s = carry
        while s < L:
            t = s / L; out.append(([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], bearing(a, b))); s += step
        carry = s - L
    # jämna ut riktningen lite, så att kameran inte vrider sig i hack i kurvor
    sm = []
    for i in range(len(out)):
        sx = sum(math.sin(math.radians(out[j][1])) for j in range(max(0, i - 2), min(len(out), i + 3)))
        sy = sum(math.cos(math.radians(out[j][1])) for j in range(max(0, i - 2), min(len(out), i + 3)))
        sm.append((out[i][0], (math.degrees(math.atan2(sx, sy)) + 360) % 360))
    return sm


def panorama(session, pano, meta, zoom, look=None, half_fov=46.0):
    """Sätter ihop bitarna till en 360°-bild (ekvirektangulär). Med 'look' hämtas bara bitarna som syns
    när man tittar åt det hållet (resten lämnas svarta), vilket sparar ungefär två tredjedelar av anropen."""
    tw, th = meta.get('tileWidth', 512), meta.get('tileHeight', 512)
    # full upplösning motsvarar zoomnivå zmax; varje nivå lägre halverar bredden
    zmax = max(0, math.ceil(math.log2(meta['imageWidth'] / tw)))
    zoom = min(zoom, zmax)
    w = max(tw, round(meta['imageWidth'] / 2 ** (zmax - zoom))); h = w // 2
    nx, ny = math.ceil(w / tw), math.ceil(h / th)
    img = Image.new('RGB', (nx * tw, ny * th))
    def tile(xy):
        x, y = xy
        u = f'{TILES}/streetview/tiles/{zoom}/{x}/{y}?session={session}&panoId={urllib.parse.quote(pano)}'
        return x, y, Image.open(io.BytesIO(http(u))).convert('RGB')
    want = [(x, y) for y in range(ny) for x in range(nx)]
    if look is not None:
        rel = (look - meta.get('heading', 0.0) + 180) % 360 - 180          # vyns mitt, i grader från panoramats mitt
        cols = set()
        for deg_ in np.arange(rel - half_fov - 8, rel + half_fov + 8, 2.0):
            cols.add(int(((deg_ / 360 + 0.5) % 1.0) * w) // tw)
        top, bottom = 0.5 - 40 / 180, 0.5 + 45 / 180                       # från 40° upp till 45° ned
        rows = range(int(top * h) // th, min(ny, int(bottom * h) // th + 1))
        want = [(x, y) for y in rows for x in sorted(c for c in cols if c < nx)]
    with cf.ThreadPoolExecutor(8) as ex:
        for x, y, t in ex.map(tile, want):
            img.paste(t, (x * tw, y * th)); stats['tiles'] += 1
    return img.crop((0, 0, w, h))


def view(pano_img, pano_heading, look, W, H, fov=92.0, horizon=0.08, pitch=0.0):
    """Perspektivvy ur en 360°-bild: titta mot kompassriktningen 'look' med horisontell bildvinkel 'fov'.
    Samma geometri som Gatuvys spelare (horisonten lite ovanför mitten)."""
    src = np.asarray(pano_img, dtype=np.float32); ph, pw = src.shape[:2]
    f = math.tan(math.radians(fov / 2))
    xs = (np.arange(W) + 0.5) / W * 2 - 1
    ys = 1 - (np.arange(H) + 0.5) / H * 2 - horizon
    X, Y = np.meshgrid(xs * f, ys * f / (W / H))
    d = np.stack([X, Y + math.radians(pitch), np.ones_like(X)], -1); d /= np.linalg.norm(d, axis=-1, keepdims=True)
    lon = np.arctan2(d[..., 0], d[..., 2]) + math.radians(look - pano_heading)
    lat = np.arcsin(np.clip(d[..., 1], -1, 1))
    u = ((lon / (2 * math.pi) + 0.5) % 1.0) * pw - 0.5
    v = (0.5 - lat / math.pi) * ph - 0.5
    u0 = np.floor(u).astype(int); v0 = np.clip(np.floor(v).astype(int), 0, ph - 2); fu = (u - u0)[..., None]; fv = np.clip(v - v0, 0, 1)[..., None]
    u1 = (u0 + 1) % pw; u0 %= pw
    out = (src[v0, u0] * (1 - fu) + src[v0, u1] * fu) * (1 - fv) + (src[v0 + 1, u0] * (1 - fu) + src[v0 + 1, u1] * fu) * fv
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def densify(work, out, max_gap, W, H, zoom):
    """Fyller luckor i en befintlig uppsättning nyckelbilder: från panoramat före en lucka följs Googles egna
    länkar (pilarna i Street View) mot panoramat efter luckan, och panoramorna däremellan tas med."""
    meta = json.load(open(os.path.join(work, 'frames.json'))); fr = meta['frames']
    os.makedirs(out, exist_ok=True)
    sess = json.loads(http(f'{TILES}/createSession', {'mapType': 'streetview', 'language': 'sv-SE', 'region': 'SE'}))['session']
    cache = {}
    def md(pid):
        if pid not in cache:
            stats['calls'] += 1; cache[pid] = json.loads(http(f'{TILES}/streetview/metadata?session={sess}&panoId={urllib.parse.quote(pid)}'))
        return cache[pid]
    pos = lambda m: [m['lng'], m['lat']]
    merged, added = [], 0
    for i, f in enumerate(fr):
        merged.append(dict(f, _src=os.path.join(work, f['file'])))
        if i + 1 >= len(fr) or dist((f['lon'], f['lat']), (fr[i + 1]['lon'], fr[i + 1]['lat'])) <= max_gap: continue
        target = (fr[i + 1]['lon'], fr[i + 1]['lat']); cur = md(f['panoId']); last_kept = (f['lon'], f['lat']); seen = {f['panoId']}
        for _ in range(12):
            here = pos(cur); want = bearing(here, target)
            links = [l for l in cur.get('links', []) if l['panoId'] not in seen]
            if not links: break
            l = min(links, key=lambda l: abs((l['heading'] - want + 180) % 360 - 180))
            if abs((l['heading'] - want + 180) % 360 - 180) > 60: break
            nxt = md(l['panoId']); seen.add(l['panoId'])
            if (nxt.get('copyright') or '').split(':')[-1].strip() != 'Google': break
            if dist(pos(nxt), target) >= dist(here, target) - 1: break        # kommer inte närmare
            if dist(pos(nxt), target) < 4: break                               # framme vid nästa bild
            cur = nxt
            if dist(pos(nxt), last_kept) >= 6:
                merged.append({'panoId': nxt['panoId'], 'lon': nxt['lng'], 'lat': nxt['lat'], 'pano': True, 'bearing': bearing(pos(nxt), target),
                               'credit': 'Photo by: Google · Google Street View', 'taken': nxt.get('date', ''), 'seq': nxt['panoId'][:6], '_meta': nxt})
                last_kept = pos(nxt); added += 1
            if dist(pos(nxt), target) <= max_gap * 0.6: break
    # spara i ordning, nya bilder renderas
    out_frames, prev = [], None
    for n, f in enumerate(merged):
        name = f'key_{n:04d}.jpg'; dst = os.path.join(out, name)
        if '_meta' in f:
            if not os.path.exists(dst): view(panorama(sess, f['panoId'], f['_meta'], zoom, look=f['bearing']), f['_meta'].get('heading', 0.0), f['bearing'], W, H).save(dst, quality=93)
        elif not os.path.exists(dst):
            import shutil; shutil.copy(f['_src'], dst)
        p = (f['lon'], f['lat']); g = {k: v for k, v in f.items() if not k.startswith('_')}
        g.update(file=name, gap=dist(prev, p) if prev else 0); out_frames.append(g); prev = p
    meta['frames'] = out_frames; json.dump(meta, open(os.path.join(out, 'frames.json'), 'w'), indent=1)
    print(f'{added} panoramor tillagda i luckor, {len(out_frames)} nyckelbilder totalt. Google-anrop: {stats["calls"]} metadata, {stats["tiles"]} bitar.')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='frm'); ap.add_argument('--to')
    ap.add_argument('--out', required=True); ap.add_argument('--gap', type=float, default=11)
    ap.add_argument('--size', default='1280x720'); ap.add_argument('--zoom', type=int, default=3, help='bitarnas detaljnivå (3 ≈ 4096 px bred 360°-bild)')
    ap.add_argument('--radius', type=int, default=25, help='största avstånd (m) från rutten till ett panorama')
    ap.add_argument('--limit', type=int, default=0, help='bara de första N bilderna (för test)')
    ap.add_argument('--all-owners', action='store_true', help='ta med panoramor som privatpersoner laddat upp (normalt bara Googles egna bilar)')
    ap.add_argument('--densify', default='', help='fyll luckor i en befintlig mapp (från detta skript) i stället för att börja om')
    ap.add_argument('--max-gap', type=float, default=14, help='--densify: luckor större än så här (m) fylls')
    a = ap.parse_args()
    W, H = map(int, a.size.split('x'))
    if a.densify: return densify(a.densify, a.out, a.max_gap, W, H, a.zoom)
    os.makedirs(a.out, exist_ok=True)
    A, B = geocode(a.frm), geocode(a.to)
    coords = route(A, B); pts = resample(coords, a.gap)
    if a.limit: pts = pts[:a.limit]
    total = sum(dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
    print(f'Rutt {total / 1000:.1f} km, {len(pts)} punkter var {a.gap:.0f}:e meter')

    sess = json.loads(http(f'{TILES}/createSession', {'mapType': 'streetview', 'language': 'sv-SE', 'region': 'SE'}))['session']
    # närmaste panorama per punkt (högst 100 punkter per anrop)
    ids = []
    for i in range(0, len(pts), 100):
        part = pts[i:i + 100]
        j = json.loads(http(f'{TILES}/streetview/panoIds?session={sess}', {'locations': [{'lat': p[0][1], 'lng': p[0][0]} for p in part], 'radius': a.radius}))
        ids += j.get('panoIds', [''] * len(part)); stats['calls'] += 1
    keep, last = [], None
    for p, pid in zip(pts, ids):
        if pid and pid != last: keep.append((p, pid)); last = pid
    print(f'{len(keep)} unika panoramor ({sum(1 for x in ids if not x)} punkter utan panorama)')

    frames, prev_pos = [], None
    meta_cache = {}
    def fetch_meta(pid):
        stats['calls'] += 1
        return json.loads(http(f'{TILES}/streetview/metadata?session={sess}&panoId={urllib.parse.quote(pid)}'))
    with cf.ThreadPoolExecutor(6) as ex:
        for pid, m in zip([k[1] for k in keep], ex.map(fetch_meta, [k[1] for k in keep])): meta_cache[pid] = m
    skipped = 0
    for n, ((pos, brg), pid) in enumerate(keep):
        m = meta_cache[pid]
        owner = (m.get('copyright') or '').split(':')[-1].strip()
        mpos = [m.get('lng', pos[0]), m.get('lat', pos[1])]
        # bara Googles egna bilar (privatpersoners panoramor har andra datum, kameror och ibland fel position),
        # och bara panoramor som faktiskt ligger vid rutten
        if (not a.all_owners and owner != 'Google') or dist(mpos, pos) > a.radius + 10:
            skipped += 1; continue
        name = f'key_{len(frames):04d}.jpg'
        if not os.path.exists(os.path.join(a.out, name)):
            pano = panorama(sess, pid, m, a.zoom, look=brg)
            view(pano, m.get('heading', 0.0), brg, W, H).save(os.path.join(a.out, name), quality=93)
        ppos = [m.get('lng', pos[0]), m.get('lat', pos[1])]
        frames.append({'file': name, 'lon': ppos[0], 'lat': ppos[1], 'bearing': brg, 'pano': True, 'panoId': pid,
                       'credit': (m.get('copyright') or '© Google').replace('From the Owner, ', '').strip() + ' · Google Street View',
                       'taken': m.get('date', ''), 'seq': pid[:6], 'gap': dist(prev_pos, ppos) if prev_pos else 0})
        prev_pos = ppos
        if (n + 1) % 25 == 0: print(f'  {n + 1}/{len(keep)} bilder, {stats["tiles"]} bitar', flush=True)
    if skipped: print(f'{skipped} panoramor hoppades över (inte Googles egna eller för långt från rutten)')
    json.dump({'from': a.frm, 'to': a.to, 'size': [W, H], 'source': 'google', 'frames': frames}, open(os.path.join(a.out, 'frames.json'), 'w'), indent=1)
    print(f'{len(frames)} nyckelbilder sparade i {a.out}. Google-anrop: {stats["calls"]} metadata/sök, {stats["tiles"]} bitar.')


if __name__ == '__main__':
    main()
