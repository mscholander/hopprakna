#!/usr/bin/env python3
"""Sammanfogning av AI-klipp, svängar och körningsbyten till en film där rörelsen aldrig stannar.

Används av make-film.py. Problemen den löser (uppmätta i Malmö-filmen, nov 2026):
  - Övertoningar mellan två stillbilder fick filmen att stå still (16 av 131 sekunder nästan helt stilla).
    Nu tonas i stället slutet av klippet före över i början av klippet efter, medan båda rör sig.
  - Svängarna (renderade direkt ur 360°-bilden) var dubbelt så skarpa som AI-klippen och hade annan färgton,
    så bilden "poppade" vid varje sväng. Nu får svängen samma skärpa och färg som AI-klippen intill.
  - Svängarna gick från rakt fram till full vridfart på 0,2 s (ett ryck in i och ur varje sväng). Nu ökar och
    minskar vridfarten under en tredjedel av svängen var, toppfarten är begränsad och korta svängar förlängs.
    Mellanbilderna räknas fram exakt (en ren kameravridning är en homografi), så inga dubbelkonturer.
  - AI-klippen (1280×704) sträcktes förut till nyckelbildernas 1280×720, vilket gav ett zoomhopp vid varje sväng.
    Nu byggs filmen i AI-klippens format, och varje skarv riktas in automatiskt (seam_shift/align_seam).
  - Små hopp mellan två AI-klipp döljs med en kort överlappning.
"""
import json, os, subprocess
import numpy as np
import cv2

BLUR_SCALE = 1.0      # andel av den uppmätta skärpeskillnaden som jämnas ut
OVERLAP = {'ai→ai': 4, 'ai→sväng': 12, 'sväng→ai': 12, 'övertoning': 24}


