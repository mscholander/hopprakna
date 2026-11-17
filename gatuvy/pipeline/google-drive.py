#!/usr/bin/env python3
"""Körplan av Street View-bilder där AI bara gör raka sträckor: svängar och byten mellan fotokörningar görs utan AI.

    python3 google-drive.py --from "Hökgatan 3, Malmö" --to "Bispgatan 55, Malmö" --out work/malmo --max-length 620
    python3 make-film.py work/malmo --engine prunaai/p-video --resolution 720p --draft --speed 4.1

Så fungerar planen:
  - Panoramor väljs som i google-strict.py: Googles egna bilar, nära vägen, strikt i ordning längs vägen.
    Hela sträckan planeras på en gång så att så få byten mellan fotokörningar som möjligt behövs. Bilar som körde
    åt motsatt håll används bara där inget annat finns (på lugna gator syns det knappt).
  - Mellan två bilder tittar kameran i den riktning man faktiskt förflyttar sig. AI-modellen får alltså bara
    start- och slutbilder som motsvarar körning rakt fram.
  - Sväng: när riktningen ändras 15° eller mer vid en bild stannar AI-klippet där, och kameran vrids i stället
    mjukt inuti 360°-bilden (exakt, inget påhittat).
  - Byte av fotokörning: en kort övertoning mellan två bilder på samma plats, i stället för ett AI-klipp.

OBS: Googles villkor tillåter inte video av Street View-bilder. Endast privata tester.
"""
import argparse, collections, json, math, os, shutil, subprocess, sys, urllib.parse
import concurrent.futures as cf
import importlib.util

