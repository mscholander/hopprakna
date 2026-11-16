// Gatuvy – upplevelselager ovanpå resan:
// faktabubblor, gatuskylt, tid och väder, språkruta, vykortsstopp, guideröst, gissa staden,
// världen runt, röstning med mobilen (QR), hemmabas (t.ex. hotellet) och tips/annonser.
//
// Filen laddas före huvudskriptet i index.html och pratar med det via window.GV:
//   huvudskriptet skickar händelser:  trip, frame, tick, holdend, compose, stop, manual
//   och frågar:                       GV.hold(frame, index), GV.startWorld(), GV.urlParams(), GV.opts
(function () {
'use strict';

const qs = new URLSearchParams(location.search);
const GV = window.GV = {
  opts: {}, handlers: {},
  on(ev, fn) { (this.handlers[ev] ||= []).push(fn); },
  emit(ev, data) { for (const fn of this.handlers[ev] || []) { try { fn(data); } catch (e) { console.warn('GV.' + ev, e); } } },
};
GV.votePage = qs.has('vote');

// ---------------------------------------------------------------------------------------------
// Inställningar
// ---------------------------------------------------------------------------------------------
const FEATURES = [
  ['bubbles', 'Faktabubblor', true],
  ['sign', 'Gatuskylt', true],
  ['weather', 'Tid och väder', true],
  ['phrase', 'Språkruta', true],
  ['stops', 'Vykortsstopp', true],
  ['voice', 'Guideröst', false],
  ['quiz', 'Gissa staden', false],
  ['votes', 'Rösta med mobilen', false],
];
const NOT_IN_X = new Set();   // tekniska val som inte styrs av x= i adressen
const DEFAULTS = { mode: 'route', tourMin: 10, epoch: 'any', lang: 'sv', home: '', homeName: '', sponsors: '' };
for (const [k, , on] of FEATURES) DEFAULTS[k] = on;

function loadOpts() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('gvOpts') || '{}'); } catch (e) { /* ignore */ }
  const o = { ...DEFAULTS, ...saved };
  // Adressen kan styra allt, så att en kioskskärm kan startas med en enda länk.
  if (qs.has('x')) { const on = new Set(qs.get('x').split(',')); for (const [k] of FEATURES) if (!NOT_IN_X.has(k)) o[k] = on.has(k); }
  for (const k of ['mode', 'epoch', 'lang', 'home', 'homeName', 'sponsors']) if (qs.has(k)) o[k] = qs.get(k);
  if (qs.has('tour')) o.tourMin = Math.max(1, parseFloat(qs.get('tour')) || DEFAULTS.tourMin);
  return o;
}
function saveOpts() { try { localStorage.setItem('gvOpts', JSON.stringify(GV.opts)); } catch (e) { /* ignore */ } }
GV.urlParams = () => {
  const o = GV.opts, p = { x: FEATURES.filter(([k]) => o[k] && !NOT_IN_X.has(k)).map(([k]) => k).join(',') };
  for (const k of ['mode', 'epoch', 'lang', 'home', 'homeName', 'sponsors']) if (o[k] && o[k] !== DEFAULTS[k]) p[k] = o[k];
  if (o.tourMin !== DEFAULTS.tourMin) p.tour = o.tourMin;
  return p;
};

// ---------------------------------------------------------------------------------------------
// Små hjälpare (huvudskriptets dist, bearing, signed, rad, deg och geocode används vid körning)
// ---------------------------------------------------------------------------------------------
const el = id => document.getElementById(id);
const wait = ms => new Promise(r => setTimeout(r, ms));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clampv = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const rnd = n => Array.from(crypto.getRandomValues(new Uint8Array(n)), b => 'abcdefghijklmnopqrstuvwxyz0123456789'[b % 36]).join('');
const flag = cc => cc && cc.length === 2 ? String.fromCodePoint(...cc.toUpperCase().split('').map(c => 127397 + c.charCodeAt(0))) : '';
const isPlaying = () => document.body.classList.contains('playing');
async function getJ(url, opt, tries = 3) {
  for (let i = 1; ; i++) {
    const r = await fetch(url, opt);
    if (r.ok) return r.json();
    // "För många anrop": vänta som servern ber om (eller allt längre) och försök igen
    if ((r.status === 429 || r.status === 503) && i < tries) {
      const ra = parseFloat(r.headers.get('Retry-After'));
      await wait((isFinite(ra) ? Math.min(ra, 20) : 2 * i * i) * 1000); continue;
    }
    throw new Error(new URL(url).host + ' svarade ' + r.status);
  }
}
// Wikimedia vill att program identifierar sig; webbläsare får inte ändra User-Agent, därför Api-User-Agent.
const WIKI = { headers: { 'Api-User-Agent': 'Gatuvy/0.3 (street-view travel player)' } };
// Nominatim tillåter högst ett anrop per sekund.
let nomLast = 0;
async function nominatimSlot() {
  const now = Date.now(), at = Math.max(now, nomLast + 1100);
  nomLast = at; if (at > now) await wait(at - now);
}
const walkMin = d => Math.max(1, Math.round(d * 1.3 / 80));   // 80 m/min, gator är inte fågelvägen