def fit(img, W, H):
    """Skala och beskär till exakt W×H (fyll ut, inga svarta kanter)."""
    h, w = img.shape[:2]; k = max(W / w, H / h)
    img = cv2.resize(img, (max(W, round(w * k)), max(H, round(h * k))), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
    y0, x0 = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
    return img[y0:y0 + H, x0:x0 + W]


def read_clip(path, W, H):
    cap = cv2.VideoCapture(path); out = []
    while True:
        ok, f = cap.read()
        if not ok: break
        out.append(fit(f, W, H))
    return out


def retime(frames, n_out):
    """Jämnt utspridda bildrutor (med mjuk blandning mellan två källrutor) så att klippet blir n_out långt."""
    n = len(frames)
    if n_out <= 1 or n <= 1: return frames[:max(1, n_out)]
    res = []
    for j in range(n_out):
        x = j * (n - 1) / (n_out - 1); k = int(x); t = x - k
        res.append(frames[k] if t < 1e-3 or k + 1 >= n else mix(frames[k], frames[k + 1], t))
    return res


def mix(a, b, t):
    return cv2.addWeighted(a, 1 - t, b, t, 0)


def smoothstep(x): return x * x * (3 - 2 * x)


TURN_PEAK = 40.0      # högsta vridfart i en sväng (grader/s)
TURN_RAMP = 0.35     # andel av svängen som går åt till att öka respektive minska vridfarten
TURN_MIN = 2.0       # kortaste sväng (s); kortare svängar startade och stannade ryckigt


def yaw_homography(W, H, d, fov=92.0, horizon=0.08):
    """Bildavbildning (utbild → källbild) för en kamera som vridits d grader runt lodaxeln, i samma geometri
    som google-frames.view(). En ren vridning ger exakt en homografi, så mellanbilder blir skarpa."""
    f = np.tan(np.radians(fov / 2))
    # pixel = A @ (X, Y, 1) där X, Y är riktningens koordinater i bildplanet (z = 1)
    A = np.array([[W / (2 * f), 0, W / 2 - 0.5], [0, -H * (W / H) / (2 * f), (1 - horizon) * H / 2 - 0.5], [0, 0, 1]])
    c, s_ = np.cos(np.radians(d)), np.sin(np.radians(d))
    R = np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
    return A @ R @ np.linalg.inv(A)


def turn_frames(src, delta, fps, W, H, fov=92.0, horizon=0.08):
    """Svängen räknas om till mjuk start, jämn fart och mjukt stopp med högst TURN_PEAK grader/s.
    Källrutorna är renderade med vinkel delta·smoothstep(k/n); varje ny ruta skapas genom att de två
    närmaste källrutorna vrids exakt till rätt vinkel och blandas (där den ena saknar bild tar den andra över)."""
    n = len(src) - 1; sh, sw = src[0].shape[:2]
    ang_src = [delta * smoothstep(k / n) for k in range(n + 1)]
    T = max(TURN_MIN, abs(delta) / (TURN_PEAK * (1 - TURN_RAMP))); m = max(2, round(T * fps))
    vmax = 1 / (1 - TURN_RAMP); out = []
    for j in range(m + 1):
        t = j / m
        if t < TURN_RAMP: p = vmax * t * t / (2 * TURN_RAMP)
        elif t > 1 - TURN_RAMP: p = 1 - vmax * (1 - t) ** 2 / (2 * TURN_RAMP)
        else: p = vmax * (t - TURN_RAMP / 2)
        th = delta * p
        k = max(0, min(n - 1, int(np.searchsorted(ang_src, th) - 1) if delta > 0 else int(np.searchsorted([-x for x in ang_src], -th) - 1)))
        acc = np.zeros((sh, sw, 3), np.float32); wsum = np.zeros((sh, sw, 1), np.float32)
        span = ang_src[k + 1] - ang_src[k]; u = 0.5 if abs(span) < 1e-6 else min(1, max(0, (th - ang_src[k]) / span))
        for kk, w in ((k, 1 - u), (k + 1, u)):
            if w < 1e-3: continue
            Hm = yaw_homography(sw, sh, th - ang_src[kk], fov, horizon)
            img = cv2.warpPerspective(src[kk], Hm, (sw, sh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=0)
            msk = cv2.warpPerspective(np.ones((sh, sw), np.float32), Hm, (sw, sh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=0)[..., None]
            acc += img.astype(np.float32) * w * msk; wsum += w * msk
        fr = (acc / np.maximum(wsum, 1e-4)).astype(np.uint8)
        if (wsum < 1e-3).any():                                  # kant utan bild (bara vid mycket glesa källrutor)
            fr = np.where(wsum < 1e-3, src[k + (u >= 0.5)], fr)
        out.append(fit(fr, W, H))
    return out


def sharpness(img): return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var())


def blur_to(img, target):
    """Hur mycket oskärpa som behövs för att img ska få ungefär samma skärpa som target."""
    goal = sharpness(target); best = (abs(sharpness(img) - goal), 0.0)
    for s in (0.4, 0.6, 0.8, 1.0, 1.25, 1.5, 1.8, 2.2):
        best = min(best, (abs(sharpness(cv2.GaussianBlur(img, (0, 0), s)) - goal), s))
    return best[1] * BLUR_SCALE


def color_stats(img):
    lab = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    return lab.reshape(-1, 3).mean(0), lab.reshape(-1, 3).std(0) + 1e-3


def match_look(frames, ref_a, ref_b):
    """Ge svängen samma skärpa och färg som klippen intill: som klippet före i början, som klippet efter i slutet."""
    if not frames: return frames
    sa = blur_to(frames[0], ref_a) if ref_a is not None else 0.0
    sb = blur_to(frames[-1], ref_b) if ref_b is not None else sa
    if ref_a is None: sa = sb
    (m0, s0), (m1, s1) = color_stats(frames[0]), color_stats(frames[-1])
    ma, sa_ = color_stats(ref_a) if ref_a is not None else (m0, s0)
    mb, sb_ = color_stats(ref_b) if ref_b is not None else (m1, s1)
    out, n = [], len(frames)
    for j, f in enumerate(frames):
        w = j / max(1, n - 1); sig = sa * (1 - w) + sb * w
        g = cv2.GaussianBlur(f, (0, 0), sig) if sig > 0.05 else f
        lab = cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
        src_m, src_s = m0 * (1 - w) + m1 * w, s0 * (1 - w) + s1 * w
        dst_m, dst_s = ma * (1 - w) + mb * w, sa_ * (1 - w) + sb_ * w
        lab = (lab - src_m) * (dst_s / src_s) + dst_m
        out.append(cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR))
    return out


_orb = None
def seam_shift(a, b):
    """Likformig avbildning (skala, vridning, förskjutning runt bildens mitt) som för b till a, eller None om
    bilderna inte går att rikta in säkert (t.ex. två olika körningar)."""
    global _orb
    _orb = _orb or cv2.ORB_create(2500)
    sm = lambda x: cv2.resize(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    ka, da = _orb.detectAndCompute(sm(a), None); kb, db = _orb.detectAndCompute(sm(b), None)
    if da is None or db is None or len(ka) < 50 or len(kb) < 50: return None
    m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(db, da)
    if len(m) < 50: return None
    pb = np.float32([kb[x.queryIdx].pt for x in m]) * 2; pa = np.float32([ka[x.trainIdx].pt for x in m]) * 2
    M, inl = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC, ransacReprojThreshold=3)
    if M is None or inl.sum() < 60: return None
    h, w = a.shape[:2]; c = np.array([w / 2, h / 2])
    sc = float(np.hypot(M[0, 0], M[1, 0])); rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    t = M[:, :2] @ c + M[:, 2] - c                                 # förskjutning av bildens mitt
    if abs(sc - 1) > 0.08 or abs(rot) > 3 or abs(t[0]) > 0.08 * w or abs(t[1]) > 0.08 * h: return None
    if abs(sc - 1) < 0.002 and abs(rot) < 0.05 and np.hypot(*t) < 0.7: return None
    return sc, rot, t


def warp_part(img, sc, rot, t, f):
    """Bråkdelen f av avbildningen (f = 0 → oförändrad)."""
    if abs(f) < 1e-3: return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), -rot * f, sc ** f); M[:, 2] += np.asarray(t) * f
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)