_here = os.path.dirname(os.path.abspath(__file__))
def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_here, file)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
gf = _load('gf', 'google-frames.py'); gs = _load('gs', 'google-strict.py')
TILES, http, dist, bearing, Route = gf.TILES, gf.http, gf.dist, gf.bearing, gs.Route
ang = lambda a, b: abs((a - b + 180) % 360 - 180)
signed = lambda a, b: (a - b + 180) % 360 - 180          # a − b, i intervallet −180..180


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='frm', required=True); ap.add_argument('--to', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--max-length', type=float, default=0); ap.add_argument('--spacing', type=float, default=10)
    ap.add_argument('--side', type=float, default=4.5); ap.add_argument('--turn', type=float, default=15, help='riktningsändring (grader) som räknas som sväng')
    ap.add_argument('--turn-speed', type=float, default=40, help='grader per sekund i en sväng')
    ap.add_argument('--size', default='1280x720'); ap.add_argument('--zoom', type=int, default=3); ap.add_argument('--fps', type=int, default=30)
    a = ap.parse_args()
    W, H = map(int, a.size.split('x')); os.makedirs(a.out, exist_ok=True)

    A, B = gf.geocode(a.frm), gf.geocode(a.to)
    R = Route(gf.route(A, B), a.max_length)
    print(f'Rutt {R.cum[-1]:.0f} m, används {R.length:.0f} m')
    sess = json.loads(http(f'{TILES}/createSession', {'mapType': 'streetview', 'language': 'sv-SE', 'region': 'SE'}))['session']

    # 1. panoramor nära rutten (sökning + två led av Googles egna länkar)
    probes = [R.point(i * 4.0) for i in range(int(R.length / 4) + 1)]
    ids = set()
    for i in range(0, len(probes), 100):
        j = json.loads(http(f'{TILES}/streetview/panoIds?session={sess}', {'locations': [{'lat': p[1], 'lng': p[0]} for p in probes[i:i + 100]], 'radius': int(a.side + 5)}))
        ids |= {x for x in j.get('panoIds', []) if x}
    def md(pid): return json.loads(http(f'{TILES}/streetview/metadata?session={sess}&panoId={urllib.parse.quote(pid)}'))
    metas = {}
    todo = set(ids)
    for _ in range(3):
        with cf.ThreadPoolExecutor(8) as ex:
            for m in ex.map(md, sorted(todo)): metas[m['panoId']] = m
        todo = {l['panoId'] for m in metas.values() for l in m.get('links', [])} - set(metas)
        if not todo: break
    print(f'{len(metas)} panoramor hittade')

    # 2. kandidater: Googles egna, nära vägen. Varje bil-passage (månad + körriktning) är en "körning".
    cand, why = [], collections.Counter()
    for m in metas.values():
        if (m.get('copyright') or '').split(':')[-1].strip() != 'Google': why['inte Googles egen bil'] += 1; continue
        s, side = R.project(m['lng'], m['lat'])
        if side > a.side: why['långt från vägen'] += 1; continue
        if s <= 0 or s >= R.length: why['utanför sträckan'] += 1; continue
        rev = ang(m.get('heading', R.heading(s, 6)), R.heading(s, 6)) > 90
        cand.append({'m': m, 's': s, 'side': side, 'rev': rev, 'run': f"{m.get('date', '?')}{' motsatt' if rev else ''}"})
    print(f'{len(cand)} kandidater; bortsorterade: {dict(why)}')
    runs = sorted({c['run'] for c in cand})
    for r in runs:
        ss = sorted(c['s'] for c in cand if c['run'] == r); print(f'  körning {r:16s} {len(ss):3d} bilder, {ss[0]:5.0f}–{ss[-1]:.0f} m')
    newest = max(int(c['m'].get('date', '0')[:4] or 0) for c in cand)

    # 3. planera hela sträckan: så få körningsbyten och luckor som möjligt, helst bilar i vår riktning
    N = int(R.length / a.spacing) + 1
    def best(run, k):
        ok = [c for c in cand if c['run'] == run and abs(c['s'] - k * a.spacing) <= a.spacing * 0.5]
        return min(ok, key=lambda c: abs(c['s'] - k * a.spacing) + 0.3 * c['side']) if ok else None
    pick = {(r, k): best(r, k) for r in runs for k in range(N)}
    states = runs + ['lucka']; INF = 1e9; SWITCH, HOLE, REV = 4.0, 3.0, 0.7
    def emit(r, k):
        if r == 'lucka': return HOLE
        c = pick[(r, k)]
        if c is None: return INF
        age = newest - int(c['m'].get('date', '0')[:4] or newest)          # nyare bilder föredras något
        return (REV if c['rev'] else 0.0) + 0.04 * age
    cost = [{r: emit(r, 0) for r in states}]; back = [{}]
    for k in range(1, N):
        cost.append({}); back.append({})
        for r in states:
            p = min(states, key=lambda q: cost[k - 1][q] + (0 if q == r or 'lucka' in (q, r) else SWITCH))
            cost[k][r] = cost[k - 1][p] + (0 if p == r or 'lucka' in (p, r) else SWITCH) + emit(r, k); back[k][r] = p
    r = min(states, key=lambda q: cost[N - 1][q]); path = [r]
    for k in range(N - 1, 0, -1): r = back[k][r]; path.append(r)
    path.reverse()
    chosen = []
    for k, r in enumerate(path):
        c = pick.get((r, k))
        if c and (not chosen or c['s'] > chosen[-1]['s'] + a.spacing * 0.45) and all(c['m']['panoId'] != x['m']['panoId'] for x in chosen):
            chosen.append(dict(c, link='ai'))

    # 4. byte av körning: hitta det ställe där den gamla och den nya körningen har var sin bild så nära varandra
    #    som möjligt (oftast mitt i en korsning), och tona över där. Då hoppar bilden så lite som möjligt.
    P = lambda c: (c['m']['lng'], c['m']['lat'])
    out = []
    for i, c in enumerate(chosen):
        if not out or c['run'] == out[-1]['run']: out.append(c); continue
        prev = out[-1]; lo = out[-2]['s'] + 3 if len(out) > 1 else -1
        olds = [x for x in cand if x['run'] == prev['run'] and lo < x['s'] <= c['s']]
        news = [y for y in cand if y['run'] == c['run'] and lo < y['s'] <= c['s']]
        pairs = [(dist(P(x), P(y)), x, y) for x in olds for y in news if y['s'] >= x['s'] - 4]
        if pairs:
            dmin, x, y = min(pairs, key=lambda t: t[0])
            if x['m']['panoId'] != prev['m']['panoId']: out[-1] = dict(x, link=prev['link'])
            out.append(dict(y, link='dissolve', jump=round(dmin, 1)))
            if c['s'] > y['s'] + a.spacing * 0.45: out.append(c)
        else: out.append(dict(c, link='dissolve', jump=round(dist(P(prev), P(c)), 1)))
    K = out; n = len(K)
    pos = [(c['m']['lng'], c['m']['lat']) for c in K]

    # 5. riktningar: mellan två bilder tittar kameran dit man förflyttar sig. 'a' = riktning när man kommer fram
    #    till bilden, 'd' = riktning när man lämnar den. Skiljer de sig mycket är det en sväng (vridning i bilden).
    def travel(i):   # riktning från bild i till i+1, om det är en körd sträcka
        if i < 0 or i + 1 >= n or K[i + 1]['link'] != 'ai': return None
        return bearing(pos[i], pos[i + 1]) if dist(pos[i], pos[i + 1]) >= 4 else R.heading((K[i]['s'] + K[i + 1]['s']) / 2, 4)
    for i, c in enumerate(K):
        prev_t, next_t = travel(i - 1), travel(i)
        if c['link'] == 'dissolve' and i: c['a'] = K[i - 1]['d']                     # samma blick på båda sidor om övertoningen
        else: c['a'] = prev_t if prev_t is not None else (next_t if next_t is not None else R.heading(c['s'], 6))
        c['d'] = next_t if next_t is not None else c['a']
        if ang(c['a'], c['d']) < a.turn:
            if c['link'] == 'dissolve' and i: c['d'] = c['a']                         # liten skillnad: AI-klippet tar den
            else: c['a'] = c['d']
    # 6. rendera bilder och svängar
    frames = []
    for i, c in enumerate(K):
        m = c['m']; name = f'key_{i:04d}.jpg'; turn = ang(c['a'], c['d']) >= a.turn
        pano = gf.panorama(sess, m['panoId'], m, a.zoom, look=None if turn else c['a'])
        ph = m.get('heading', 0.0)
        gf.view(pano, ph, c['a'], W, H).save(os.path.join(a.out, name), quality=93)
        f = {'file': name, 'lon': m['lng'], 'lat': m['lat'], 'along': round(c['s'], 1), 'side': round(c['side'], 1), 'bearing': round(c['a'], 1),
             'bearing_out': round(c['d'], 1), 'carHeading': m.get('heading'), 'reverse': c['rev'], 'pano': True, 'panoId': m['panoId'],
             'taken': m.get('date', ''), 'seq': c['run'], 'link': c['link'] if i else 'start', 'credit': 'Photo by: Google · Google Street View',
             'gap': round(c['s'] - K[i - 1]['s'], 1) if i else 0}
        if 'jump' in c: f['jump'] = c['jump']
        if turn:
            delta = signed(c['d'], c['a']); nfr = max(int(a.fps * 0.8), int(abs(delta) / a.turn_speed * a.fps))
            tdir = os.path.join(a.out, f'turn_{i:04d}'); shutil.rmtree(tdir, ignore_errors=True); os.makedirs(tdir)
            for k in range(nfr + 1):
                t = k / nfr; e = t * t * (3 - 2 * t)                                   # mjuk start och mjukt stopp
                gf.view(pano, ph, c['a'] + delta * e, W, H).save(os.path.join(tdir, f'{k:04d}.jpg'), quality=92)
            shutil.copy(os.path.join(tdir, f'{nfr:04d}.jpg'), os.path.join(a.out, f'key_{i:04d}_out.jpg'))
            clip = os.path.join(a.out, f'turn_{i:04d}.mp4')
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', str(a.fps), '-i', os.path.join(tdir, '%04d.jpg'),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', clip], check=True)
            shutil.rmtree(tdir)
            f.update(file_out=f'key_{i:04d}_out.jpg', pre=f'turn_{i:04d}.mp4', turn=round(delta, 1))
        frames.append(f)
    json.dump({'from': a.frm, 'to': a.to, 'size': [W, H], 'source': 'google-drive', 'frames': frames}, open(os.path.join(a.out, 'frames.json'), 'w'), indent=1)

    print('\n nr  längs  steg  sida  blick in→ut   länk       körning')
    for f in frames:
        print(f" {f['file'][4:8]} {f['along']:6.1f} {f['gap']:5.1f} {f['side']:5.1f}  {f['bearing']:5.0f}→{f['bearing_out']:<5.0f} {f['link']:9s}  {f['seq']}" + (f"   SVÄNG {f['turn']:+.0f}°" if 'turn' in f else '') + (f"   hopp {f['jump']} m" if 'jump' in f else ''))
    sw = sum(1 for f in frames if f['link'] == 'dissolve'); tn = sum(1 for f in frames if 'turn' in f)
    print(f'\n{len(frames)} bilder, {tn} svängar (vrids i bilden), {sw} körningsbyten (övertoning), {sum(1 for f in frames if f["reverse"])} bilder från bil i motsatt riktning')
    print(f'Google: {len(metas)} metadata, {gf.stats["tiles"]} bitar')


if __name__ == '__main__':
    main()
