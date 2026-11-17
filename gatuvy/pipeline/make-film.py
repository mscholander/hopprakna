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
OFFICIAL = {}
# Generativa modeller med start- och slutbild: hur deras indata heter. (a = flaggorna, p = masterprompten)
ENGINES = {
    'wan-video/wan-2.2-i2v-fast': lambda a, p, first, last: {'image': first, 'last_image': last, 'prompt': p, 'resolution': a.resolution,
                                                             'num_frames': a.frames, 'seed': a.seed},
    'lucataco/wan-2.2-first-last-frame': lambda a, p, first, last: {'start_image': first, 'end_image': last, 'prompt': p,
                                                                     'duration_seconds': a.seconds, 'seed': a.seed},
    'bytedance/seedance-1.5-pro': lambda a, p, first, last: {'image': first, 'last_frame_image': last, 'prompt': p, 'resolution': a.resolution,
                                                              'duration': max(4, int(a.seconds)), 'generate_audio': False, 'seed': a.seed},
    'prunaai/p-video': lambda a, p, first, last: {'image': first, 'last_frame_image': last, 'prompt': p, 'resolution': a.resolution,
                                                  'duration': max(1, min(20, int(a.seconds))), 'fps': 24, 'save_audio': False, 'prompt_upsampling': False, 'seed': a.seed, 'draft': a.draft},
    'wan-video/wan-2.7-i2v': lambda a, p, first, last: {'first_frame': first, 'last_frame': last, 'prompt': p, 'resolution': a.resolution,
                                                         'duration': int(a.seconds), 'seed': a.seed, 'enable_prompt_expansion': False},
}

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
    # 'film'/'maxim' körs med låst version; andra anges som ägare/namn (Replicates officiella modeller)
    if model in VERSIONS: url, body = API + '/predictions', {'version': VERSIONS[model], 'input': inputs}
    else:
        if model not in OFFICIAL:   # officiella modeller körs per namn, övriga behöver ett versionsnummer
            info = json.loads(http(f'{API}/models/{model}'))
            OFFICIAL[model] = None if info.get('is_official') else info['latest_version']['id']
        v = OFFICIAL[model]
        url, body = (API + '/predictions', {'version': v, 'input': inputs}) if v else (f'{API}/models/{model}/predictions', {'input': inputs})
    p = json.loads(http(url, json.dumps(body).encode(), {'Content-Type': 'application/json', 'Prefer': 'wait=30'}))
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
    if path.endswith('.jpg') and open(path, 'rb').read(4) == b'\x89PNG':   # MAXIM svarar med PNG: gör om till JPG
        tmp = path + '.png'; os.replace(path, tmp)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', tmp, '-q:v', '2', path], check=True); os.remove(tmp)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('work'); ap.add_argument('--out', default=None)
    ap.add_argument('--enhance', default='', help='MAXIM-läge, t.ex. "Image Deblurring (GoPro)"; tomt = ingen förbättring')
    ap.add_argument('--times', type=int, default=4, help='FILM: 2^times steg per bildpar (4 = 16 steg)')
    ap.add_argument('--engine', default='film', help='"film" (mellanbilder) eller en generativ videomodell med start- och slutbild, t.ex. wan-video/wan-2.2-i2v-fast')
    ap.add_argument('--prompt', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'master-prompt.txt'), help='fil med masterprompten för generativa modeller')
    ap.add_argument('--resolution', default='720p'); ap.add_argument('--frames', type=int, default=81, help='bildrutor per klipp för generativa modeller')
    ap.add_argument('--seed', type=int, default=1); ap.add_argument('--seconds', type=float, default=5, help='kliplängd för modeller som anger tid')
    ap.add_argument('--draft', action='store_true', help='utkastläge (billigare, lägre kvalitet) för modeller som har det')
    ap.add_argument('--speed', type=float, default=0, help='jämn fart i m/s: varje klipp spelas på (avstånd mellan fotona)/fart sekunder, t.ex. 4.1 = 15 km/h')
    ap.add_argument('--skip-check', action='store_true', help='hoppa över bildkontrollen (rekommenderas inte)')
    ap.add_argument('--max-gap', type=float, default=30, help='större lucka än så här (meter) mellan två foton genereras inte, utan blir en kort övertoning')
    ap.add_argument('--clip-seconds', type=float, default=0, help='spela varje klipp på så här många sekunder i den färdiga filmen (0 = alla bildrutor i --fps)')
    ap.add_argument('--fps', type=int, default=30); ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--film-local', default=''); ap.add_argument('--maxim-local', default='')
    a = ap.parse_args()
    if not shutil.which('ffmpeg'): sys.exit('ffmpeg saknas')
    hosted = not (a.film_local and (a.maxim_local or not a.enhance))
    if hosted and not os.environ.get('REPLICATE_API_TOKEN'): sys.exit('Sätt REPLICATE_API_TOKEN (eller använd --film-local/--maxim-local)')
    # 0. bildkontroll: rätt ordning, rätt håll, följer sträckan. Ingen generering om något underkänns.
    if not a.skip_check:
        import importlib.util
        spec = importlib.util.spec_from_file_location('cf', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'check-frames.py'))
        cfm = importlib.util.module_from_spec(spec); spec.loader.exec_module(cfm)
        m0, res = cfm.check(a.work); nfail = cfm.report(a.work, m0, res)
        if nfail: sys.exit(f'Avbryter: {nfail} bilder underkändes i bildkontrollen (se {os.path.join(a.work, "kontroll.png")}).\n'
                          f'Rätta urvalet, eller kör: python3 check-frames.py {a.work} --fix <ny mapp>')
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
        return data_uri(use)   # FILM kräver en bild med filändelse, så den bäddas in i anropet i stället för att laddas upp
    # en bild kan ha en egen ut-vy (efter en sväng som vrids i bilden): klippet till nästa bild startar från den
    keys_out = [f.get('file_out', f['file']) for f in meta['frames']]
    with cf.ThreadPoolExecutor(a.jobs) as ex: urls = list(ex.map(prep, keys)); urls_out = [u if ko == k else prep(ko) for u, k, ko in zip(urls, keys, keys_out)]
    print(f'{len(keys)} nyckelbilder klara ({tag}), {time.time() - t0:.0f} s')

    # 2. ett klipp per bildpar
    gen = a.engine != 'film'
    if gen and a.engine not in ENGINES: sys.exit('Okänd modell. Kända: ' + ', '.join(ENGINES))
    label = ('gen-' + a.engine.split('/')[-1] + ('-' + a.resolution if 'resolution' in ENGINES[a.engine](a, '', '', '') else '') + ('-draft' if a.draft else '')) if gen else f't{a.times}'
    prompt = open(a.prompt).read().strip() if gen else ''
    gaps = [f.get('gap', 0) for f in meta['frames']][1:]
    # hur länge varje klipp ska synas i filmen: jämn fart om --speed är satt, annars --clip-seconds
    play = [max(0.4, g / a.speed) if a.speed and g else a.clip_seconds for g in gaps]
    # ingen generering över långa luckor eller byten av fotokörning (link = dissolve): där blir det en övertoning
    cuts = [i for i, g in enumerate(gaps) if gen and (g > a.max_gap or meta['frames'][i + 1].get('link') == 'dissolve')]
    for i in cuts: play[i] = 1.0
    failed = []
    def clip(i):
        dst = os.path.join(d, f'clip_{i:04d}_{label}.mp4')
        if i in cuts: return None   # för långt mellan fotona för att generera något trovärdigt
        if not os.path.exists(dst):
            if gen:   # samma masterprompt för alla klipp; startbild = detta foto, slutbild = nästa
                import copy, math
                ai = copy.copy(a)
                if a.speed: ai.seconds = min(5, max(1, math.ceil(play[i])))   # generera minst så långt klipp som ska visas
                for attempt in range(3):
                    try:
                        out = run_hosted(a.engine, ENGINES[a.engine](ai, prompt, urls_out[i], urls[i + 1]))
                        save(out if isinstance(out, str) else out[0], dst); break
                    except Exception as e:
                        if attempt == 2: failed.append(i); print(f'klipp {i} misslyckades: {str(e)[:120]}', flush=True); return None
                        time.sleep(5)
                if (i + 1) % 25 == 0: print(f'  {i + 1} klipp påbörjade, {time.time() - t0:.0f} s', flush=True)
            else:
                inp = {'frame1': urls_out[i], 'frame2': urls[i + 1], 'times_to_interpolate': a.times}
                save(run_local(a.film_local, inp) if a.film_local else run_hosted('film', inp), dst)
        return dst
    with cf.ThreadPoolExecutor(a.jobs) as ex: clips = list(ex.map(clip, range(len(keys) - 1)))
    print(f'{len(clips)} klipp klara, {time.time() - t0:.0f} s')

    # 3. foga ihop (assemble.py): överlappande övergångar så att rörelsen aldrig stannar, och svängar som får
    #    samma skärpa och färg som AI-klippen intill.
    out = a.out or os.path.join(a.work, f'film_{tag}_{label}.mp4')
    import importlib.util
    spec = importlib.util.spec_from_file_location('asm', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assemble.py'))
    asm = importlib.util.module_from_spec(spec); spec.loader.exec_module(asm)
    kd = d if a.enhance else a.work
    n_out = asm.assemble(meta, a.work, clips, [p or 2.7 for p in play], set(cuts),
                         [os.path.join(kd, k) for k in keys], [os.path.join(kd, k) for k in keys_out], out, fps=a.fps,
                         bounds_json=os.path.splitext(out)[0] + '_skarvar.json')
    print(f'{n_out} bildrutor ({n_out / a.fps:.1f} s)')
    # fotograferna ska anges (bilderna är öppet licensierade)
    names = sorted({f.get('credit', '') for f in meta['frames'] if f.get('credit')})
    open(os.path.splitext(out)[0] + '_credits.txt', 'w').write('Gatubilder:\n' + '\n'.join('  © ' + n for n in names) + '\n')
    if cuts: print(f'{len(cuts)} luckor över {a.max_gap:.0f} m blev korta övertoningar i stället för genererade klipp')
    if failed: print(f'{len(failed)} klipp gick inte att generera och ersattes med övertoning: {sorted(failed)}')
    print(f'Film: {out}')
    if stats['runs']: print(f'Replicate: {stats["runs"]} körningar, {stats["seconds"]:.0f} s beräkningstid')

if __name__ == '__main__': main()
