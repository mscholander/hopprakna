#!/usr/bin/env python3
"""Kör FILM (mellanbildsberäkning) på egen dator utan Docker, med PyTorch. Fungerar på vanlig processor
(långsamt, ca 10–20 s per mellanbild i 720p) och snabbt med NVIDIA-grafikkort.

    pip install torch numpy pillow
    # modellen (138 MB): github.com/dajes/frame-interpolation-pytorch → Releases → film_net_fp32.pt
    python3 film-local.py work/paris --model film_net_fp32.pt --times 4 --out paris.mp4

Läser nyckelbilderna från export-frames.js, sparar mellanbilderna som JPG i work/.../film_tN/ och fogar
ihop dem med ffmpeg. Ett avbrutet jobb fortsätter där det slutade.
"""
import argparse, json, os, subprocess, sys, time
import numpy as np, torch
from PIL import Image

def load(path, dev):
    a = np.asarray(Image.open(path).convert('RGB'), dtype=np.float32) / 255.0
    return torch.from_numpy(a).permute(2, 0, 1)[None].to(dev)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('work'); ap.add_argument('--model', required=True); ap.add_argument('--out', default=None)
    ap.add_argument('--times', type=int, default=4, help='2^times steg per bildpar'); ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--keys', default='', help='undermapp med förbättrade nyckelbilder (t.ex. från MAXIM)')
    a = ap.parse_args()
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = torch.jit.load(a.model, map_location=dev).eval()
    meta = json.load(open(os.path.join(a.work, 'frames.json'))); keys = [f['file'] for f in meta['frames']]
    src = os.path.join(a.work, a.keys) if a.keys else a.work
    d = os.path.join(a.work, f'film_t{a.times}' + ('_' + a.keys if a.keys else '')); os.makedirs(d, exist_ok=True)
    n = 2 ** a.times; t0 = time.time(); done = 0
    def pad(x):   # modellen vill ha mått delbara med 64
        h, w = x.shape[2:]; ph, pw = (64 - h % 64) % 64, (64 - w % 64) % 64
        return torch.nn.functional.pad(x, (0, pw, 0, ph), mode='replicate'), h, w
    def save(x, h, w, path):
        Image.fromarray((x[0, :, :h, :w].clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255 + .5).astype(np.uint8)).save(path, quality=95)
    with torch.no_grad():
        for i in range(len(keys) - 1):
            name = lambda k: os.path.join(d, f'f_{i:04d}_{k:03d}.jpg')
            if all(os.path.exists(name(k)) for k in range(n)): continue
            A, h, w = pad(load(os.path.join(src, keys[i]), dev)); B, _, _ = pad(load(os.path.join(src, keys[i + 1]), dev))
            fr = {0: A, n: B}
            def mid(lo, hi):    # som FILM själv gör: halvera intervallet om och om igen
                nonlocal done
                if hi - lo < 2: return
                m = (lo + hi) // 2
                fr[m] = model(fr[lo], fr[hi], torch.full((1, 1), 0.5, device=dev)); done += 1
                mid(lo, m); mid(m, hi)
            mid(0, n)
            for k in range(n): save(fr[k], h, w, name(k))
            if i == len(keys) - 2: save(B, h, w, os.path.join(d, f'f_{i + 1:04d}_000.jpg'))
            print(f'par {i + 1}/{len(keys) - 1} klart, {time.time() - t0:.0f} s ({(time.time() - t0) / max(1, done):.1f} s per mellanbild)', flush=True)
    out = a.out or os.path.join(a.work, f'film_local_t{a.times}' + ('_' + a.keys if a.keys else '') + '.mp4')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', str(a.fps), '-pattern_type', 'glob', '-i', os.path.join(d, 'f_*.jpg'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', out], check=True)
    print('Film:', out)

if __name__ == '__main__': main()
