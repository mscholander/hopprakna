#!/usr/bin/env python3
"""Bildkontroll före filmgenerering: följer bilderna sträckan, i rätt ordning, och tittar de åt samma håll?

    python3 check-frames.py work/malmo            # rapport + kontroll.png, avslutar med fel om något underkänns
    python3 check-frames.py work/malmo --fix work/malmo_ok   # skriver en rensad uppsättning utan underkända bilder

Två sorters kontroller:
  1. Uppgifter om varje bild (från frames.json): position längs vägen ska öka för varje bild, avståndet åt sidan
     från vägen ska vara litet, steget till förra bilden lagom, och bilen/kameran som tog bilden ska ha kört åt
     samma håll som vi (annars "backar" trafiken i filmen).
  2. Bildinnehållet: nästa bild ska se ut som en inzoomning av den förra (vi kör ju framåt). Mittpartiet av bild A
     förstoras i några steg och jämförs med bild B, och tvärtom. Om B inte liknar en förstoring av A, eller om
     A liknar en förstoring av B bättre (= bakåt), underkänns paret.
make-film.py kör den här kontrollen automatiskt och vägrar generera om något underkänns.
"""
import argparse, json, math, os, shutil, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ang = lambda a, b: abs((a - b + 180) % 360 - 180)
# Gränsvärden. Bildlikheten är kalibrerad på riktiga gatubilder (nov 2026): par i rätt ordning gav median 0,93
# (lägsta 0,64), samma plats men vyn vriden 45° gav median 0,24, 90° åt sidan 0,34 och bakåt −0,03.
LIMITS = dict(side=10.0, step_min=2.0, step_warn=16.0, step_fail=30.0, heading=50.0, turn=40.0,
              sim_fail=0.50, sim_warn=0.65, back_window=5, back_margin=-0.02,
              # Detaljpunkter (kräver OpenCV): rätta par delade i median 50–312 geometriskt stämmande punkter,
              # bilder från olika platser som mest 17. Expansion > 0 = punkterna glider utåt = vi åker framåt.
              kp_fail=18, kp_warn=30, kp_window=5)


def load_grey(path):
    """Liten, lätt suddad gråskalebild: jämför gatans form (himmel, hus, väg) och inte detaljer,
    som skiljer sig mycket mellan två foton tagna några meter isär."""
    im = Image.open(path).convert('L').resize((96, 54), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.5)).resize((48, 27), Image.BILINEAR)
    return np.asarray(im, np.float32)


def zoom(a, z, cy=0.46):
    """Förstora mittpartiet (runt horisonten) z gånger till full storlek."""
    h, w = a.shape; ch, cw = h / z, w / z; y0 = cy * h - ch / 2; x0 = (w - cw) / 2
    ys = np.clip(y0 + (np.arange(h) + .5) * ch / h, 0, h - 1).astype(int); xs = np.clip(x0 + (np.arange(w) + .5) * cw / w, 0, w - 1).astype(int)
    return a[np.ix_(ys, xs)]


def ncc(a, b):
    h, w = a.shape; m = slice(int(h * .1), int(h * .85)), slice(int(w * .08), int(w * .92))
    x, y = a[m] - a[m].mean(), b[m] - b[m].mean()
    return float((x * y).sum() / (np.sqrt((x * x).sum() * (y * y).sum()) + 1e-6))


def best_match(a, b):
    """Hur väl liknar b en förstoring av a (= en bild tagen längre fram)? Förstoring 1,08–1,65 och små förskjutningar."""
    return max(ncc(np.roll(np.roll(zoom(a, z), dx, 1), dy, 0), b)
               for z in (1.08, 1.17, 1.27, 1.38, 1.5, 1.65) for dx in (-3, -1, 0, 1, 3) for dy in (-1, 0, 1))


try:
    import cv2
except ImportError:            # utan OpenCV görs bara gråskalejämförelsen
    cv2 = None
_orb = cv2.ORB_create(nfeatures=2000, fastThreshold=12) if cv2 is not None else None


def keypoints(path):
    g = cv2.cvtColor(cv2.resize(cv2.imread(path), (640, 360)), cv2.COLOR_BGR2GRAY)
    g[int(360 * .86):] = 0                       # motorhuv och bilens delar längst ned räknas inte
    k, d = _orb.detectAndCompute(g, None)
    return np.float32([p.pt for p in k]), d


def match(ka, kb):
    """Antal detaljpunkter som stämmer geometriskt mellan två bilder, och hur de rör sig: > 0 = utåt från
    horisontens mitt, alltså framåt; < 0 = inåt, alltså bakåt."""
    (pa, da), (pb, db) = ka, kb
    if da is None or db is None or len(pa) < 20 or len(pb) < 20: return 0, 0.0
    m = [x for x in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2) if len(x) == 2 and x[0].distance < 0.8 * x[1].distance]
    if len(m) < 12: return 0, 0.0
    A = np.float32([pa[x[0].queryIdx] for x in m]); B = np.float32([pb[x[0].trainIdx] for x in m])
    F, mask = cv2.findFundamentalMat(A, B, cv2.FM_RANSAC, 2.0, 0.99)
    if mask is None: return 0, 0.0
    inl = mask.ravel().astype(bool); A, B = A[inl], B[inl]
    c = np.float32([320, 360 * 0.46]); ra, rb = np.linalg.norm(A - c, axis=1), np.linalg.norm(B - c, axis=1); keep = (ra > 25) & (rb > 25)
    return int(inl.sum()), (float(np.median(np.log(rb[keep] / ra[keep]))) if keep.sum() >= 5 else 0.0)