def align_seam(tail, head, n=15):
    """Riktar in skarven: halva rättningen läggs på slutet av delen före, halva på början av delen efter,
    och tonas bort över n bildrutor åt vardera hållet. Då försvinner små hopp (zoom, några pixlar) utan att synas."""
    r = seam_shift(tail[-1], head[0])
    if r is None: return tail, head, None
    sc, rot, t = r; na, nb = min(n, len(tail) - 1), min(n, len(head) - 1)
    tail = tail[:len(tail) - na] + [warp_part(tail[len(tail) - na + j], sc, rot, t, -0.5 * smoothstep((j + 1) / na)) for j in range(na)] if na > 0 else tail
    head = [warp_part(head[j], sc, rot, t, 0.5 * (1 - smoothstep(j / nb))) for j in range(nb)] + head[nb:] if nb > 0 else head
    return tail, head, r


def clip_size(clips):
    for c in clips:
        if c:
            cap = cv2.VideoCapture(c); w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if w and h: return [w, h]
    return None


def assemble(meta, work, clips, play, cuts, keys_in, keys_out, out, fps=30, size=None, bounds_json=None):
    """clips[i] = AI-klipp från bild i till i+1 (eller None), play[i] = sekunder det ska ta,
    cuts = index där bild i → i+1 ska bli en övertoning, keys_in/keys_out = sökvägar till bildernas in-/ut-vy."""
    # Filmen byggs i AI-klippens eget format (t.ex. 1280×704). Nyckelbilder och svängar (1280×720) beskärs då
    # i stället för att AI-klippen sträcks ut, vilket annars gav 2–3 % zoomhopp vid varje sväng.
    W, H = size or clip_size(clips) or meta.get('size', [1280, 720]); H -= H % 2; W -= W % 2
    fr = meta['frames']
    # 1. plan: delar i ordning. Bildrutorna läses först när delen behövs, så att bara ett par klipp ligger i minnet.
    plan = []
    def still(path, n): img = fit(cv2.imread(path), W, H); return lambda: [img] * n
    def ai(i, drop_first):
        def load():
            src = read_clip(clips[i], W, H)
            if drop_first and len(src) > 1: src = src[1:]                       # första rutan = förra klippets sista
            return retime(src, max(4, round(play[i] * fps)))
        return load
    def turn(path, f):
        def load():
            cap = cv2.VideoCapture(path); src = []
            while True:
                ok, im = cap.read()
                if not ok: break
                src.append(im)
            if 'turn' not in f: return [fit(x, W, H) for x in src]
            return turn_frames(src, f['turn'], fps, W, H, meta.get('fov', 92.0), meta.get('horizon', 0.08))
        return load
    for i in range(len(fr) - 1):
        if i in cuts or clips[i] is None:
            if not plan or plan[-1][0] == 'tona': plan.append(('still', still(keys_out[i], 15)))
            plan.append(('tona', None))
        else:
            plan.append(('ai', ai(i, bool(plan) and plan[-1][0] != 'tona')))
        if fr[i + 1].get('pre'):
            plan.append(('sväng', turn(os.path.join(work, fr[i + 1]['pre']), fr[i + 1])))
    if plan and plan[-1][0] == 'tona': plan.append(('still', still(keys_in[-1], 15)))
    parts = [(k, f) for k, f in plan if k != 'tona']
    tona_before = set(); n = 0
    for k, f in plan:
        if k == 'tona': tona_before.add(n)
        else: n += 1
    cache = {}
    def get(j):
        if j not in cache: cache[j] = parts[j][1]()
        for old in [x for x in cache if x < j - 1]: del cache[old]
        return cache[j]
    def frames_of(j):
        """Delens bildrutor; svängar får skärpa och färg från klippen intill (2)."""
        kind, _ = parts[j]; f = get(j)
        if kind != 'sväng': return f
        prev = next((get(x)[-1] for x in range(j - 1, -1, -1) if parts[x][0] != 'sväng'), None)
        nxt = next((get(x)[0] for x in range(j + 1, len(parts)) if parts[x][0] != 'sväng'), None)
        return match_look(f, prev, nxt)
    # 3. skriv ut med överlappning mellan delarna
    enc = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(fps),
                            '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium', '-movflags', '+faststart', out], stdin=subprocess.PIPE)
    written = 0; buf = []; prev_kind = None; bounds = []; aligned = []
    def write(frames):
        nonlocal written
        for f in frames: enc.stdin.write(f.tobytes()); written += 1
    for j, (kind, _) in enumerate(parts):
        frames = frames_of(j); dissolve = j in tona_before
        if buf:
            if len(buf) > 2 and len(frames) > 2:
                tl, frames, r = align_seam(buf, list(frames))
                if r: buf = tl; aligned.append(r)
            key = 'övertoning' if dissolve else f'{prev_kind if prev_kind != "still" else "ai"}→{kind if kind != "still" else "ai"}'
            L = min(OVERLAP.get(key, 4), len(buf) - 1, len(frames) // 2)
            if L > 0:
                write(buf[:-L]); bounds.append((written + L // 2, key if not dissolve else f'{prev_kind}→övertoning→{kind}'))
                blend = [mix(buf[-L + j2], frames[j2], smoothstep((j2 + 1) / (L + 1))) for j2 in range(L)]
                buf = blend + list(frames[L:])
            else:
                write(buf); bounds.append((written, key)); buf = list(frames)
        else: buf = list(frames)
        prev_kind = kind
    write(buf); enc.stdin.close(); enc.wait()
    if bounds_json: json.dump(bounds, open(bounds_json, 'w'))
    if aligned: print(f'{len(aligned)} skarvar inriktade (största zoom {max(abs(a[0] - 1) for a in aligned) * 100:.1f} %, '
                      f'största förskjutning {max(np.hypot(*a[2]) for a in aligned):.0f} px)')
    return written
