#!/usr/bin/env python3
"""Steg 2 i filmkedjan: gör en film av nyckelbilderna från export-frames.js.

    python3 make-film.py work/paris --out paris.mp4

1. (valfritt) MAXIM förbättrar varje nyckelbild:   --enhance "Image Deblurring (GoPro)"
2. FILM räknar fram mellanbilder för varje bildpar: --times 4  (2^4 = 16 steg per par)
3. ffmpeg fogar ihop klippen till en film.

Modellerna körs hos Replicate (kräver miljövariabeln REPLICATE_API_TOKEN) eller på egen dator med
Docker och NVIDIA-grafikkort:
    docker run -d -p 5000:5000 --gpus=all r8.im/google-research/frame-interpolation
    docker run -d -p 5001:5000 --gpus=all r8.im/google-research/maxim
    python3 make-film.py work/paris --film-local http://localhost:5000 --maxim-local http://localhost:5001
Allt som hämtats sparas i arbetsmappen, så ett avbrutet jobb fortsätter där det slutade.
"""
import argparse, base64, json, os, subprocess, sys, time, urllib.request, urllib.error, uuid, shutil, concurrent.futures as cf

API = 'https://api.replicate.com/v1'
VERSIONS = {  # låsta versioner, så att resultatet inte ändras om modellerna uppdateras
    'film': '4f88a16a13673a8b589c18866e540556170a5bcb2ccdc12de556e800e9456d3d',
    'maxim': '494ca4d578293b4b93945115601b6a38190519da18467556ca223d219c3af9f9',
}
stats = {'runs': 0, 'seconds': 0.0}

def http(url, data=None, headers=None, method=None, timeout=300):
    h = {'User-Agent': 'gatuvy-pipeline'}
    if url.startswith(API): h['Authorization'] = 'Bearer ' + os.environ.get('REPLICATE_API_TOKEN', '')
    h.update(headers or {})
    attempt = throttled = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:300]
            if e.code == 429 and throttled < 60:      # Replicates gräns för antal körningar per minut: vänta så länge de ber om
                throttled += 1; attempt -= 1
                try: wait = float(json.loads(body).get('retry_after', 0))
                except Exception: wait = 0
                time.sleep((wait or float(e.headers.get('Retry-After') or 10)) + 1); continue
            if e.code in (500, 502, 503) and attempt < 6: time.sleep(3 * attempt); continue
            raise RuntimeError(f'{url.split("?")[0]} svarade {e.code}: {body}')
        except (urllib.error.URLError, TimeoutError):
            if attempt < 6: time.sleep(3 * attempt); continue
            raise

def upload(path):
    """Ladda upp en fil till Replicate och få en adress som modellerna kan läsa."""
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="content"; filename="{os.path.basename(path)}"\r\n'
            f'Content-Type: image/jpeg\r\n\r\n').encode() + open(path, 'rb').read() + f'\r\n--{b}--\r\n'.encode()
    return json.loads(http(API + '/files', body, {'Content-Type': 'multipart/form-data; boundary=' + b}))['urls']['get']

def data_uri(path):
    return 'data:image/jpeg;base64,' + base64.b64encode(open(path, 'rb').read()).decode()

def run_hosted(model, inputs):
    p = json.loads(http(API + '/predictions', json.dumps({'version': VERSIONS[model], 'input': inputs}).encode(),
                        {'Content-Type': 'application/json', 'Prefer': 'wait=30'}))
    while p['status'] in ('starting', 'processing'):
        time.sleep(2); p = json.loads(http(p['urls']['get']))
    if p['status'] != 'succeeded': raise RuntimeError(f'{model} misslyckades: {p.get("error")}')
    stats['runs'] += 1; stats['seconds'] += (p.get('metrics') or {}).get('predict_time', 0)
    return p['output']