def check(work, limits=LIMITS, quiet=False):
    meta = json.load(open(os.path.join(work, 'frames.json'))); fr = meta['frames']
    res = []
    grey = [load_grey(os.path.join(work, f['file'])) for f in fr]
    kps = [keypoints(os.path.join(work, f['file'])) for f in fr] if cv2 is not None else None
    for i, f in enumerate(fr):
        r = {'i': i, 'file': f['file'], 'fail': [], 'warn': []}
        side = f.get('side'); car = f.get('carHeading'); view = f.get('bearing')
        if side is not None and side > limits['side']: r['fail'].append(f'{side:.0f} m från vägen')
        if car is not None and view is not None and ang(car, view) > limits['heading']:
            r['fail'].append(f'tagen av en bil som körde åt andra hållet ({ang(car, view):.0f}°)')
        if i:
            p = fr[i - 1]
            if 'along' in f and 'along' in p:
                step = f['along'] - p['along']
                if step <= 0: r['fail'].append(f'ligger bakom förra bilden ({step:.1f} m)')
                elif step < limits['step_min']: r['warn'].append(f'bara {step:.1f} m från förra')
                elif step > limits['step_fail']: r['fail'].append(f'{step:.0f} m från förra (lucka)')
                elif step > limits['step_warn']: r['warn'].append(f'{step:.0f} m från förra')
            elif not quiet: r['warn'].append('saknar position längs vägen')
            if view is not None and p.get('bearing') is not None and ang(view, p['bearing']) > limits['turn']:
                r['warn'].append(f'skarp sväng ({ang(view, p["bearing"]):.0f}°)')
            fwd, bwd = best_match(grey[i - 1], grey[i]), best_match(grey[i], grey[i - 1])
            r['fwd'], r['bwd'] = round(fwd, 3), round(bwd, 3)
            if fwd < limits['sim_fail']: r['fail'].append(f'liknar inte förra bilden (likhet {fwd:.2f}): tittar åt fel håll eller fel plats?')
            elif fwd < limits['sim_warn']: r['warn'].append(f'svag likhet med förra ({fwd:.2f})')
            if kps is not None:
                n, ex = match(kps[i - 1], kps[i]); r['kp'], r['exp'] = n, round(ex, 3)
                # Få gemensamma detaljer är väntat där fotokörningen byts (annat ljus, andra bilar) eller där
                # steget är långt: då är det en svag övergång, inte en felaktig bild.
                step = f['along'] - p['along'] if 'along' in f and 'along' in p else 0
                r['explained'] = (f.get('seq', f.get('taken')) != p.get('seq', p.get('taken'))) or step > 12
                if n < limits['kp_fail'] and not r['explained']: r['fail'].append(f'nästan inga gemensamma detaljer med förra bilden ({n}): annan plats?')
                elif n < limits['kp_warn']: r['warn'].append(f'svag övergång, få gemensamma detaljer ({n})' + (': ny fotokörning' if f.get('seq', f.get('taken')) != p.get('seq', p.get('taken')) else ': långt steg' if step > 12 else ''))
        res.append(r)
    # En bild som liknar både förra och nästa bild dåligt hör inte hemma i följden (fel plats eller fel håll).
    for i in range(1, len(res) - 1):
        weak = lambda r: r.get('fwd', 1) < limits['sim_warn'] or (r.get('kp', 999) < limits['kp_warn'] and not r.get('explained'))
        if weak(res[i]) and weak(res[i + 1]):
            msg = f"liknar varken förra eller nästa bild (detaljer {res[i].get('kp', '–')}/{res[i + 1].get('kp', '–')}, form {res[i]['fwd']:.2f}/{res[i + 1]['fwd']:.2f})"
            if not any(m.startswith('liknar') for m in res[i]['fail']): res[i]['fail'].append(msg)
    # Riktning: ett enskilt par är osäkert, men flera i rad som alla "ser bakåt" är ett tydligt tecken.
    W = limits['back_window']
    if kps is not None:   # detaljpunkternas rörelse är ett mycket säkrare riktningsmått än formen
        d = [(1 if r.get('exp', 0) > 0 else -1) if r.get('kp', 0) >= limits['kp_fail'] else 0 for r in res]; thr = 0
    else:
        d = [r.get('fwd', 0) - r.get('bwd', 0) for r in res]; thr = limits['back_margin']
    for i in range(1, len(res) - W + 2):
        win = d[i:i + W]
        if len(win) == W and sum(win) / W < thr:
            for r in res[i:i + W]:
                msg = 'avsnittet ser ut att gå bakåt'
                if msg not in r['fail']: r['fail'].append(msg)
    return meta, res