// ---------------------------------------------------------------------------------------------
// Data om länder: språkfraser, världsdel
// ---------------------------------------------------------------------------------------------
const PHRASES = {
  fr: ['Franska', [['Bonjour', 'Hej'], ['Merci', 'Tack'], ['Au revoir', 'Hej då'], ['S’il vous plaît', 'Tack (när man ber om något)'], ['Une baguette, s’il vous plaît', 'En baguette, tack'], ['C’est magnifique', 'Det är fantastiskt']]],
  de: ['Tyska', [['Guten Tag', 'God dag'], ['Danke', 'Tack'], ['Tschüss', 'Hej då'], ['Wie geht’s?', 'Hur mår du?'], ['Prost!', 'Skål!']]],
  at: ['Tyska', [['Grüß Gott', 'Hej'], ['Danke', 'Tack'], ['Servus', 'Hej / hej då']]],
  es: ['Spanska', [['Hola', 'Hej'], ['Gracias', 'Tack'], ['¿Qué tal?', 'Hur är läget?'], ['Hasta luego', 'Vi ses'], ['¡Qué bonito!', 'Så vackert!']]],
  mx: ['Spanska', [['Hola', 'Hej'], ['Gracias', 'Tack'], ['¿Qué onda?', 'Läget?'], ['¡Qué padre!', 'Vad häftigt!']]],
  ar: ['Spanska', [['Hola', 'Hej'], ['Gracias', 'Tack'], ['Che', 'Du (tilltal)'], ['¡Qué lindo!', 'Så fint!']]],
  it: ['Italienska', [['Ciao', 'Hej'], ['Grazie', 'Tack'], ['Buongiorno', 'God morgon'], ['Che bello!', 'Så vackert!']]],
  pt: ['Portugisiska', [['Olá', 'Hej'], ['Obrigado/a', 'Tack'], ['Bom dia', 'God morgon']]],
  br: ['Portugisiska', [['Oi', 'Hej'], ['Obrigado/a', 'Tack'], ['Tudo bem?', 'Allt bra?'], ['Que legal!', 'Vad kul!']]],
  nl: ['Nederländska', [['Hallo', 'Hej'], ['Dank je wel', 'Tack så mycket'], ['Gezellig', 'Mysigt']]],
  no: ['Norska', [['Hei', 'Hej'], ['Takk', 'Tack'], ['Kos', 'Mys'], ['Ha det bra', 'Ha det bra']]],
  dk: ['Danska', [['Hej', 'Hej'], ['Tak', 'Tack'], ['Hyggeligt', 'Mysigt'], ['Skål', 'Skål']]],
  fi: ['Finska', [['Moi', 'Hej'], ['Kiitos', 'Tack'], ['Sisu', 'Envishet och mod'], ['Kippis', 'Skål']]],
  se: ['Svenska', [['Fika', 'Kaffepaus med något gott'], ['Lagom', 'Precis lagom'], ['Allemansrätten', 'Rätten att röra sig fritt i naturen']]],
  gb: ['Engelska', [['Cheers', 'Tack / skål'], ['Brilliant', 'Toppen'], ['Fancy a cuppa?', 'Vill du ha en kopp te?']]],
  ie: ['Engelska / iriska', [['Sláinte', 'Skål'], ['Grand', 'Bra'], ['What’s the craic?', 'Läget?']]],
  us: ['Engelska', [['Howdy', 'Hej'], ['Awesome', 'Grymt'], ['Have a nice day', 'Ha en bra dag']]],
  ca: ['Engelska / franska', [['Hello / Bonjour', 'Hej'], ['Eh?', 'Eller hur?'], ['Merci', 'Tack']]],
  au: ['Engelska', [['G’day', 'Hej'], ['No worries', 'Ingen fara'], ['Arvo', 'Eftermiddag']]],
  nz: ['Engelska / maori', [['Kia ora', 'Hej'], ['Sweet as', 'Toppen']]],
  za: ['Zulu / afrikaans', [['Sawubona', 'Hej (zulu)'], ['Ngiyabonga', 'Tack (zulu)'], ['Dankie', 'Tack (afrikaans)'], ['Lekker', 'Gott / skönt']]],
  ke: ['Swahili', [['Jambo', 'Hej'], ['Asante', 'Tack'], ['Hakuna matata', 'Inga problem'], ['Karibu', 'Välkommen']]],
  tz: ['Swahili', [['Jambo', 'Hej'], ['Asante', 'Tack'], ['Karibu', 'Välkommen']]],
  ng: ['Engelska / yoruba', [['Bawo ni', 'Hej (yoruba)'], ['E se', 'Tack (yoruba)']]],
  ma: ['Arabiska / franska', [['Salam', 'Hej'], ['Shukran', 'Tack'], ['Merci', 'Tack (franska)']]],
  eg: ['Arabiska', [['Ahlan', 'Hej'], ['Shukran', 'Tack'], ['Yalla', 'Kom igen']]],
  ae: ['Arabiska', [['Marhaba', 'Hej'], ['Shukran', 'Tack'], ['Yalla', 'Kom igen']]],
  tr: ['Turkiska', [['Merhaba', 'Hej'], ['Teşekkürler', 'Tack'], ['Afiyet olsun', 'Smaklig måltid']]],
  gr: ['Grekiska', [['Yassou', 'Hej'], ['Efcharistó', 'Tack'], ['Opa!', 'Hoppsan / hurra!']]],
  pl: ['Polska', [['Cześć', 'Hej'], ['Dziękuję', 'Tack'], ['Na zdrowie', 'Skål']]],
  cz: ['Tjeckiska', [['Ahoj', 'Hej'], ['Děkuji', 'Tack'], ['Na zdraví', 'Skål']]],
  jp: ['Japanska', [['Konnichiwa', 'Hej'], ['Arigatō', 'Tack'], ['Sumimasen', 'Ursäkta'], ['Kanpai', 'Skål']]],
  kr: ['Koreanska', [['Annyeonghaseyo', 'Hej'], ['Kamsahamnida', 'Tack']]],
  cn: ['Kinesiska', [['Nǐ hǎo', 'Hej'], ['Xièxie', 'Tack']]],
  sg: ['Malajiska / kinesiska', [['Selamat pagi', 'God morgon (malajiska)'], ['Terima kasih', 'Tack (malajiska)'], ['Xièxie', 'Tack (kinesiska)'], ['Can lah', 'Det går bra (singlish)']]],
  th: ['Thailändska', [['Sawasdee', 'Hej'], ['Khop khun', 'Tack'], ['Mai pen rai', 'Ingen fara']]],
  in: ['Hindi', [['Namaste', 'Hej'], ['Dhanyavaad', 'Tack'], ['Accha', 'Okej']]],
  id: ['Indonesiska', [['Halo', 'Hej'], ['Terima kasih', 'Tack'], ['Selamat jalan', 'Trevlig resa']]],
  is: ['Isländska', [['Halló', 'Hej'], ['Takk', 'Tack'], ['Skál', 'Skål']]],
  ee: ['Estniska', [['Tere', 'Hej'], ['Aitäh', 'Tack']]],
};
const CONT = {};
const addCont = (name, list) => list.split(' ').forEach(cc => { CONT[cc] = name; });
addCont('Europa', 'se no dk fi is ee lv lt pl de nl be lu fr es pt it ch at cz sk hu si hr ba rs me mk al gr bg ro md ua by ru gb ie mt cy li mc ad sm va');
addCont('Asien', 'tr jp kr kp cn tw hk mo mn sg my id th vn la kh mm ph in pk bd lk np bt mv af ir iq sy lb jo il ps sa ae qa bh kw om ye kz uz tm kg tj ge am az');
addCont('Afrika', 'za na bw zw zm mz mw tz ke ug rw bi et so dj er sd ss eg ly tn dz ma mr ml ne td ng gh ci sn gm gn sl lr bf tg bj cm cf ga cg cd ao mg mu sc km cv st gq');
addCont('Nordamerika', 'us ca mx gt bz hn sv ni cr pa cu jm ht do bs bb tt pr');
addCont('Sydamerika', 'br ar cl uy py bo pe ec co ve gy sr');
addCont('Oceanien', 'au nz fj pg ws to vu sb');

// ---------------------------------------------------------------------------------------------
// Världen runt: städer med tillräckligt många gatubilder (24 städer testades, dessa 21 klarade minst 70 %).
// src 'px' = fungerar med bara Panoramax, 'mly' = kräver Mapillary-token.
// ---------------------------------------------------------------------------------------------
const WORLD = [
  // täckning med testrutten inom parentes (andel av rutten med bilder)
  { city: 'Paris', cc: 'fr', tz: 'Europe/Paris', src: 'px', from: 'Musée du Louvre, Paris', to: 'Arc de Triomphe, Paris' },              // 99 %, 360°
  { city: 'Lyon', cc: 'fr', tz: 'Europe/Paris', src: 'px', from: 'Place Bellecour, Lyon', to: 'Gare de Lyon-Part-Dieu' },                 // 100 %, 360°
  { city: 'Marseille', cc: 'fr', tz: 'Europe/Paris', src: 'px', from: 'Vieux-Port, Marseille', to: 'Notre-Dame de la Garde, Marseille' }, // 72 %, 360°
  { city: 'Amsterdam', cc: 'nl', tz: 'Europe/Amsterdam', src: 'px', from: 'Amsterdam Centraal', to: 'Museumplein, Amsterdam' },         // 100 %, 360°
  { city: 'Nice', cc: 'fr', tz: 'Europe/Paris', src: 'mly', from: 'Aéroport Nice Côte d\'Azur', to: 'Place Masséna, Nice' },              // 81 %
  { city: 'Barcelona', cc: 'es', tz: 'Europe/Madrid', src: 'mly', from: 'Plaça de Catalunya, Barcelona', to: 'Sagrada Família, Barcelona' }, // 80 %
  { city: 'London', cc: 'gb', tz: 'Europe/London', src: 'mly', from: 'Trafalgar Square, London', to: 'Buckingham Palace, London' },      // 89 %
  { city: 'Berlin', cc: 'de', tz: 'Europe/Berlin', src: 'mly', from: 'Potsdamer Platz, Berlin', to: 'Kurfürstendamm, Berlin' },          // 94 %, 360°
  { city: 'Stockholm', cc: 'se', tz: 'Europe/Stockholm', src: 'mly', from: 'Slussen, Stockholm', to: 'Odenplan, Stockholm' },             // 85 %
  { city: 'Oslo', cc: 'no', tz: 'Europe/Oslo', src: 'mly', from: 'Aker Brygge, Oslo', to: 'Grünerløkka, Oslo' },                          // 78 %
  { city: 'New York', cc: 'us', tz: 'America/New_York', src: 'mly', from: 'Times Square, New York', to: 'Columbus Circle, New York' },   // 100 %
  { city: 'Miami', cc: 'us', tz: 'America/New_York', src: 'mly', from: 'Bayfront Park, Miami', to: 'Brickell City Centre, Miami' },      // 72 %
  { city: 'Los Angeles', cc: 'us', tz: 'America/Los_Angeles', src: 'mly', from: 'Hollywood and Highland, Los Angeles', to: 'Sunset Boulevard and Vine Street, Los Angeles' }, // 80 %
  { city: 'Mexico City', cc: 'mx', tz: 'America/Mexico_City', src: 'mly', from: 'Zócalo, Mexico City', to: 'Ángel de la Independencia, Mexico City' }, // 83 %
  { city: 'Rio de Janeiro', cc: 'br', tz: 'America/Sao_Paulo', src: 'mly', from: 'Copacabana Palace, Rio de Janeiro', to: 'Ipanema, Rio de Janeiro' }, // 96 %
  { city: 'Johannesburg', cc: 'za', tz: 'Africa/Johannesburg', src: 'mly', from: 'Nelson Mandela Square, Sandton', to: 'Rosebank Mall, Johannesburg' }, // 90 %
  { city: 'Dubai', cc: 'ae', tz: 'Asia/Dubai', src: 'mly', from: 'Dubai Mall', to: 'Jumeirah Mosque, Dubai' },                            // 82 %
  { city: 'Tokyo', cc: 'jp', tz: 'Asia/Tokyo', src: 'mly', from: 'Tokyo Station', to: 'Ginza, Tokyo' },                                   // 81 %
  { city: 'Singapore', cc: 'sg', tz: 'Asia/Singapore', src: 'mly', from: 'Marina Bay Sands, Singapore', to: 'Orchard Road, Singapore' },  // 72 %
  { city: 'Sydney', cc: 'au', tz: 'Australia/Sydney', src: 'mly', from: 'Circular Quay, Sydney', to: 'Town Hall, Sydney' },              // 72 %
  { city: 'Melbourne', cc: 'au', tz: 'Australia/Melbourne', src: 'mly', from: 'Federation Square, Melbourne', to: 'St Kilda Beach, Melbourne' }, // 89 %
];

