# Gatuvy (MVP)

En statisk webbsida som spelar upp en "åktur" mellan två platser med öppna gatubilder, med fakta,
skyltar, väder och mer ovanpå. Ingen server behövs – lägg mappen på valfri statisk webbhost.

- `index.html` – rutt, bildval och uppspelning (WebGL)
- `extras.js` – upplevelselagret (se nedan)
- `sponsors.example.json` – exempel på tips/annonser

## Bildkällor
Panoramax (öppen, ingen nyckel) och Mapillary (gratis token från mapillary.com/dashboard/developers).
Standardläget *Kombinerat* tar Panoramax först och fyller luckor med Mapillary.

## Upplevelse (panelen → Upplevelse)
| Funktion | Vad den gör | Datakälla |
|---|---|---|
| Faktabubblor | Namn och kort beskrivning som sitter fast på platsen i bilden | Wikipedia, Wikidata |
| Gatuskylt | Gatan och stadsdelen man kör på just nu | OpenStreetMap (Nominatim) |
| Tid och väder | Lokal tid och aktuellt väder på platsen | Open-Meteo, reserv met.no + timeapi.io |
| Språkruta | En fras på platsens språk då och då | inbyggd lista, ~40 länder |
| Vykortsstopp | Resan stannar vid kända platser, vrider blicken dit och visar ett kort med bild, text och QR-kod | Wikipedia |
| Guideröst | Läser upp vad man passerar (webbläsarens talsyntes) | – |
| Gissa staden | Döljer platsnamnen, ger tre ledtrådar och avslöjar staden efter ~45 s | – |
| Rösta med mobilen | QR-kod där tittarna väljer nästa stad i *Världen runt* | ntfy.sh |
| Världen runt | Byter stad automatiskt bland 21 städer där det är dag just nu | inbyggd lista |
| Bilder från | Nyaste, äldsta, sommar eller vinter | bildernas datum |
| Hemmabas | T.ex. hotellet: visar promenadtid och QR med vägbeskrivning dit | – |
| Tips och annonser | Egna platser (JSON) som visas som guldbubblor och tipsstopp | egen fil |

Städerna utan 360°-bilder i Panoramax (allt utom Paris, Lyon, Marseille och Amsterdam) kräver Mapillary-token.

## Kiosklänkar
Alla inställningar kan ges i adressen, så en skärm kan startas med en enda länk:

- Världen runt med gissa staden och röstning, ny stad var 10:e minut:
  `index.html?mode=world&x=bubbles,sign,weather,phrase,stops,quiz,votes&tour=10&autoplay=1`
- Hotell i Västerås med promenadtider och egna tips:
  `index.html?from=Stora Torget, Västerås&to=Västerås centralstation&x=bubbles,sign,weather,stops&home=Hotellets adress, Västerås&homeName=Hotellet&sponsors=sponsors.json&autoplay=1`
- Mapillary-token sparas i webbläsaren första gången: lägg till `&token=MLY|...`.

`x` är en kommaseparerad lista av: `bubbles, sign, weather, phrase, stops, voice, quiz, votes`.
Övriga: `mode=route|world`, `tour=<minuter>`, `epoch=any|newest|oldest|summer|winter`, `lang=sv|en`,
`home=<adress>`, `homeName=<namn>`, `sponsors=<länk till JSON>`, `tempo=<sek per bild>`.

## Tips och annonser
Lägg en `sponsors.json` bredvid `index.html` (eller ange `sponsors=<länk>`), samma format som
`sponsors.example.json`: `name`, `text`, `lat`, `lon`, valfritt `url` (blir QR-kod) och `image`.
Tips inom 300 m från rutten visas som guldbubblor och som tipsstopp.

## Begränsningar
- De publika tjänsterna (Nominatim, OSRM, Wikipedia, Open-Meteo, ntfy.sh) är gratis med
  användningsgränser. För skarp drift med många skärmar behövs egna eller betalda tjänster.
- Röstningen går via ett publikt ntfy.sh-ämne med slumpat namn; den som har länken kan rösta.
- Guiderösten låter olika i olika webbläsare och kan saknas helt på vissa enheter.
