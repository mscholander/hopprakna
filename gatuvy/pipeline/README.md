# Filmkedjan: färdiga filmer av gatubilder

Gör en riktig videofil av en rutt, i stället för att sidan tonar mellan foton i webbläsaren.

1. **Nyckelbilder** – `node export-frames.js --from "…" --to "…" --out work/paris --count 11`
   Återanvänder Gatuvys bildval och stabilisering, hämtar originalfoton och sparar vyn rakt fram (1280×720).
   Kräver Node och Playwright (`npm i playwright && npx playwright install chromium`).
2. **Mellanbilder med FILM** (Googles "Frame Interpolation for Large Motion"), ett av:
   - egen dator utan Docker: `python3 film-local.py work/paris --model film_net_fp32.pt --times 4`
     (PyTorch; ca 18 s per mellanbild på en vanlig processor med 4 kärnor, mycket snabbare med NVIDIA-kort)
   - Replicate eller egen Docker: `python3 make-film.py work/paris --times 4`
     (kräver `REPLICATE_API_TOKEN` och kredit på kontot, eller `--film-local http://localhost:5000`)
3. **Bildförbättring med MAXIM** (valfritt, före steg 2): `make-film.py … --enhance "Image Deblurring (GoPro)"`.

`--times 4` ger 16 steg per bildpar, alltså drygt en halv sekund film per par vid 30 bilder per sekund.

Status: alla tre skripten är körda. `make-film.py` är körd mot Replicate med både FILM och MAXIM;
Docker-läget (`--film-local`, `--maxim-local`) är inte provat.

Erfarenheter från testet (Rue de Rivoli, Paris, 19 nyckelbilder):
- Hos Replicate tar FILM ca 13 s beräkningstid per bildpar och MAXIM ca 25 s per bild.
- MAXIM gav ingen synlig skillnad på Panoramax originalfoton, som redan är skarpa.
- FILM fungerar bäst när fotona ligger högst ca 2,5 m isär; föremål nära kameran (pelare, bilar,
  byggnadsställningar) förvrängs ändå i mellanbilderna.