def report(work, meta, res):
    fr = meta['frames']
    print(' nr   längs  sida  steg  form fram/bak  detaljer  rörelse  status')
    for r in res:
        f = fr[r['i']]; st = 'UNDERKÄND: ' + '; '.join(r['fail']) if r['fail'] else ('varning: ' + '; '.join(r['warn']) if r['warn'] else 'ok')
        step = f['along'] - fr[r['i'] - 1]['along'] if r['i'] and 'along' in f and 'along' in fr[r['i'] - 1] else 0
        mv = '' if 'exp' not in r else ('framåt' if r['exp'] > 0 else 'BAKÅT')
        print(f" {r['i']:3d} {f.get('along', float('nan')):7.1f} {f.get('side', float('nan')):5.1f} {step:5.1f}  {r.get('fwd', 1):5.2f}/{r.get('bwd', 0):4.2f}   {r.get('kp', ''):>6}  {mv:7s}  {st}")
    nf = sum(1 for r in res if r['fail']); nw = sum(1 for r in res if r['warn'] and not r['fail'])
    print(f'\n{len(res)} bilder: {len(res) - nf - nw} ok, {nw} med varning, {nf} underkända')
    # kontrollblad: rött = underkänd, gult = varning
    cols, tw, th = 6, 320, 180; rows = math.ceil(len(fr) / cols)
    sheet = Image.new('RGB', (cols * tw, rows * th)); d = ImageDraw.Draw(sheet)
    for r in res:
        f = fr[r['i']]; x, y = (r['i'] % cols) * tw, (r['i'] // cols) * th
        sheet.paste(Image.open(os.path.join(work, f['file'])).resize((tw, th)), (x, y))
        col = (230, 40, 40) if r['fail'] else (240, 200, 0) if r['warn'] else (40, 200, 80)
        d.rectangle((x, y, x + tw - 1, y + th - 1), outline=col, width=4)
        d.rectangle((x, y, x + 230, y + 16), fill=(0, 0, 0))
        d.text((x + 5, y + 3), f"{r['i']}  {f.get('along', 0):.0f} m  {f.get('taken', '')}" + ('  FEL' if r['fail'] else ''), fill=col)
    sheet.save(os.path.join(work, 'kontroll.png'))
    json.dump(res, open(os.path.join(work, 'kontroll.json'), 'w'), indent=1, ensure_ascii=False)
    print('Kontrollblad:', os.path.join(work, 'kontroll.png'))
    return nf


def fix(work, out, limits=LIMITS):
    """Tar bort underkända bilder en i taget (den senare i ett underkänt par) tills allt håller."""
    meta = json.load(open(os.path.join(work, 'frames.json'))); keep = list(range(len(meta['frames'])))
    tmp = out + '.tmp'
    def failures(ks):
        shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
        sub = [meta['frames'][k] for k in ks]
        for f in sub: shutil.copy(os.path.join(work, f['file']), os.path.join(tmp, f['file']))
        json.dump(dict(meta, frames=sub), open(os.path.join(tmp, 'frames.json'), 'w'))
        _, res = check(tmp, limits, quiet=True)
        return [r['i'] for r in res if r['fail']], res
    bad, _ = failures(keep)
    while bad:
        # Ett underkänt par kan bero på vilken som helst av de två bilderna: prova att ta bort den ena eller den
        # andra. Välj det alternativ som ger minst fel, och därefter där de kvarvarande grannarna hänger ihop bäst
        # (flest gemensamma detaljer i den nya skarven) – den felaktiga bilden är den som inte passar in.
        b = bad[0]; best = None
        for cand in ([b, b - 1] if b > 0 else [b]):
            ks = keep[:cand] + keep[cand + 1:]; nb, res = failures(ks)
            joint = res[cand].get('kp', 0) + 100 * res[cand].get('fwd', 0) if cand < len(res) else 0
            key = (len(nb), -joint)
            if best is None or key < best[0]: best = (key, ks, nb)
        _, keep, bad = best
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out); out_frames = []
    for n, k in enumerate(keep):
        f = dict(meta['frames'][k]); name = f'key_{n:04d}.jpg'
        shutil.copy(os.path.join(work, f['file']), os.path.join(out, name)); f['file'] = name
        if n and 'along' in f: f['gap'] = round(f['along'] - out_frames[-1]['along'], 1)
        out_frames.append(f)
    json.dump(dict(meta, frames=out_frames), open(os.path.join(out, 'frames.json'), 'w'), indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'{len(meta["frames"]) - len(keep)} bilder borttagna, {len(keep)} kvar i {out}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('work'); ap.add_argument('--fix', default='', help='skriv en rensad uppsättning hit')
    a = ap.parse_args()
    meta, res = check(a.work)
    nf = report(a.work, meta, res)
    if a.fix:
        fix(a.work, a.fix); meta, res = check(a.fix); nf = report(a.fix, meta, res)
    sys.exit(1 if nf else 0)


if __name__ == '__main__':
    main()