// ---------------------------------------------------------------------------------------------
// Läget för den pågående resan
// ---------------------------------------------------------------------------------------------
const T = {
  trip: 0, pois: [], routePts: [], place: {}, placeReady: null, wx: null, tz: null,
  view: null, stopsDone: new Set(), lastStopAt: -1e9, stopImp: 25, voiceImp: 15,
  spoken: new Set(), quizHiding: false, card: null, sign: null, bubbles: [], home: null, sponsors: null,
  revAt: null, revBusy: false, phraseIdx: 0, phraseNow: '', vote: null, tourCity: null,
};

// ---------------------------------------------------------------------------------------------
// Utseende: överlägg och panelens inställningar
// ---------------------------------------------------------------------------------------------
const CSS = `
#gv-layer { position: fixed; inset: 0; pointer-events: none; z-index: 2; overflow: hidden; }
.gv-b { position: absolute; left: 0; top: 0; transform: translate(-50%, -100%); max-width: 240px; padding: 6px 10px 7px; border-radius: 10px;
  background: rgba(14,14,18,.78); border: 1px solid rgba(255,255,255,.25); color: #fff; font-size: 13px; line-height: 1.25;
  box-shadow: 0 4px 14px rgba(0,0,0,.35); white-space: normal; will-change: transform, opacity; }
.gv-b::after { content: ''; position: absolute; left: 50%; bottom: -7px; margin-left: -7px; border: 7px solid transparent;
  border-bottom: 0; border-top-color: rgba(14,14,18,.78); }
.gv-b b { display: block; font-size: 14px; } .gv-b span { display: block; color: #cfd3dc; font-size: 12px; }
.gv-b i { display: block; font-style: normal; color: #9fe39f; font-size: 12px; margin-top: 2px; }
.gv-b.sp { border-color: #e7b416; background: rgba(40,30,4,.86); } .gv-b.sp::after { border-top-color: rgba(40,30,4,.86); }
.gv-b.sp em { display: inline-block; font-style: normal; font-size: 10px; letter-spacing: .06em; text-transform: uppercase;
  background: #e7b416; color: #000; border-radius: 4px; padding: 0 5px; margin-bottom: 3px; }
#gv-sign { position: fixed; top: calc(14px + var(--st)); left: 50%; transform: translateX(-50%); z-index: 3; text-align: center;
  background: #1b4fa8; color: #fff; border: 2px solid #fff; border-radius: 8px; padding: 5px 14px 6px; min-width: 160px;
  box-shadow: 0 3px 12px rgba(0,0,0,.4); display: none; max-width: calc(100vw - 140px); }
#gv-sign b { display: block; font-size: 17px; letter-spacing: .02em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#gv-sign span { display: block; font-size: 11px; opacity: .85; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#gv-wx { position: fixed; top: calc(68px + var(--st)); right: calc(14px + var(--sr)); z-index: 3; text-align: right;
  background: rgba(0,0,0,.5); border-radius: 10px; padding: 6px 10px; font-size: 13px; color: #fff; display: none; }
#gv-wx b { font-size: 20px; font-weight: 600; }
#gv-phrase { position: fixed; left: 50%; bottom: calc(44px + var(--sb)); transform: translate(-50%, 20px); z-index: 3;
  background: rgba(14,14,18,.85); border: 1px solid #444; border-radius: 20px; padding: 7px 14px; font-size: 14px; color: #fff;
  opacity: 0; transition: opacity .6s, transform .6s; max-width: calc(100vw - 40px); text-align: center; }
#gv-phrase.show { opacity: 1; transform: translate(-50%, 0); }
#gv-card { position: fixed; right: calc(18px + var(--sr)); top: 50%; transform: translate(30px, -50%); z-index: 4; width: min(360px, calc(100vw - 36px));
  background: rgba(14,14,18,.92); border: 1px solid #555; border-radius: 14px; overflow: hidden; color: #fff;
  opacity: 0; transition: opacity .6s, transform .6s; box-shadow: 0 8px 30px rgba(0,0,0,.5); }
#gv-card.show { opacity: 1; transform: translate(0, -50%); }
#gv-card img { width: 100%; max-height: 180px; object-fit: cover; display: block; }
#gv-card .in { padding: 12px 14px 14px; } #gv-card small { color: #e7b416; text-transform: uppercase; letter-spacing: .08em; font-size: 10px; }
#gv-card h3 { margin: 2px 0 6px; font-size: 19px; } #gv-card p { margin: 0; font-size: 13px; color: #d6d9e0; line-height: 1.4; }
#gv-card .ft { display: flex; align-items: center; gap: 10px; margin-top: 10px; } #gv-card .ft i { font-style: normal; color: #9fe39f; font-size: 13px; flex: 1; }
.gv-qr { background: #fff; padding: 5px; border-radius: 6px; line-height: 0; } .gv-qr img, .gv-qr canvas { display: block; }
#gv-quiz { position: fixed; left: 50%; top: 28%; transform: translate(-50%, -50%) scale(.95); z-index: 4; text-align: center;
  background: rgba(14,14,18,.9); border: 1px solid #666; border-radius: 16px; padding: 18px 26px; color: #fff; min-width: 280px;
  max-width: calc(100vw - 40px); opacity: 0; transition: opacity .5s, transform .5s; }
#gv-quiz.show { opacity: 1; transform: translate(-50%, -50%) scale(1); }
#gv-quiz h2 { margin: 0 0 8px; font-size: 26px; } #gv-quiz p { margin: 4px 0; font-size: 16px; color: #dfe2ea; }
#gv-quiz .big { font-size: 34px; font-weight: 700; color: #fff; }
body.gv-hide #place, body.gv-hide #mini, body.gv-hide #gv-sign, body.gv-hide #gv-layer, body.gv-hide #gv-phrase { visibility: hidden; }
#gv-vote { position: fixed; right: calc(14px + var(--sr)); bottom: calc(40px + var(--sb)); z-index: 3; display: none;
  background: rgba(14,14,18,.88); border: 1px solid #555; border-radius: 12px; padding: 10px; color: #fff; font-size: 12px; width: 170px; }
#gv-vote b { display: block; font-size: 13px; margin-bottom: 6px; } #gv-vote .gv-qr { width: fit-content; margin: 0 auto 6px; }
#gv-vote div.t { display: flex; justify-content: space-between; }
#gv-tour { position: fixed; inset: 0; z-index: 4; display: none; align-items: center; justify-content: center; flex-direction: column;
  background: radial-gradient(circle, rgba(10,10,14,.75), rgba(0,0,0,.92)); color: #fff; text-align: center; }
#gv-tour.show { display: flex; } #gv-tour small { letter-spacing: .2em; text-transform: uppercase; color: #aab; }
#gv-tour h2 { font-size: 46px; margin: 6px 0; } #gv-tour p { font-size: 18px; color: #ccd; margin: 0; }
#gv-x summary { cursor: pointer; margin-top: 12px; font-weight: 600; }
#gv-x .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; margin-top: 6px; }
#gv-x .grid label { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 13px; color: #ddd; cursor: pointer; }
#gv-x input[type=checkbox] { width: 16px; height: 16px; margin: 0; padding: 0; flex: none; }
#gv-x .note { font-size: 11px; color: #888; margin-top: 4px; }
@media (max-width: 600px) { #gv-wx { top: calc(64px + var(--st)); } .gv-b { max-width: 170px; font-size: 12px; } #gv-card img { max-height: 120px; } }
`;

function buildOverlays() {
  const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
  const add = (id, cls) => { const d = document.createElement('div'); d.id = id; if (cls) d.className = cls; document.body.appendChild(d); return d; };
  add('gv-layer', 'playing-only'); add('gv-sign'); add('gv-wx'); add('gv-phrase'); add('gv-card'); add('gv-quiz'); add('gv-vote'); add('gv-tour');
}

function buildSettings() {
  const box = document.createElement('details'); box.id = 'gv-x';
  box.innerHTML = `<summary>Upplevelse</summary>
    <div class="grid">${FEATURES.map(([k, label]) => `<label><input type="checkbox" data-k="${k}"> ${label}</label>`).join('')}</div>
    <div class="row">
      <div><label>Läge</label><select id="gv-mode"><option value="route">En rutt</option><option value="world">Världen runt</option></select></div>
      <div><label>Byt stad efter</label><select id="gv-tourMin">${[2, 5, 10, 20, 30, 60].map(m => `<option value="${m}">${m} min</option>`).join('')}</select></div>
    </div>
    <div class="row">
      <div><label>Bilder från</label><select id="gv-epoch"><option value="any">Blandat</option><option value="newest">Nyaste</option>
        <option value="oldest">Äldsta</option><option value="summer">Sommar</option><option value="winter">Vinter</option></select></div>
      <div><label>Faktaspråk</label><select id="gv-lang"><option value="sv">Svenska</option><option value="en">English</option></select></div>
    </div>
    <label>Hemmabas, t.ex. hotellet (namn och adress)</label>
    <div class="row"><input id="gv-homeName" placeholder="Hotell Plaza"><input id="gv-home" placeholder="Gata 1, Stad"></div>
    <label>Tips och annonser (länk till JSON-fil)</label>
    <input id="gv-sponsors" placeholder="sponsors.json">
    <div class="note">Världen runt byter stad automatiskt bland städer där det är dag just nu. Rösta med mobilen visar en
      QR-kod där tittarna väljer nästa stad.</div>`;
  el('go').parentNode.insertBefore(box, el('go'));
  for (const cb of box.querySelectorAll('input[type=checkbox]')) {
    cb.checked = !!GV.opts[cb.dataset.k];
    cb.onchange = () => { GV.opts[cb.dataset.k] = cb.checked; saveOpts(); applyLive(cb.dataset.k); };
  }
  for (const k of ['mode', 'tourMin', 'epoch', 'lang', 'homeName', 'home', 'sponsors']) {
    const i = el('gv-' + k); i.value = GV.opts[k];
    i.onchange = () => { GV.opts[k] = k === 'tourMin' ? parseFloat(i.value) : i.value.trim(); saveOpts();
      if (k === 'home' || k === 'homeName') { T.home = null; loadHome(); }
      if (k === 'sponsors') T.sponsors = null; };
  }
}

