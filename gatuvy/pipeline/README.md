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
   - generativ videomodell med start- och slutbild (bäst resultat hittills):
     `python3 make-film.py work/paris --engine wan-video/wan-2.2-i2v-fast --resolution 720p`
     Använder masterprompten i `master-prompt.txt` för alla klipp. Exportera då nyckelbilder ca 25 m isär
     (`export-frames.js --spacing 2 --step 9`): varje klipp blir 81 bildrutor, 2,7 s i 30 bilder/s.
3. **Bildförbättring med MAXIM** (valfritt, före steg 2): `make-film.py … --enhance "Image Deblurring (GoPro)"`.

`--times 4` ger 16 steg per bildpar, alltså drygt en halv sekund film per par vid 30 bilder per sekund.

Status: alla tre skripten är körda. `make-film.py` är körd mot Replicate med både FILM och MAXIM;
Docker-läget (`--film-local`, `--maxim-local`) är inte provat.

Erfarenheter från testet (Rue de Rivoli, Paris, 19 nyckelbilder):
- Hos Replicate tar FILM ca 13 s beräkningstid per bildpar och MAXIM ca 25 s per bild.
- MAXIM gav ingen synlig skillnad på Panoramax originalfoton, som redan är skarpa.
- Wan 2.2 (start- och slutbild) ger skarp, filmlik rörelse även med 25 m mellan fotona, och klippen möts
  utan synligt hopp i skarvarna. Modellen hittar på detaljer (människor, bilar) mellan fotona.
  Ca 75–90 s beräkningstid per klipp hos Replicate.
- Nyckelbilder ca 11 m isär (`--spacing 2 --step 4`) ger klart bäst resultat med Wan; vid 25 m hittar
  modellen på för mycket (bilar kör in i väggar, fotgängare rör sig slumpartat) och farten blir för hög.
- Pris hos Replicate för `wan-video/wan-2.2-i2v-fast` (nov 2026): 0,05 dollar per klipp i 480p och
  0,11 dollar i 720p. `lucataco/wan-2.2-first-last-frame` debiteras per sekund och blev ca 0,07 dollar
  per klipp i 480p, alltså inte billigare.
- `prunaai/p-video` (0,02 dollar per sekund i 720p, 0,005 i utkastläge `--draft`) ger rörelse och rena skarvar
  med 3 s klipp: ca 0,06 dollar per klipp i 720p. Utkastläget är mjukare och förvränger småsaker.
  `bytedance/seedance-1.5-pro` är nedlagd hos Replicate.
- Hel rutt körd: Louvren–Triumfbågen (3,9 km), 365 nyckelbilder med `--spacing 2 --gap 11`, 364 klipp med
  `--engine prunaai/p-video --resolution 720p --draft --speed 4.1`. Alla klipp gick igenom; 14 minuters
  generering med fyra samtidiga körningar och en film på 15 min 41 s.
- FILM fungerar bäst när fotona ligger högst ca 2,5 m isär; föremål nära kameran (pelare, bilar,
  byggnadsställningar) förvrängs ändå i mellanbilderna.
