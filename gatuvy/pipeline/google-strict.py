#!/usr/bin/env python3
"""Strikt urval av Street View-bilder längs en rutt, för filmtest där ordning och riktning måste stämma.

    python3 google-strict.py --from "Hökgatan 12, Malmö" --to "Bispgatan 55, Malmö" --out work/malmo --max-length 500

Skillnader mot google-frames.py:
  - varje panorama placeras på rutten (hur långt längs vägen, hur långt åt sidan) och bilderna sorteras strikt
    efter avståndet längs vägen, så att filmen aldrig går bakåt eller visar samma sträcka två gånger;
  - bara Googles egna bilar, och bara panoramor där bilen körde åt samma håll som vi (annars "backar" trafiken);
  - helst en enda körning (samma månad), med andra körningar bara som utfyllnad i luckor;
  - jämnt avstånd mellan bilderna (--spacing), och vyn riktas längs vägen.
Ett kontrollblad (kontroll.png) och en tabell skrivs ut, så att urvalet kan granskas innan något genereras.

OBS: Googles villkor tillåter inte video av Street View-bilder. Endast privata tester.
Kräver nyckeln som rubriken X-Goog-Api-Key för tile.googleapis.com (nätverkshemlighet) eller GOOGLE_MAPS_API_KEY.
"""
import argparse, collections, json, math, os, sys, urllib.parse
import concurrent.futures as cf
import importlib.util
from PIL import Image, ImageDraw

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('gf', os.path.join(_here, 'google-frames.py'))
gf = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(gf)
TILES, http, dist, bearing = gf.TILES, gf.http, gf.dist, gf.bearing
ang = lambda a, b: abs((a - b + 180) % 360 - 180)


