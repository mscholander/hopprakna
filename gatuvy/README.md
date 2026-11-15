# Gatuvy (MVP)

En enda statisk sida som spelar upp en "åktur" mellan två platser med öppna gatubilder.

- Öppna `index.html` i en webbläsare (eller lägg den på valfri statisk webbhost).
- Kioskläge: `index.html?from=Slussen, Stockholm&to=Odenplan, Stockholm&autoplay=1`

Flöde: Nominatim (adress → koordinat) → OSRM (körväg) → Panoramax eller Mapillary
(gatubilder längs vägen) → WebGL-uppspelning med övertoning. 360°-bilder vrids i färdriktningen.

Mapillary kräver en gratis access token (mapillary.com/dashboard/developers) och är inte testad än.
De publika Nominatim/OSRM-servrarna är demotjänster och ska bytas mot egna/betalda i produktion.