// Sådant som kan slås av och på mitt under resan
function applyLive(k) {
  if (k === 'sign') renderSign();
  if (k === 'weather') renderWx();
  if (k === 'bubbles' && !GV.opts.bubbles) clearBubbles();
  if (k === 'voice' && !GV.opts.voice && window.speechSynthesis) speechSynthesis.cancel();
  if (k === 'votes') { if (!GV.opts.votes) closeVote(); }
}

// ---------------------------------------------------------------------------------------------
// Faktapunkter längs rutten: Wikipedia (geosök + inledning + bild) och Wikidata (hur känd platsen är)
// ---------------------------------------------------------------------------------------------
// Sådant som inte är en plats man ser: gator, områden, städer, tunnelbanestationer och historiska händelser
const AREA_WORDS = /\b(gata|gatan|väg|vägen|aveny|street|road|avenue|boulevard|rue|stadsdel|district|arrondissement|kommun|municipality|neighbourhood|neighborhood|suburb|förort|huvudstad|capital|city in|stad i|commune|county|län|province|region)\b/i;
const NOT_A_SIGHT = /(metro|métro|tunnelbana|subway|u-bahn|s-bahn|\b(republic|republik|war|krig|battle|slaget|revolution|liberation|befrielse|treaty|fördrag|massacre|attack|siege|belägring|riot|upplopp|election|dynasty|dynasti|empire|imperium|kingdom|kungarike|event|händelse|period|epok|era|protest|uprising|uppror)\b)/i;
const cleanTitle = t => t.replace(/\s*\([^)]*\)\s*$/, '');

function minDistToRoute(p) {
  let best = 1e9;
  for (const q of T.routePts) { const d = dist(p, q); if (d < best) best = d; }
  return best;
}

async function loadPOIs(coords, trip) {
  // Få och stora anrop: en geosökning per ~1,2 km rutt (radie 900 m), detaljer 50 sidor åt gången,
  // och längre text och bild bara för de platser som kan bli vykortsstopp.
  const pts = [coords[0]]; let acc = 0;
  for (let i = 1; i < coords.length; i++) { acc += dist(coords[i - 1], coords[i]); if (acc >= 1200) { pts.push(coords[i]); acc = 0; } }
  if (dist(pts[pts.length - 1], coords[coords.length - 1]) > 400) pts.push(coords[coords.length - 1]);
  const langs = GV.opts.lang === 'en' ? ['en'] : ['sv', 'en'];
  const api = lang => `https://${lang}.wikipedia.org/w/api.php?format=json&formatversion=2&origin=*&`;
  const pages = new Map();
  for (const lang of langs) {
    for (const p of pts) {
      if (trip !== T.trip) return;
      try {
        const r = await getJ(api(lang) + `action=query&list=geosearch&gscoord=${p[1].toFixed(6)}%7C${p[0].toFixed(6)}&gsradius=900&gslimit=300`, WIKI);
        for (const g of r.query?.geosearch || []) pages.set(lang + ':' + g.pageid, { lang, pageid: g.pageid, title: g.title, p: [g.lon, g.lat] });
      } catch (e) { console.warn(e); }
      await wait(300);
    }
  }
  // Bara sådant som faktiskt syns från vägen
  const near = [...pages.values()].filter(x => minDistToRoute(x.p) <= 160);
  for (const lang of langs) {
    const list = near.filter(x => x.lang === lang);
    for (let i = 0; i < list.length; i += 50) {
      if (trip !== T.trip) return;
      const part = list.slice(i, i + 50);
      try {
        const r = await getJ(api(lang) + `action=query&prop=pageprops%7Cdescription&ppprop=wikibase_item&pageids=${part.map(x => x.pageid).join('%7C')}`, WIKI);
        for (const pg of r.query?.pages || []) {
          const x = part.find(y => y.pageid === pg.pageid); if (!x) continue;
          x.desc = pg.description || ''; x.q = pg.pageprops?.wikibase_item || null;
        }
      } catch (e) { console.warn(e); }
      await wait(300);
    }
  }
  // Slå ihop samma plats på olika språk; första språket (svenska) vinner om det finns
  const byQ = new Map();
  for (const x of near) {
    const k = x.q || x.lang + ':' + x.pageid, prev = byQ.get(k);
    if (!prev || (prev.lang !== langs[0] && x.lang === langs[0])) byQ.set(k, x);
  }
  // Hur känd är platsen? Antal språk som har en Wikipedia-artikel om den.
  const qids = [...byQ.keys()].filter(k => /^Q\d+$/.test(k)), imp = new Map();
  for (let i = 0; i < qids.length; i += 50) {
    if (trip !== T.trip) return;
    try {
      const r = await getJ(`https://www.wikidata.org/w/api.php?action=wbgetentities&props=sitelinks&ids=${qids.slice(i, i + 50).join('%7C')}&format=json&origin=*`, WIKI);
      for (const [id, e] of Object.entries(r.entities || {})) imp.set(id, Object.keys(e.sitelinks || {}).filter(s => /wiki$/.test(s) && !/^(commons|species|meta|wikidata|abstract)wiki$/.test(s)).length);
    } catch (e) { console.warn(e); }
    await wait(300);
  }
  const pois = [];
  for (const [k, x] of byQ) {
    if (AREA_WORDS.test(x.desc || '') || AREA_WORDS.test(x.title) || NOT_A_SIGHT.test(x.desc || '') || NOT_A_SIGHT.test(x.title)) continue;
    pois.push({ id: k, title: cleanTitle(x.title), desc: x.desc || '', extract: '', thumb: null, url: `https://${x.lang}.wikipedia.org/?curid=${x.pageid}`,
      pageid: x.pageid, p: x.p, lang: x.lang, imp: imp.get(k) || 1, sponsor: false });
  }
  if (trip !== T.trip) return;
  // Vykortsstopp vid de mest kända platserna längs just den här rutten; rösten tar lite fler
  const imps = pois.map(p => p.imp).sort((a, b) => b - a), at = q => imps[Math.min(imps.length - 1, Math.floor(imps.length * q))] || 0;
  T.stopImp = Math.max(20, at(0.12)); T.voiceImp = Math.max(10, at(0.3));
  T.pois = T.pois.filter(p => p.sponsor).concat(pois);
  planStops();
  // Inledning och bild till vykortskorten (högst 20 sidor per språk och anrop)
  for (const lang of langs) {
    const top = pois.filter(p => p.lang === lang && p.imp >= T.stopImp).slice(0, 20);
    if (!top.length) continue;
    try {
      const r = await getJ(api(lang) + `action=query&prop=extracts%7Cpageimages%7Cinfo&exintro=1&explaintext=1&exsentences=2&exlimit=20`
        + `&piprop=thumbnail&pithumbsize=480&inprop=url&pageids=${top.map(p => p.pageid).join('%7C')}`, WIKI);
      for (const pg of r.query?.pages || []) {
        const x = top.find(p => p.pageid === pg.pageid); if (!x) continue;
        // ta bort uttal och översättningar inom parentes, behåll resten av meningen
        x.extract = (pg.extract || '').replace(/\s*\([^()]*[:;][^()]*\)/g, '').replace(/\s+,/g, ',').replace(/\s+/g, ' ').trim();
        x.thumb = pg.thumbnail?.source || null; if (pg.fullurl) x.url = pg.fullurl;
      }
    } catch (e) { console.warn(e); }
  }
}

async function loadSponsors(trip) {
  if (T.sponsors === null) {
    T.sponsors = [];
    const url = GV.opts.sponsors || 'sponsors.json';
    try {
      const list = await getJ(new URL(url, location.href).href);
      for (const [i, s] of (Array.isArray(list) ? list : []).entries()) {
        const lat = parseFloat(s.lat), lon = parseFloat(s.lon);
        if (!s.name || !isFinite(lat) || !isFinite(lon)) continue;
        T.sponsors.push({ id: 'sp:' + i, title: String(s.name), desc: String(s.text || ''), extract: String(s.text || ''),
          url: /^https?:\/\//.test(s.url || '') ? s.url : null, thumb: /^https?:\/\//.test(s.image || '') ? s.image : null,
          p: [lon, lat], imp: 1000, sponsor: true });
      }
    } catch (e) { /* ingen fil = inga tips */ }
  }
  if (trip !== T.trip) return;
  const mine = T.sponsors.filter(s => minDistToRoute(s.p) <= 300);
  T.pois = T.pois.filter(p => !p.sponsor).concat(mine);
  planStops();
}

