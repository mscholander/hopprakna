# Gatuvy (MVP)

En statisk webbsida som spelar upp en "åktur" mellan två platser med öppna gatubilder, med fakta,
skyltar, väder och mer ovanpå. Ingen server behövs – lägg mappen på valfri statisk webbhost.

- `index.html` – rutt, bildval och uppspelning (WebGL)
- `extras.js` – upplevelselagret (se nedan)
- `sponsors.example.json` – exempel på tips/annonser

## Bildkällor
Panoramax (öppen, ingen nyckel) och Mapillary (gratis token från mapillary.com/dashboard/developers).
Standardläget *Kombinerat* tar Panoramax först och fyller luckor med Mapillary.

## Så blir det en ström i stället för ett bildspel
- **3D-omprojicering:** gatan modelleras som ett "gaturum" (mark + husfasader på vänster och höger sida).
  Kameran glider med konstant fart mellan fotopunkterna och varje foto läggs på modellen, så vägmarkeringar
  strömmar under kameran och fasader glider förbi med äkta parallax. I fotopunkterna visas fotot exakt.
- **Självkalibrering:** vid varje bildbyte provas olika avstånd till fasaderna (4–60 m per sida, 60 m = öppen
  plats) och kamerahöjder (1,4–2,7 m); modellen där de två fotona stämmer bäst används. Sökningen sprids över
  flera bildrutor och modellen glider mjukt mot det nya värdet, så den orsakar inga ryck.
- **Kort övertoning:** fotona blandas bara mitt på sträckan; där ett foto bara visar bilens tak tar det andra över.
- **Lugn kamera och lugna bubblor:** riktningen följer vägen med lite eftersläpning, och faktabubblor glider,
  tonas in och ut med avståndet och byter inte plats i onödan.
- **Skarpt utan flimmer:** bilderna förberäknas i flera storlekar (mipmaps), avkodas i bakgrunden och hämtas
  åtta steg i förväg.

Kvarstående: bilar och människor nära kameran kan dras ut något i övergångarna, eftersom modellen bara känner
till mark och fasader och de dessutom har flyttat sig mellan fotona.

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
