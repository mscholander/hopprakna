#!/usr/bin/env python3
"""Sammanfogning av AI-klipp, svängar och körningsbyten till en film där rörelsen aldrig stannar.

Används av make-film.py. Problemen den löser (uppmätta i Malmö-filmen, nov 2026):
  - Övertoningar mellan två stillbilder fick filmen att stå still (16 av 131 sekunder nästan helt stilla).
    Nu tonas i stället slutet av klippet före över i början av klippet efter, medan båda rör sig.
  - Svängarna (renderade direkt ur 360°-bilden) var dubbelt så skarpa som AI-klippen och hade annan färgton,
    så bilden "poppade" vid varje sväng. Nu får svängen samma skärpa och färg som AI-klippen intill.
  - Svängarna startade och stannade mjukt (= bilen stod still i hörnet). Nu vrids kameran med jämnare fart,
    och svängen överlappar AI-klippen före och efter så att framåtrörelsen glider över i vridningen.
  - Små hopp mellan två AI-klipp döljs med en kort överlappning.
"""
import json, os, subprocess
import numpy as np
import cv2

# Uppmätt: full skärpematchning gjorde svängarna mjukare än AI-klippen (0,6×), eftersom AI-rutorna intill
# själva blandas mellan två källrutor. 0,6 av oskärpan ger samma upplevda skärpa.
BLUR_SCALE = 0.6
OVERLAP = {'ai→ai': 4, 'ai→sväng': 9, 'sväng→ai': 9, 'övertoning': 24}


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


def turn_profile(frames, ramp=0.2):
    """Svängklippen är renderade med mjuk start och mjukt stopp (smoothstep). Räkna om till jämnare
    vridfart: fart som ökar under de första 20 % och minskar under de sista 20 %, konstant däremellan."""
    n = len(frames); out = []
    vmax = 1 / (1 - ramp)
    for j in range(n):
        t = j / (n - 1)
        if t < ramp: p = vmax * t * t / (2 * ramp)
        elif t > 1 - ramp: p = 1 - vmax * (1 - t) ** 2 / (2 * ramp)
        else: p = vmax * (t - ramp / 2)
        lo, hi = 0.0, 1.0                                   # hitta källrutan där smoothstep(x) = p
        for _ in range(30):
            mid = (lo + hi) / 2
            if smoothstep(mid) < p: lo = mid
            else: hi = mid
        x = (lo + hi) / 2 * (n - 1); k = int(x); u = x - k
        out.append(frames[k] if k + 1 >= n else mix(frames[k], frames[k + 1], u))
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


def assemble(meta, work, clips, play, cuts, keys_in, keys_out, out, fps=30, size=None, bounds_json=None):
    """clips[i] = AI-klipp från bild i till i+1 (eller None), play[i] = sekunder det ska ta,
    cuts = index där bild i → i+1 ska bli en övertoning, keys_in/keys_out = sökvägar till bildernas in-/ut-vy."""
    W, H = size or meta.get('size', [1280, 720]); H -= H % 2
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
    def turn(path): return lambda: turn_profile(read_clip(path, W, H))
    for i in range(len(fr) - 1):
        if i in cuts or clips[i] is None:
            if not plan or plan[-1][0] == 'tona': plan.append(('still', still(keys_out[i], 15)))
            plan.append(('tona', None))
        else:
            plan.append(('ai', ai(i, bool(plan) and plan[-1][0] != 'tona')))
        if fr[i + 1].get('pre'):
            plan.append(('sväng', turn(os.path.join(work, fr[i + 1]['pre']))))
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
    written = 0; buf = []; prev_kind = None; bounds = []
    def write(frames):
        nonlocal written
        for f in frames: enc.stdin.write(f.tobytes()); written += 1
    for j, (kind, _) in enumerate(parts):
        frames = frames_of(j); dissolve = j in tona_before
        if buf:
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
    return written