async function loadHome() {
  if (!GV.opts.home) { T.home = null; return; }
  const key = 'gvHome:' + GV.opts.home;
  try { const c = JSON.parse(localStorage.getItem(key) || 'null'); if (c) { T.home = { p: c, name: GV.opts.homeName || 'hemmabasen' }; return; } } catch (e) { /* ignore */ }
  try {
    await nominatimSlot();
    const p = await geocode(GV.opts.home);
    T.home = { p, name: GV.opts.homeName || 'hemmabasen' };
    try { localStorage.setItem(key, JSON.stringify(p)); } catch (e) { /* ignore */ }
  } catch (e) { console.warn('Hemmabas', e); }
}
const homeLine = p => {
  if (!T.home) return '';
  const d = dist(T.home.p, p.p);
  return d <= 3000 ? `🚶 ${walkMin(d)} min från ${T.home.name}` : '';
};

// ---------------------------------------------------------------------------------------------
// Faktabubblor som sitter fast i bilden
// ---------------------------------------------------------------------------------------------
// Varje bubbla har ett eget läge som glider mot målet (ingen bubbla hoppar), tonas in och ut med
// avståndet och vid bildkanten, och de som redan syns får företräde så att de inte byts i onödan.
const bubbleEls = new Map();
function clearBubbles() { for (const b of bubbleEls.values()) b.el.remove(); bubbleEls.clear(); T.bubbles = []; }

function placeBubbles(v) {
  const layer = el('gv-layer'); if (!layer) return;
  const now = performance.now(), dt = Math.min(0.1, Math.max(0.001, (now - (T.bubT || now - 16)) / 1000)); T.bubT = now;
  const kPos = 1 - Math.exp(-dt / 0.22), kOp = 1 - Math.exp(-dt / 0.35);
  const Wd = innerWidth, Hd = innerHeight, half = v.hfov / 2, tanHalf = Math.tan(rad(half));
  const cand = [];
  for (const p of T.pois) {
    if (T.card && T.card.id === p.id) continue;                 // visas redan som vykort
    const d = dist(v.pos, p.p);
    const maxD = p.sponsor ? 260 : p.imp >= T.stopImp ? 380 : 170;
    if (d > maxD || d < 10) continue;
    const a = signed(bearing(v.pos, p.p), v.W);
    if (Math.abs(a) > half - 2) continue;
    const shown = bubbleEls.get(p.id);
    const sc = (p.sponsor ? 400 : 15 + p.imp) / (d + 40) * (shown && !shown.dead ? 1.8 : 1);   // företräde för de som syns
    cand.push({ p, d, a, maxD, sc });
  }
  cand.sort((x, y) => y.sc - x.sc);
  const show = cand.slice(0, Wd < 600 ? 2 : 3), placed = [];
  // Bubblan hänger ovanför sin punkt; punkten hålls så lågt att bubblan inte täcker skylten eller vädret
  const minY = Math.max(190, Hd * 0.3);
  for (const c of show) {
    c.x = Wd * (0.5 + Math.tan(rad(c.a)) / (2 * tanHalf));
    c.y = Math.max(minY, Hd * 0.46 - Hd * clampv(22 / c.d, 0.04, 0.24));
    for (const o of placed) if (Math.abs(o.x - c.x) < 190 && Math.abs(o.y - c.y) < 64) c.y = o.y + 72;   // undvik överlapp
    if (c.y > Hd * 0.75) continue;
    placed.push(c);
  }
  const keep = new Set();
  for (const c of placed) {
    const p = c.p; keep.add(p.id);
    let b = bubbleEls.get(p.id);
    if (!b) {
      const d = document.createElement('div'); d.className = 'gv-b' + (p.sponsor ? ' sp' : ''); d.style.opacity = 0;
      const desc = p.sponsor ? p.desc : (p.desc || '').replace(/^./, s => s.toUpperCase());
      d.innerHTML = (p.sponsor ? '<em>Tips</em>' : '') + `<b>${esc(p.title)}</b>` + (desc ? `<span>${esc(desc)}</span>` : '')
        + (homeLine(p) ? `<i>${esc(homeLine(p))}</i>` : '');
      layer.appendChild(d);
      b = { el: d, x: c.x, y: c.y + 12, o: 0, s: 0.9, p };
      bubbleEls.set(p.id, b);
      maybeSpeakBubble(c);
    }
    const edge = Math.abs(c.a) / half, near = clampv((c.maxD - c.d) / (0.25 * c.maxD), 0, 1);
    b.tx = c.x; b.ty = c.y; b.to = (1 - Math.pow(edge, 6)) * near; b.ts = clampv(1.18 - c.d / 420, 0.78, 1.12); b.dead = false;
  }
  for (const [id, b] of bubbleEls) {
    if (!keep.has(id)) { b.dead = true; b.to = 0; }
    b.x += (b.tx - b.x) * kPos; b.y += (b.ty - b.y) * kPos; b.s += (b.ts - b.s) * kPos; b.o += (b.to - b.o) * kOp;
    if (b.dead && b.o < 0.02) { b.el.remove(); bubbleEls.delete(id); continue; }
    b.el.style.transform = `translate(${b.x.toFixed(1)}px, ${b.y.toFixed(1)}px) translate(-50%, -100%) scale(${b.s.toFixed(3)})`;
    b.el.style.opacity = b.o.toFixed(3);
  }
  T.bubbles = [...bubbleEls.values()].filter(b => b.o > 0.05).map(b => ({ title: b.p.title, desc: b.p.desc, sp: b.p.sponsor, xf: b.x / Wd, yf: b.y / Hd, o: b.o }));
}

// ---------------------------------------------------------------------------------------------
// Guideröst
// ---------------------------------------------------------------------------------------------
function speak(text, lang) {
  if (!GV.opts.voice || !window.speechSynthesis || !text) return false;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang === 'en' ? 'en-US' : 'sv-SE';
  const v = speechSynthesis.getVoices().find(x => x.lang && x.lang.toLowerCase().startsWith(u.lang.slice(0, 2).toLowerCase()));
  if (v) u.voice = v;
  u.rate = 1;
  const a = el('audio'), vol = a.volume;
  u.onstart = () => { a.volume = Math.min(vol, 0.2); };
  u.onend = u.onerror = () => { a.volume = vol || 0.6; };
  speechSynthesis.speak(u);
  return true;
}
function maybeSpeakBubble(c) {
  const p = c.p;
  if (!GV.opts.voice || !window.speechSynthesis || T.quizHiding || T.spoken.has(p.id) || speechSynthesis.speaking || speechSynthesis.pending) return;
  if (!p.sponsor && p.imp < T.voiceImp) return;
  const en = p.lang === 'en' && GV.opts.lang === 'en';
  const side = c.a < -15 ? (en ? 'On your left' : 'Till vänster') : c.a > 15 ? (en ? 'On your right' : 'Till höger') : (en ? 'Straight ahead' : 'Rakt fram');
  T.spoken.add(p.id);
  speak(`${side}: ${p.title}. ${p.desc || ''}`, en ? 'en' : 'sv');
}