class Route:
    """Rutten som en linje i meter, med 'hur långt längs vägen' och 'hur långt åt sidan' för en punkt."""
    def __init__(self, coords, max_len=0):
        self.lat0 = coords[0][1]; k = math.cos(math.radians(self.lat0))
        self.xy = [((c[0] - coords[0][0]) * 111320 * k, (c[1] - coords[0][1]) * 111320) for c in coords]
        self.c0 = coords[0]; self.k = k
        self.cum = [0.0]
        for a, b in zip(self.xy, self.xy[1:]): self.cum.append(self.cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.length = min(self.cum[-1], max_len) if max_len else self.cum[-1]

    def to_xy(self, lon, lat): return ((lon - self.c0[0]) * 111320 * self.k, (lat - self.c0[1]) * 111320)
    def to_lonlat(self, x, y): return (self.c0[0] + x / (111320 * self.k), self.c0[1] + y / 111320)

    def project(self, lon, lat):
        p = self.to_xy(lon, lat); best = None
        for i, (a, b) in enumerate(zip(self.xy, self.xy[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
            t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
            q = (a[0] + t * dx, a[1] + t * dy); d = math.hypot(p[0] - q[0], p[1] - q[1])
            if best is None or d < best[1]: best = (self.cum[i] + t * math.sqrt(L2), d)
        return best  # (längs vägen, åt sidan)

    def point(self, s):
        for i in range(len(self.cum) - 1):
            if self.cum[i + 1] >= s:
                t = (s - self.cum[i]) / max(1e-9, self.cum[i + 1] - self.cum[i]); a, b = self.xy[i], self.xy[i + 1]
                return self.to_lonlat(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        return self.to_lonlat(*self.xy[-1])

    def heading(self, s, smooth=12):
        """Vägens riktning vid s, utjämnad över ±smooth meter så att kameran svänger mjukt i kurvor."""
        a, b = self.point(max(0, s - smooth)), self.point(min(self.cum[-1], s + smooth))
        return bearing(a, b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='frm', required=True); ap.add_argument('--to', required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--max-length', type=float, default=0, help='bara de första N metrarna av rutten')
    ap.add_argument('--spacing', type=float, default=8, help='önskat avstånd längs vägen mellan bilderna (m)')
    ap.add_argument('--probe', type=float, default=4, help='hur tätt rutten genomsöks efter panoramor (m)')
    ap.add_argument('--side', type=float, default=10, help='största avstånd åt sidan från rutten (m)')
    ap.add_argument('--max-turn', type=float, default=50, help='största skillnad mellan bilens och vägens riktning (grader)')
    ap.add_argument('--size', default='1280x720'); ap.add_argument('--zoom', type=int, default=3)
    a = ap.parse_args()
    W, H = map(int, a.size.split('x')); os.makedirs(a.out, exist_ok=True)

    A, B = gf.geocode(a.frm), gf.geocode(a.to)
    R = Route(gf.route(A, B), a.max_length)
    print(f'Rutt {R.cum[-1]:.0f} m, används {R.length:.0f} m')
    sess = json.loads(http(f'{TILES}/createSession', {'mapType': 'streetview', 'language': 'sv-SE', 'region': 'SE'}))['session']

    # 1. alla panoramor nära rutten
    probes = [R.point(s) for s in [i * a.probe for i in range(int(R.length / a.probe) + 1)]]
    ids = set()
    for i in range(0, len(probes), 100):
        part = probes[i:i + 100]
        j = json.loads(http(f'{TILES}/streetview/panoIds?session={sess}', {'locations': [{'lat': p[1], 'lng': p[0]} for p in part], 'radius': int(a.side + 5)}))
        ids |= {x for x in j.get('panoIds', []) if x}
    def md(pid): return json.loads(http(f'{TILES}/streetview/metadata?session={sess}&panoId={urllib.parse.quote(pid)}'))
    with cf.ThreadPoolExecutor(8) as ex: metas = list(ex.map(md, sorted(ids)))
    # Googles länkar ger fler panoramor från samma körningar (grannar som sökningen missade)
    more = {l['panoId'] for m in metas for l in m.get('links', [])} - ids
    with cf.ThreadPoolExecutor(8) as ex: metas += list(ex.map(md, sorted(more)))
    print(f'{len(metas)} panoramor hittade')

    # 2. placera på rutten och sålla
    cand, why = [], collections.Counter()
    for m in metas:
        owner = (m.get('copyright') or '').split(':')[-1].strip()
        s, side = R.project(m['lng'], m['lat'])
        road = R.heading(s)
        if owner != 'Google': why['inte Googles egen bil'] += 1; continue
        if side > a.side: why[f'mer än {a.side:.0f} m från vägen'] += 1; continue
        if s < 0 or s > R.length: why['utanför sträckan'] += 1; continue
        if ang(m.get('heading', road), road) > a.max_turn: why['bilen körde åt andra hållet'] += 1; continue
        cand.append({'m': m, 's': s, 'side': side, 'road': road, 'date': m.get('date', '')})
    print(f'{len(cand)} godkända; bortsorterade:', dict(why))

    # 3. välj körning: den månad som täcker mest av sträckan; andra bara där den har luckor
    def coverage(c):
        ss = sorted(x['s'] for x in c); cov = 0
        for x, y in zip(ss, ss[1:]): cov += min(y - x, a.spacing * 2)
        return cov
    by = collections.defaultdict(list)
    for c in cand: by[c['date']].append(c)
    ranked = sorted(by, key=lambda d: -coverage(by[d]))
    main_date = ranked[0] if ranked else None
    print('körningar (månad: täckning m):', ', '.join(f'{d}: {coverage(by[d]):.0f}' for d in ranked[:5]))

    # 4. jämnt avstånd, strikt framåt. Huvudkörningen först; luckor fylls med närmaste andra körning.
    chosen, s_last = [], -1e9
    pool_main = sorted(by.get(main_date, []), key=lambda x: x['s'])
    pool_other = sorted([c for c in cand if c['date'] != main_date], key=lambda x: x['s'])
    target = 0.0
    while target <= R.length + 1e-6:
        def pick(pool):
            ok = [c for c in pool if c['s'] > s_last + a.spacing * 0.6 and abs(c['s'] - target) <= a.spacing * 0.6]
            return min(ok, key=lambda c: abs(c['s'] - target) + c['side'] * 0.3) if ok else None
        c = pick(pool_main) or pick(pool_other)
        if c: chosen.append(c); s_last = c['s']
        target += a.spacing
    print(f'{len(chosen)} bilder valda, var ~{a.spacing:.0f}:e meter')

    # 5. rendera vyn längs vägen och skriv kontrolltabell
    frames, prev = [], None
    for n, c in enumerate(chosen):
        m = c['m']; look = R.heading(c['s']); name = f'key_{n:04d}.jpg'
        if not os.path.exists(os.path.join(a.out, name)):
            gf.view(gf.panorama(sess, m['panoId'], m, a.zoom, look=look), m.get('heading', 0.0), look, W, H).save(os.path.join(a.out, name), quality=93)
        gap = c['s'] - prev['s'] if prev else 0
        frames.append({'file': name, 'lon': m['lng'], 'lat': m['lat'], 'along': round(c['s'], 1), 'side': round(c['side'], 1),
                       'bearing': look, 'carHeading': m.get('heading'), 'pano': True, 'panoId': m['panoId'], 'taken': c['date'],
                       'seq': c['date'], 'credit': 'Photo by: Google · Google Street View', 'gap': gap})
        prev = c
    json.dump({'from': a.frm, 'to': a.to, 'size': [W, H], 'source': 'google-strict', 'frames': frames}, open(os.path.join(a.out, 'frames.json'), 'w'), indent=1)

    print('\n nr  längs   steg  sida  väg°  bil°  diff  månad')
    for f in frames:
        print(f" {f['file'][4:8]} {f['along']:6.1f} {f['gap']:6.1f} {f['side']:5.1f} {f['bearing']:5.0f} {f['carHeading']:5.0f} {ang(f['bearing'], f['carHeading']):5.0f}  {f['taken']}")
    gaps = [f['gap'] for f in frames[1:]]
    back = sum(1 for g in gaps if g <= 0)
    print(f'\nKontroll: steg {min(gaps):.1f}–{max(gaps):.1f} m, bakåtsteg {back}, körningsbyten {sum(1 for x, y in zip(frames, frames[1:]) if x["taken"] != y["taken"])}')

    # kontrollblad: alla bilder i ordning, med nummer, steg och månad
    cols = 6; tw, th = 320, 180; rows = math.ceil(len(frames) / cols)
    sheet = Image.new('RGB', (cols * tw, rows * th)); d = ImageDraw.Draw(sheet)
    for n, f in enumerate(frames):
        im = Image.open(os.path.join(a.out, f['file'])).resize((tw, th)); x, y = (n % cols) * tw, (n // cols) * th
        sheet.paste(im, (x, y)); d.rectangle((x, y, x + 200, y + 16), fill=(0, 0, 0)); d.text((x + 4, y + 2), f"{n}  {f['along']:.0f} m  {f['taken']}", fill=(255, 255, 0))
    sheet.save(os.path.join(a.out, 'kontroll.png'))
    print('Kontrollblad:', os.path.join(a.out, 'kontroll.png'), f'| Google: {gf.stats["tiles"]} bitar')


if __name__ == '__main__':
    main()