def run_local(endpoint, inputs):
    p = json.loads(http(endpoint.rstrip('/') + '/predictions', json.dumps({'input': inputs}).encode(), {'Content-Type': 'application/json'}, timeout=1800))
    if p.get('status') not in (None, 'succeeded'): raise RuntimeError(f'lokal modell misslyckades: {p.get("error")}')
    return p['output']

def save(output, path):
    if output.startswith('data:'): open(path, 'wb').write(base64.b64decode(output.split(',', 1)[1]))
    else: open(path, 'wb').write(http(output, timeout=600))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('work'); ap.add_argument('--out', default=None)
    ap.add_argument('--enhance', default='', help='MAXIM-läge, t.ex. "Image Deblurring (GoPro)"; tomt = ingen förbättring')
    ap.add_argument('--times', type=int, default=4, help='FILM: 2^times steg per bildpar (4 = 16 steg)')
    ap.add_argument('--fps', type=int, default=30); ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--film-local', default=''); ap.add_argument('--maxim-local', default='')
    a = ap.parse_args()
    if not shutil.which('ffmpeg'): sys.exit('ffmpeg saknas')
    hosted = not (a.film_local and (a.maxim_local or not a.enhance))
    if hosted and not os.environ.get('REPLICATE_API_TOKEN'): sys.exit('Sätt REPLICATE_API_TOKEN (eller använd --film-local/--maxim-local)')
    meta = json.load(open(os.path.join(a.work, 'frames.json'))); keys = [f['file'] for f in meta['frames']]
    tag = ('maxim-' + ''.join(c for c in a.enhance.lower() if c.isalnum())) if a.enhance else 'raw'
    d = os.path.join(a.work, tag); os.makedirs(d, exist_ok=True)
    t0 = time.time()

    # 1. nyckelbilder: ladda upp, och förbättra med MAXIM om det är valt
    def prep(k):
        src = os.path.join(a.work, k); dst = os.path.join(d, k)
        if a.enhance and not os.path.exists(dst):
            if a.maxim_local: save(run_local(a.maxim_local, {'image': data_uri(src), 'model': a.enhance}), dst)
            else: save(run_hosted('maxim', {'image': upload(src), 'model': a.enhance}), dst)
        use = dst if a.enhance else src
        return data_uri(use) if a.film_local else upload(use)
    with cf.ThreadPoolExecutor(a.jobs) as ex: urls = list(ex.map(prep, keys))
    print(f'{len(keys)} nyckelbilder klara ({tag}), {time.time() - t0:.0f} s')

    # 2. ett klipp per bildpar
    def clip(i):
        dst = os.path.join(d, f'clip_{i:04d}_t{a.times}.mp4')
        if not os.path.exists(dst):
            inp = {'frame1': urls[i], 'frame2': urls[i + 1], 'times_to_interpolate': a.times}
            save(run_local(a.film_local, inp) if a.film_local else run_hosted('film', inp), dst)
        return dst
    with cf.ThreadPoolExecutor(a.jobs) as ex: clips = list(ex.map(clip, range(len(keys) - 1)))
    print(f'{len(clips)} klipp klara, {time.time() - t0:.0f} s')

    # 3. foga ihop: varje klipp slutar på nästa klipps första bild, så den dubbla bilden tas bort
    out = a.out or os.path.join(a.work, f'film_{tag}_t{a.times}.mp4')
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
    for c in clips: args += ['-i', c]
    n = len(clips)
    parts = ''.join(f'[{i}:v]{"trim=start_frame=1," if i else ""}setpts=PTS-STARTPTS,fps={a.fps}[v{i}];' for i in range(n))
    args += ['-filter_complex', parts + ''.join(f'[v{i}]' for i in range(n)) + f'concat=n={n}:v=1[o]', '-map', '[o]',
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', out]
    subprocess.run(args, check=True)
    print(f'Film: {out}')
    if stats['runs']: print(f'Replicate: {stats["runs"]} körningar, {stats["seconds"]:.0f} s beräkningstid')

if __name__ == '__main__': main()