// ---------------------------------------------------------------------------------------------
// Gatuskylt, plats, väder och klocka
// ---------------------------------------------------------------------------------------------
async function reverse(p) {
  await nominatimSlot();
  const r = await getJ(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=17&addressdetails=1`
    + `&lat=${p[1].toFixed(6)}&lon=${p[0].toFixed(6)}&accept-language=${GV.opts.lang === 'en' ? 'en' : 'sv,en'}`);
  return r.address || {};
}
const cityOf = a => a.city || a.town || a.village || a.municipality || a.county || '';
async function updateSign(p, trip) {
  if (T.revBusy || (T.revAt && dist(p, T.revAt) < 90)) return;
  T.revBusy = true; T.revAt = p;
  try {
    const a = await reverse(p);
    if (trip !== T.trip) return;
    const road = a.road || a.pedestrian || a.square || a.footway || a.cycleway || a.path || '';
    const area = a.neighbourhood || a.quarter || a.suburb || a.city_district || '';
    T.sign = road ? { road, sub: [area, cityOf(a)].filter(Boolean).filter((s, i, l) => l.indexOf(s) === i).join(' · ') } : null;
    if (!T.place.cc && a.country_code) T.place = { city: cityOf(a), country: a.country || '', cc: a.country_code };
    renderSign();
  } catch (e) { console.warn(e); } finally { T.revBusy = false; }
}
function renderSign() {
  const s = el('gv-sign'); if (!s) return;
  if (!GV.opts.sign || !T.sign || !isPlaying()) { s.style.display = 'none'; return; }
  const html = `<b>${esc(T.sign.road)}</b>${T.sign.sub ? `<span>${esc(T.sign.sub)}</span>` : ''}`;
  if (s.innerHTML !== html) s.innerHTML = html;
  s.style.display = 'block';
}

const WMO = [[0, 'Klart', '☀️', '🌙'], [1, 'Mest klart', '🌤️', '🌙'], [2, 'Halvklart', '⛅', '☁️'], [3, 'Mulet', '☁️', '☁️'],
  [45, 'Dimma', '🌫️', '🌫️'], [48, 'Dimma', '🌫️', '🌫️'], [51, 'Duggregn', '🌦️', '🌧️'], [56, 'Underkylt regn', '🌧️', '🌧️'],
  [61, 'Regn', '🌧️', '🌧️'], [66, 'Underkylt regn', '🌧️', '🌧️'], [71, 'Snö', '🌨️', '🌨️'], [77, 'Snö', '🌨️', '🌨️'],
  [80, 'Regnskurar', '🌦️', '🌧️'], [85, 'Snöbyar', '🌨️', '🌨️'], [95, 'Åska', '⛈️', '⛈️']];
const wmo = (code, day) => { let r = WMO[0]; for (const w of WMO) if (code >= w[0]) r = w; return { text: r[1], icon: day ? r[2] : r[3] }; };
const metCode = s => /thunder/.test(s) ? 95 : /snow/.test(s) ? (/showers/.test(s) ? 85 : 71) : /sleet/.test(s) ? 66 : /rain/.test(s) ? (/showers/.test(s) ? 80 : 61)
  : /fog/.test(s) ? 45 : /cloudy/.test(s) && !/partly/.test(s) ? 3 : /partlycloudy/.test(s) ? 2 : /fair/.test(s) ? 1 : 0;

async function loadWeather(p, trip, tzHint) {
  let w = null;
  try {
    const r = await getJ(`https://api.open-meteo.com/v1/forecast?latitude=${p[1].toFixed(3)}&longitude=${p[0].toFixed(3)}&current=temperature_2m,weather_code,is_day&timezone=auto`, undefined, 1);
    w = { temp: r.current.temperature_2m, code: r.current.weather_code, day: !!r.current.is_day, tz: r.timezone };
  } catch (e) { console.warn('Open-Meteo', e); }
  if (!w) {
    try {   // reserv: Meteorologisk institutt (Norge)
      const r = await getJ(`https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=${p[1].toFixed(3)}&lon=${p[0].toFixed(3)}`);
      const ts = r.properties.timeseries[0], sym = ts.data.next_1_hours?.summary?.symbol_code || '';
      w = { temp: ts.data.instant.details.air_temperature, code: metCode(sym), day: !/night/.test(sym), tz: null };
    } catch (e) { console.warn('met.no', e); }
  }
  let tz = w?.tz || tzHint || null;
  if (!tz) { try { tz = (await getJ(`https://timeapi.io/api/timezone/coordinate?latitude=${p[1].toFixed(4)}&longitude=${p[0].toFixed(4)}`)).timeZone; } catch (e) { console.warn(e); } }
  if (trip !== T.trip) return;
  T.wx = w; T.tz = tz; renderWx();
}
const localTime = () => { try { return new Intl.DateTimeFormat('sv-SE', { hour: '2-digit', minute: '2-digit', timeZone: T.tz || undefined }).format(new Date()); } catch (e) { return ''; } };
function renderWx() {
  const b = el('gv-wx'); if (!b) return;
  if (!GV.opts.weather || !isPlaying() || (!T.wx && !T.tz)) { b.style.display = 'none'; return; }
  const city = T.quizHiding ? '' : (T.place.city || '');
  const w = T.wx ? wmo(T.wx.code, T.wx.day) : null;
  b.innerHTML = `${city ? esc(city) + ' · ' : ''}<b>${esc(localTime())}</b>` + (w ? `<br>${Math.round(T.wx.temp)}° ${w.icon} ${esc(w.text)}` : '');
  b.style.display = 'block';
}
setInterval(renderWx, 15000);

// ---------------------------------------------------------------------------------------------
// Språkruta
// ---------------------------------------------------------------------------------------------
function currentPhrase() {
  const ph = PHRASES[T.place.cc]; if (!ph) return null;
  const [lang, list] = ph, [a, b] = list[T.phraseIdx % list.length];
  return { lang, a, b };
}
async function phraseLoop() {
  for (;;) {
    await wait(9000);
    if (!GV.opts.phrase || !isPlaying() || T.quizHiding || T.card) continue;
    const ph = currentPhrase(); if (!ph) continue;
    const box = el('gv-phrase');
    T.phraseNow = `${flag(T.place.cc)} ${ph.lang}: «${ph.a}» = ${ph.b}`;
    box.textContent = T.phraseNow; box.classList.add('show');
    await wait(11000);
    box.classList.remove('show'); T.phraseNow = ''; T.phraseIdx++;
    await wait(25000);
  }
}

// ---------------------------------------------------------------------------------------------
// QR-koder (laddar ett litet bibliotek först när det behövs)
// ---------------------------------------------------------------------------------------------
let qrLib = null;
function loadQR() {
  return qrLib ||= new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
    s.onload = () => res(window.QRCode); s.onerror = () => { qrLib = null; rej(new Error('QR-biblioteket gick inte att ladda')); };
    document.head.appendChild(s);
  });
}
async function renderQR(box, text, size) {
  box.innerHTML = '';
  try { const Q = await loadQR(); new Q(box, { text, width: size, height: size, correctLevel: Q.CorrectLevel.M }); }
  catch (e) { console.warn(e); box.remove(); }
}
const qrCanvas = box => box && box.querySelector('canvas');

// ---------------------------------------------------------------------------------------------
// Vykortsstopp: resan stannar vid kända platser, vrider blicken dit och visar ett faktakort
// ---------------------------------------------------------------------------------------------
// Stoppen planeras när faktapunkterna är hämtade: varje känd plats får ett stopp där rutten passerar
// närmast (högst 170 m bort), de mest kända först och med minst ~150 m mellan stoppen.
function planStops() {
  T.stopAt = new Map();
  const fr = T.frames; if (!fr || !fr.length) return;
  const cands = T.pois.filter(p => p.sponsor || p.imp >= T.stopImp).sort((a, b) => (b.sponsor ? 1e4 : b.imp) - (a.sponsor ? 1e4 : a.imp));
  const taken = [];
  for (const p of cands) {
    let bi = -1, bd = 1e9;
    for (let i = 0; i < fr.length; i++) { const d = dist(fr[i].p, p.p); if (d < bd) { bd = d; bi = i; } }
    if (bi < 0 || bd > (p.sponsor ? 120 : 170)) continue;
    // stanna lite innan platsen passeras, så att den ligger snett framåt
    let si = bi; while (si > 0 && fr[bi].d - fr[si].d < Math.min(40, bd * 0.6)) si--;
    if (taken.some(t => Math.abs(fr[t].d - fr[si].d) < 150)) continue;
    taken.push(si); T.stopAt.set(si, p);
    if (taken.length >= 12) break;
  }
}
GV.hold = (f, idx) => {
  if (!GV.opts.stops || T.quizHiding || !T.stopAt) return null;
  const p = T.stopAt.get(idx);
  if (!p || performance.now() - T.lastStopAt < 15000) return null;
  T.lastStopAt = performance.now();
  showCard(p);
  return { ms: 9000, look: bearing(f.im.p, p.p) };
};

function showCard(p) {
  const c = el('gv-card'), hl = homeLine(p);
  const text = p.extract || p.desc || '';
  c.innerHTML = (p.thumb ? `<img src="${esc(p.thumb)}" alt="">` : '')
    + `<div class="in"><small>${p.sponsor ? 'Tips' : 'Vykortsstopp'}</small><h3>${esc(p.title)}</h3><p>${esc(text)}</p>`
    + `<div class="ft"><i>${esc(hl)}</i><div class="gv-qr"></div></div></div>`;
  const qrBox = c.querySelector('.gv-qr');
  // QR: vägbeskrivning från hemmabasen om den finns, annars mer läsning
  const target = T.home && dist(T.home.p, p.p) <= 3000
    ? `https://www.google.com/maps/dir/?api=1&origin=${T.home.p[1]},${T.home.p[0]}&destination=${p.p[1]},${p.p[0]}&travelmode=walking`
    : p.url;
  if (target) renderQR(qrBox, target, 84); else qrBox.remove();
  if (hl && target) c.querySelector('.ft i').textContent = hl + ' – skanna för vägen dit';
  T.card = { id: p.id, title: p.title, text, sub: p.sponsor ? 'Tips' : 'Vykortsstopp', qr: qrBox };
  requestAnimationFrame(() => c.classList.add('show'));
  el('gv-phrase').classList.remove('show');
  if (GV.opts.voice && window.speechSynthesis) { speechSynthesis.cancel(); speak(`${p.title}. ${text}`, p.lang === 'en' ? 'en' : 'sv'); }
}
function hideCard() { el('gv-card').classList.remove('show'); T.card = null; }
GV.on('holdend', hideCard);

// ---------------------------------------------------------------------------------------------
// Gissa staden
// ---------------------------------------------------------------------------------------------
function quizCard(html) { const q = el('gv-quiz'); q.innerHTML = html; q.classList.add('show'); }
async function runQuiz(trip, startPos) {
  T.quizHiding = true; document.body.classList.add('gv-hide'); clearBubbles(); renderWx();
  const alive = () => trip === T.trip && isPlaying();
  quizCard('<h2>Gissa staden!</h2><p>Var är vi någonstans? Ledtrådar kommer…</p>');
  await Promise.race([T.placeReady, wait(8000)]);
  const cc = T.place.cc, clues = [];
  const cont = CONT[cc] || (startPos[1] < -10 ? 'södra halvklotet' : 'norra halvklotet');
  clues.push(`Ledtråd 1: världsdel – <b>${esc(cont)}</b>`);
  const steps = [
    () => clues,
    () => { const w = T.wx ? `${Math.round(T.wx.temp)}° och ${wmo(T.wx.code, T.wx.day).text.toLowerCase()}` : '';
      clues.push(`Ledtråd 2: klockan är <b>${esc(localTime())}</b> där${w ? ', ' + esc(w) : ''}`); return clues; },
    () => { const ph = currentPhrase();
      clues.push(ph ? `Ledtråd 3: här säger man «<b>${esc(ph.a)}</b>» för ”${esc(ph.b.toLowerCase())}”` : 'Ledtråd 3: titta efter skyltarna!'); return clues; },
  ];
  for (const step of steps) {
    if (!alive()) break;
    quizCard('<h2>Gissa staden!</h2>' + step().map(c => `<p>${c}</p>`).join(''));
    await wait(14000);
  }
  if (alive()) {
    quizCard(`<p>Det är…</p><p class="big">${flag(cc)} ${esc(T.place.city || T.tourCity?.city || '')}</p><p>${esc(T.place.country || '')}</p>`);
    await wait(8000);
  }
  el('gv-quiz').classList.remove('show');
  if (trip === T.trip) { T.quizHiding = false; document.body.classList.remove('gv-hide'); renderWx(); }
}

// ---------------------------------------------------------------------------------------------
// Världen runt + röstning med mobilen (via ntfy.sh, en gratis tjänst för meddelanden)
// ---------------------------------------------------------------------------------------------
let tourGen = 0;
const localHour = tz => { try { return parseInt(new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hour12: false, timeZone: tz }).format(new Date()), 10); } catch (e) { return 12; } };
function buildQueue() {
  const hasToken = !!(el('token') && el('token').value.trim());
  const ok = WORLD.filter(c => c.src === 'px' || hasToken);
  const shuffle = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
  const day = shuffle(ok.filter(c => { const h = localHour(c.tz); return h >= 7 && h < 19; }));
  const night = shuffle(ok.filter(c => !day.includes(c)));
  return day.concat(night);
}
function showTour(c) {
  const t = el('gv-tour'), h = new Intl.DateTimeFormat('sv-SE', { hour: '2-digit', minute: '2-digit', timeZone: c.tz }).format(new Date());
  t.innerHTML = GV.opts.quiz ? '<small>Nästa resmål</small><h2>???</h2><p>Hämtar gatubilder…</p>'
    : `<small>Nästa resmål</small><h2>${flag(c.cc)} ${esc(c.city)}</h2><p>Klockan är ${esc(h)} där · hämtar gatubilder…</p>`;
  t.classList.add('show');
}
const hideTour = () => el('gv-tour').classList.remove('show');

GV.startWorld = async () => {
  const my = ++tourGen;
  let queue = buildQueue();
  if (!queue.length) { status('Inga städer tillgängliga för Världen runt.', true); return false; }
  while (my === tourGen) {
    if (!queue.length) queue = buildQueue();
    let next = queue.shift();
    const voted = T.vote && T.vote.round ? voteWinner() : null;
    if (voted) { next = voted; queue = queue.filter(c => c !== voted); }
    closeVote(true);
    T.tourCity = next; showTour(next);
    const ok = await GV.startTrip({ from: next.from, to: next.to, label: next.city, tz: next.tz, city: next });
    hideTour();
    if (my !== tourGen) return true;
    if (!ok) { await wait(1500); continue; }
    if (GV.opts.votes) openVote(queue.slice(0, 3));
    const until = Date.now() + GV.opts.tourMin * 60000;
    while (Date.now() < until && my === tourGen) { await wait(1000); if (T.vote) renderVote(Math.ceil((until - Date.now()) / 1000)); }
  }
  return true;
};
GV.on('stop', () => { tourGen++; hideTour(); closeVote(); });
GV.on('manual', () => { tourGen++; hideTour(); });

function openVote(options) {
  if (!options.length) return;
  let topic = null;
  try { topic = localStorage.getItem('gvTopic'); } catch (e) { /* ignore */ }
  if (!topic || !/^gatuvy-[a-z0-9]{12}$/.test(topic)) { topic = 'gatuvy-' + rnd(12); try { localStorage.setItem('gvTopic', topic); } catch (e) { /* ignore */ } }
  const v = T.vote = { topic, round: rnd(6), options, ballots: new Map(), es: T.vote?.es || null };
  if (!v.es) {
    v.es = new EventSource(`https://ntfy.sh/${topic}/sse`);
    v.es.onmessage = ev => {
      try {
        const msg = JSON.parse(JSON.parse(ev.data).message || '{}');
        if (!T.vote || msg.r !== T.vote.round || !T.vote.options.some(o => o.city === msg.c)) return;
        T.vote.ballots.set(String(msg.id).slice(0, 40), msg.c);   // en röst per mobil och omgång
        renderVote();
      } catch (e) { /* skräp ignoreras */ }
    };
  }
  const url = `${location.origin}${location.pathname}?vote=${topic}&r=${v.round}&o=${encodeURIComponent(options.map(o => o.city).join('|'))}`;
  const box = el('gv-vote');
  box.innerHTML = '<b>Välj nästa stad!</b><div class="gv-qr"></div><div class="tally"></div>';
  box.style.display = 'block';
  v.url = url; renderQR(box.querySelector('.gv-qr'), url, 132); renderVote();
}
function tally() {
  const n = new Map(T.vote.options.map(o => [o.city, 0]));
  for (const c of T.vote.ballots.values()) n.set(c, (n.get(c) || 0) + 1);
  return n;
}
function renderVote(secs) {
  if (!T.vote) return;
  const t = el('gv-vote').querySelector('.tally'); if (!t) return;
  const rows = [...tally()].map(([c, k]) => `<div class="t"><span>${esc(c)}</span><span>${k}</span></div>`).join('');
  t.innerHTML = rows + (secs ? `<div class="t" style="color:#999;margin-top:4px"><span>Byte om</span><span>${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}</span></div>` : '');
}
function voteWinner() {
  let best = null, bestN = 0;
  for (const [c, k] of tally()) if (k > bestN) { best = c; bestN = k; }
  return best ? T.vote.options.find(o => o.city === best) : null;
}
function closeVote(keepStream) {
  const box = el('gv-vote'); if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  if (T.vote && !keepStream && T.vote.es) { T.vote.es.close(); T.vote.es = null; }
  if (T.vote) { T.vote.round = null; if (!keepStream) T.vote = null; }
}

// Röstsidan som öppnas i mobilen när man skannar QR-koden
function renderVotePage() {
  const topic = qs.get('vote'), r = qs.get('r') || '', opts = (qs.get('o') || '').split('|').filter(Boolean).slice(0, 6);
  document.title = 'Gatuvy – välj nästa stad';
  const st = document.createElement('style');
  st.textContent = `html,body{overflow:auto!important;height:auto!important} .vp{max-width:420px;margin:0 auto;padding:28px 18px;font:16px system-ui,sans-serif;color:#eee}
    .vp h1{font-size:24px;margin:0 0 6px} .vp p{color:#aaa;margin:0 0 18px} .vp button{display:block;width:100%;margin:10px 0;padding:16px;font-size:18px;border-radius:12px}
    .vp .ok{font-size:20px;text-align:center;margin-top:30px}`;
  document.head.appendChild(st);
  document.body.innerHTML = '<div class="vp"><h1>Välj nästa stad</h1><p>Rösten räknas på skärmen direkt. Flest röster vinner när resan byter stad.</p><div id="vb"></div></div>';
  const vb = el('vb');
  if (!/^gatuvy-[a-z0-9]{12}$/.test(topic || '') || !opts.length) { vb.textContent = 'Den här röstlänken är ogiltig.'; return; }
  let id = null;
  try { id = localStorage.getItem('gvVoter'); if (!id) { id = rnd(10); localStorage.setItem('gvVoter', id); } } catch (e) { id = rnd(10); }
  for (const c of opts) {
    const b = document.createElement('button'); b.textContent = c;
    b.onclick = async () => {
      for (const x of vb.querySelectorAll('button')) x.disabled = true;
      try {
        const res = await fetch(`https://ntfy.sh/${topic}`, { method: 'POST', body: JSON.stringify({ c, r, id }) });
        if (!res.ok) throw new Error(res.status);
        vb.innerHTML = `<div class="ok">Tack! Din röst: <b>${esc(c)}</b> ✨</div>`;
      } catch (e) { vb.insertAdjacentHTML('beforeend', '<p>Rösten gick inte fram – försök igen.</p>'); for (const x of vb.querySelectorAll('button')) x.disabled = false; }
    };
    vb.appendChild(b);
  }
}

// ---------------------------------------------------------------------------------------------
// Händelser från uppspelningen
// ---------------------------------------------------------------------------------------------
GV.on('trip', d => {
  const trip = ++T.trip;
  T.pois = []; T.routePts = []; T.place = {}; T.frames = d.frames; T.stopAt = null; T.wx = null; T.tz = d.tz || null; T.sign = null; T.revAt = null;
  T.stopsDone.clear(); T.spoken.clear(); T.lastStopAt = -1e9; T.phraseIdx = Math.floor(Math.random() * 5);
  T.tourCity = d.city || null;
  clearBubbles(); hideCard(); renderSign(); renderWx();
  // Rutten glesad till var 25:e meter, för avståndsberäkningar
  let acc = 1e9;
  for (let i = 0; i < d.coords.length; i++) { if (i) acc += dist(d.coords[i - 1], d.coords[i]); if (acc >= 25) { T.routePts.push(d.coords[i]); acc = 0; } }
  const start = d.coords[0];
  T.placeReady = updateSign(start, trip);
  if (GV.opts.bubbles || GV.opts.stops || GV.opts.voice) loadPOIs(d.coords, trip).catch(e => console.warn(e));
  loadSponsors(trip).catch(e => console.warn(e));
  if (!T.home && GV.opts.home) loadHome();
  loadWeather(start, trip, d.tz);
  if (GV.opts.quiz) runQuiz(trip, start);
});
GV.on('frame', ({ f }) => { if (GV.opts.sign || GV.opts.phrase || GV.opts.quiz) updateSign(f.p, T.trip); });
GV.on('tick', v => {
  T.view = v;
  if (!GV.opts.bubbles || T.quizHiding) { if (bubbleEls.size) clearBubbles(); return; }
  placeBubbles(v);
});
GV.on('stop', () => {
  T.trip++; clearBubbles(); hideCard(); T.sign = null; renderSign(); renderWx();
  el('gv-quiz').classList.remove('show'); el('gv-phrase').classList.remove('show');
  T.quizHiding = false; document.body.classList.remove('gv-hide');
  if (window.speechSynthesis) speechSynthesis.cancel();
});
// Väder uppdateras var tionde minut under långa visningar
setInterval(() => { if (isPlaying() && GV.opts.weather && T.view) loadWeather(T.view.pos, T.trip, T.tz); }, 600000);

// iPhone-helskärm: allt ritas in i videobilden, eftersom vanliga element inte syns där
GV.on('compose', ({ ctx: c, W, H }) => {
  const box = (x, y, w, h, fill, r = 10) => { c.fillStyle = fill; c.beginPath(); c.roundRect ? c.roundRect(x, y, w, h, r) : c.rect(x, y, w, h); c.fill(); };
  const fit = (s, max) => { while (s.length > 3 && c.measureText(s).width > max) s = s.slice(0, -2); return s; };
  c.shadowBlur = 0; c.textBaseline = 'alphabetic';
  if (!T.quizHiding && GV.opts.bubbles) for (const b of T.bubbles) {
    const x = b.xf * W, y = b.yf * H; c.globalAlpha = b.o ?? 1; c.font = '600 15px system-ui, sans-serif';
    const t1 = fit(b.title, 240), w1 = c.measureText(t1).width; c.font = '12px system-ui, sans-serif';
    const t2 = b.desc ? fit(b.desc, 240) : '', w = Math.max(w1, c.measureText(t2).width) + 20, h = t2 ? 46 : 30;
    box(x - w / 2, y - h, w, h, b.sp ? 'rgba(40,30,4,.86)' : 'rgba(14,14,18,.8)');
    c.fillStyle = '#fff'; c.font = '600 15px system-ui, sans-serif'; c.fillText(t1, x - w / 2 + 10, y - h + 20);
    if (t2) { c.fillStyle = '#cfd3dc'; c.font = '12px system-ui, sans-serif'; c.fillText(t2, x - w / 2 + 10, y - h + 38); }
    c.globalAlpha = 1;
  }
  if (GV.opts.sign && T.sign && !T.quizHiding) {
    c.font = '600 20px system-ui, sans-serif'; const w = Math.max(200, c.measureText(T.sign.road).width + 40);
    box(W / 2 - w / 2, 14, w, T.sign.sub ? 52 : 36, '#1b4fa8', 8);
    c.fillStyle = '#fff'; c.textAlign = 'center'; c.fillText(T.sign.road, W / 2, 39);
    if (T.sign.sub) { c.font = '12px system-ui, sans-serif'; c.fillText(T.sign.sub, W / 2, 57); }
    c.textAlign = 'left';
  }
  if (GV.opts.weather && (T.wx || T.tz)) {
    const w = T.wx ? wmo(T.wx.code, T.wx.day) : null;
    const s = `${T.quizHiding ? '' : (T.place.city ? T.place.city + ' · ' : '')}${localTime()}${w ? ` · ${Math.round(T.wx.temp)}° ${w.icon}` : ''}`;
    c.font = '15px system-ui, sans-serif'; const tw = c.measureText(s).width;
    box(W - tw - 36, 16, tw + 20, 30, 'rgba(0,0,0,.5)'); c.fillStyle = '#fff'; c.fillText(s, W - tw - 26, 37);
  }
  if (T.phraseNow) {
    c.font = '15px system-ui, sans-serif'; const tw = c.measureText(T.phraseNow).width;
    box(W / 2 - tw / 2 - 14, H - 84, tw + 28, 32, 'rgba(14,14,18,.85)', 16); c.fillStyle = '#fff'; c.fillText(T.phraseNow, W / 2 - tw / 2, H - 62);
  }
  if (T.card) {
    const x = W - 400, y = H / 2 - 110; box(x, y, 370, 220, 'rgba(14,14,18,.92)', 14);
    c.fillStyle = '#e7b416'; c.font = '11px system-ui, sans-serif'; c.fillText(T.card.sub.toUpperCase(), x + 16, y + 26);
    c.fillStyle = '#fff'; c.font = '600 21px system-ui, sans-serif'; c.fillText(fit(T.card.title, 330), x + 16, y + 52);
    c.fillStyle = '#d6d9e0'; c.font = '13px system-ui, sans-serif';
    const words = T.card.text.split(' '); let line = '', ly = y + 76;
    const qc = qrCanvas(T.card.qr), maxW = qc ? 240 : 338;
    for (const wd of words) { if (c.measureText(line + wd).width > maxW) { c.fillText(line, x + 16, ly); line = ''; ly += 18; if (ly > y + 200) break; } line += wd + ' '; }
    if (ly <= y + 200) c.fillText(line, x + 16, ly);
    if (qc) { box(x + 270, y + 110, 88, 88, '#fff', 6); c.drawImage(qc, x + 274, y + 114, 80, 80); }
  }
  const q = el('gv-quiz');
  if (q && q.classList.contains('show')) {
    const lines = [...q.querySelectorAll('h2, p')].map(n => n.textContent);
    c.font = '18px system-ui, sans-serif'; const w = Math.max(...lines.map(s => c.measureText(s).width)) + 50, h = lines.length * 30 + 30;
    box(W / 2 - w / 2, H * 0.28 - h / 2, w, h, 'rgba(14,14,18,.9)', 16); c.fillStyle = '#fff'; c.textAlign = 'center';
    lines.forEach((s, i) => { c.font = i === 0 ? '600 24px system-ui, sans-serif' : '18px system-ui, sans-serif'; c.fillText(s, W / 2, H * 0.28 - h / 2 + 42 + i * 30); });
    c.textAlign = 'left';
  }
  const vb = el('gv-vote'), vq = qrCanvas(vb);
  if (vb && vb.style.display === 'block' && vq) { box(W - 170, H - 230, 150, 176, 'rgba(14,14,18,.88)'); c.fillStyle = '#fff'; c.font = '600 13px system-ui, sans-serif';
    c.fillText('Välj nästa stad!', W - 160, H - 210); box(W - 160, H - 200, 130, 130, '#fff', 6); c.drawImage(vq, W - 155, H - 195, 120, 120); }
  const tr = el('gv-tour');
  if (tr && tr.classList.contains('show')) { c.fillStyle = 'rgba(0,0,0,.85)'; c.fillRect(0, 0, W, H); c.fillStyle = '#fff'; c.textAlign = 'center';
    c.font = '600 44px system-ui, sans-serif'; c.fillText(tr.querySelector('h2').textContent, W / 2, H / 2); c.textAlign = 'left'; }
});

// ---------------------------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------------------------
GV.opts = loadOpts();
GV.debug = T;   // för felsökning i webbläsarens konsol
GV.init = () => {
  buildOverlays(); buildSettings();
  if (GV.opts.home) loadHome();
  phraseLoop();
};
if (GV.votePage) document.addEventListener('DOMContentLoaded', renderVotePage);
})();
