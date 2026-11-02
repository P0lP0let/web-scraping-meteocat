"""
Scraper de les estacions XEMA de Meteocat (Pràctica 1, UOC).

Respecta el robots.txt de meteo.cat, que prohibeix les URLs amb data
(/observacions/xema/dades?*dia=). Per això NO es consulta l'històric dia a dia:
cada execució captura el que cada estació mostra "avui" (en Temps Universal, TU) i
es van acumulant dies executant l'script cada dia.

Fase 1 - Descobriment d'estacions (dues vies complementàries i permeses):
  a) sitemap.xml (anunciat al robots.txt): conté URLs /observacions/xema/dades?codi=XX
  b) crawl BFS des de la portada -> menú Observacions -> "Llistat d'estacions", que
     dona les metadades (comarca, municipi, coordenades, altitud, estat) i més enllaços.
Fase 2 - Per a cada estació operativa es visita /observacions/xema/dades?codi=XX i
  se n'extreu el resum diari i la taula d'observacions semihoràries.

Sortida (s'acumula entre execucions; una fila per clau, la darrera captura la sobreescriu):
  meteocat_resum_diari.csv               -> 1 fila per estació i dia
  meteocat_observacions_semihoraries.csv -> 1 fila per estació, dia i franja de 30 min

Exemples:
  python meteocat.py --maxStations 5            (prova)
  python meteocat.py --maxStations 0            (totes les operatives)
"""
import argparse
import csv
import logging
import os
import random
import re
import time
from collections import deque
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs

import requests
from bs4 import BeautifulSoup

# --- Configuració -----------------------------------------------------------
SEED_URL = "https://www.meteo.cat/"
DOMAIN = "www.meteo.cat"
LIST_PATH = "/observacions/llistat-xema"        # llistat d'estacions (metadades + enllaços)
DATA_PATH = "/observacions/xema/dades"          # dades d'una estació (?codi=XX)
ALLOWED_PATHS = (LIST_PATH, "/observacions/xema")   # el crawler no surt d'aquesta secció
DATE_PARAM = "dia"                              # URLs amb aquest paràmetre: prohibides per robots.txt
SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip",
                   ".xls", ".xlsx", ".csv", ".doc", ".docx", ".mp4")
USER_AGENT = "UOC-Practica1-Crawler/1.0 (estudiants UOC; contacte: prequenamal@uoc.edu)"
MIN_DELAY, MAX_DELAY = 1.5, 3.5                 # segons entre peticions

# Etiquetes del "Resum diari" de la pàgina -> nom de columna del CSV
SUMMARY_LABELS = {
    "Temperatura mitjana": "temp_mitjana",
    "Temperatura màxima": "temp_maxima",
    "Temperatura mínima": "temp_minima",
    "Humitat relativa mitjana": "humitat_mitjana",
    "Precipitació acumulada": "precipitacio_acumulada",
    "Ratxa màxima del vent": "ratxa_maxima_vent",
    "Pressió atmosfèrica mitjana": "pressio_mitjana",
    "Irradiació solar global": "irradiacio_global",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("meteocat")


# --- Xarxa ------------------------------------------------------------------
def build_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ca,es;q=0.8,en;q=0.5"})
    return s


def check_user_agent(session):
    """Mostra el User-Agent que realment arriba al servidor (requisit de l'enunciat)."""
    try:
        r = session.get("https://httpbin.org/user-agent", timeout=15)
        log.info("User-Agent rebut pel servidor: %s", r.json().get("user-agent"))
    except requests.RequestException as exc:
        log.warning("No s'ha pogut comprovar el User-Agent: %s", exc)


def load_robots():
    rp = robotparser.RobotFileParser()
    rp.set_url(f"https://{DOMAIN}/robots.txt")
    rp.read()
    return rp


def fetch_text(session, robots, url, retries=3):
    """GET respectuós (robots.txt, pausa aleatòria, reintents amb espera exponencial)."""
    if not robots.can_fetch(USER_AGENT, url):
        log.warning("robots.txt no permet %s", url)
        return None
    for attempt in range(1, retries + 1):
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return r.text
            log.warning("HTTP %s a %s (intent %d)", r.status_code, url, attempt)
            if r.status_code in (403, 404):
                return None
        except requests.RequestException as exc:
            log.warning("Error de xarxa: %s (intent %d)", exc, attempt)
        time.sleep(2 ** attempt)
    return None


def get_soup(session, robots, url):
    text = fetch_text(session, robots, url)
    return BeautifulSoup(text, "html.parser") if text else None


# --- Utilitats d'URL ---------------------------------------------------------
def normalize(url):
    """Treu el fragment (#...) perquè la mateixa pàgina no compti dues vegades."""
    return urldefrag(url)[0]


def query_value(url, key):
    return parse_qs(urlparse(url).query).get(key, [None])[0]


def station_url(code):
    return f"https://{DOMAIN}{DATA_PATH}?codi={code}"


def is_followable(url):
    """Domini, tipus de fitxer i ruta permesos; mai URLs amb data (prohibides)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.netloc != DOMAIN:
        return False
    if p.path.lower().endswith(SKIP_EXTENSIONS):
        return False
    if DATE_PARAM in parse_qs(p.query):
        return False
    return p.path.startswith(ALLOWED_PATHS)


def is_station_page(url):
    return urlparse(url).path.rstrip("/") == DATA_PATH and query_value(url, "codi") is not None


# --- Fase 1: descobriment d'estacions ----------------------------------------
def discover_from_sitemap(session, robots):
    """Llegeix el sitemap anunciat al robots.txt i en treu els codis d'estació.

    Es fa amb una expressió regular i no amb un parser XML perquè el sitemap fa servir
    el prefix 'xhtml:' sense declarar-lo, cosa que fa fallar un parser XML estricte.
    """
    sitemaps = robots.site_maps() or [f"https://{DOMAIN}/sitemap.xml"]
    codes = set()
    for sm in sitemaps:
        text = fetch_text(session, robots, sm)
        if not text:
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text):
            if is_station_page(loc):
                codes.add(query_value(loc, "codi"))
    log.info("Sitemap: %d estacions", len(codes))
    return codes


CODE_RE = re.compile(r"\[([A-Z0-9]{2})\]\s*$")


def parse_station_list(soup):
    """Taula del llistat: Comarca, Municipi, Estació [Codi], Lat, Lon, Alt, Alta, Baixa, Estat."""
    stations = {}
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 8:
            continue
        m = CODE_RE.search(cells[2])
        if not m:
            continue
        stations[m.group(1)] = {
            "nom_estacio": CODE_RE.sub("", cells[2]).strip(),
            "comarca": cells[0], "municipi": cells[1],
            "latitud": cells[3], "longitud": cells[4], "altitud_m": cells[5],
            "data_alta": cells[6],
            "data_baixa": cells[7] if len(cells) >= 9 else "",
            "estat": cells[-1],
        }
    return stations


def discover(session, robots, args):
    """Combina sitemap i crawl BFS des de la portada. Retorna (codis, metadades)."""
    stations = {code: station_url(code) for code in discover_from_sitemap(session, robots)}
    metadata = {}

    queue, visited = deque([(SEED_URL, 0)]), set()
    while queue and len(visited) < args.maxPages:
        url, depth = queue.popleft()
        url = normalize(url)
        if url in visited:
            continue
        visited.add(url)
        soup = get_soup(session, robots, url)
        if soup is None:
            continue
        log.info("[%d/%d] prof=%d %s", len(visited), args.maxPages, depth, url)
        if urlparse(url).path.rstrip("/") == LIST_PATH:
            metadata.update(parse_station_list(soup))
            log.info("Llistat: metadades de %d estacions", len(metadata))
        if depth >= args.maxDepth:
            continue
        for a in soup.find_all("a", href=True):
            link = normalize(urljoin(url, a["href"]))
            if not is_followable(link):
                continue
            if is_station_page(link):                  # no es visita ara: es guarda per a la fase 2
                code = query_value(link, "codi")
                stations.setdefault(code, station_url(code))
            elif link not in visited:
                queue.append((link, depth + 1))

    codes = sorted(stations)
    if metadata:                                       # les desmantellades no tenen dades
        codes = [c for c in codes if metadata.get(c, {}).get("estat", "Operativa") == "Operativa"]
    log.info("Estacions candidates (sitemap + enllaços): %d", len(codes))
    return codes, metadata


# --- Fase 2: extracció d'una estació -----------------------------------------
def parse_key_value_rows(soup):
    """Files de 2-3 cel·les {etiqueta: (valor, hora)}: resum diari i metadades de l'estació."""
    kv = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) in (2, 3) and cells[0] and cells[1]:
            kv[cells[0]] = (cells[1], cells[2] if len(cells) == 3 else "")
    return kv


def parse_summary(kv):
    """Tradueix les etiquetes del resum diari a columnes (valor en brut, sense netejar)."""
    row = {}
    for label, (value, hour) in kv.items():
        for prefix, col in SUMMARY_LABELS.items():
            if label.startswith(prefix):
                row[col] = value
                if hour:
                    row[col + "_hora_tu"] = hour
    return row


def parse_page_metadata(soup, kv):
    """Metadades de la pròpia pàgina (per a estacions que el llistat no cobreix)."""
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True).replace("Dades de l'estació automàtica", "").strip() if h1 else ""
    get = lambda k: kv.get(k, ("", ""))[0]
    return {"nom_estacio": name, "comarca": get("Comarca"), "municipi": get("Municipi"),
            "altitud_m": get("Altitud"), "data_alta": get("Data alta"), "estat": get("Estat actual")}


def parse_observations(soup):
    """Taula semihorària: capçaleres 'TM °C', 'HRM %'... (nom curt = 1a paraula)."""
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        # Les capçaleres es llegeixen NOMÉS de la primera fila: a les files de dades,
        # la franja horària ("00:00 - 00:30") també és un <th> i no s'ha de barrejar.
        headers = [c.get_text(" ", strip=True).split()[0]
                   for c in trs[0].find_all(["th", "td"]) if c.get_text(strip=True)]
        if headers and headers[0].startswith("Per"):          # columna "Període TU"
            headers[0] = "periode_tu"
            rows = []
            for tr in trs[1:]:
                cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                if len(cells) == len(headers):
                    rows.append(dict(zip(headers, cells)))
            return rows
    return []


def scrape_station(session, robots, code, metadata):
    """Retorna (fila de resum, files d'observacions) d'una estació, o (None, [])."""
    soup = get_soup(session, robots, station_url(code))
    if soup is None:
        return None, []
    now = datetime.now(timezone.utc)
    kv = parse_key_value_rows(soup)
    obs = parse_observations(soup)
    summary = parse_summary(kv)
    if not summary and not obs:
        log.warning("Estació %s: no s'ha trobat cap dada (revisar selectors)", code)
        return None, []
    meta = metadata.get(code) or parse_page_metadata(soup, kv)
    day = now.date().isoformat()                       # data en TU de la captura
    base = {"codi_estacio": code, "data_observacio": day}
    summary_row = {**base, "captura_utc": now.isoformat(timespec="seconds"),
                   "ultim_periode_tu": obs[-1]["periode_tu"] if obs else "", **meta, **summary}
    obs_rows = [{**base, **r} for r in obs]
    return summary_row, obs_rows


# --- Desament acumulatiu -------------------------------------------------------
def merge_save(path, new_rows, key_fields):
    """Afegeix files a un CSV existent; una fila amb la mateixa clau se sobreescriu."""
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[tuple(r.get(k, "") for k in key_fields)] = r
    for r in new_rows:
        rows[tuple(str(r.get(k, "")) for k in key_fields)] = r
    if not rows:
        log.error("Cap registre per desar a %s", path)
        return
    fields = list(dict.fromkeys(k for r in rows.values() for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows.values())
    log.info("%s: %d files", path, len(rows))


def main():
    ap = argparse.ArgumentParser(description="Instantània diària de la XEMA (Meteocat)")
    ap.add_argument("--maxStations", type=int, default=5, help="estacions a visitar (0 = totes)")
    ap.add_argument("--maxPages", type=int, default=15, help="màxim de pàgines del crawl de descobriment")
    ap.add_argument("--maxDepth", type=int, default=3, help="profunditat màxima des de la portada")
    ap.add_argument("--outputDir", default=".", help="carpeta dels CSV de sortida")
    args = ap.parse_args()

    session, robots = build_session(), load_robots()
    check_user_agent(session)
    codes, metadata = discover(session, robots, args)
    if args.maxStations > 0:
        codes = codes[: args.maxStations]

    summaries, observations = [], []
    try:
        for i, code in enumerate(codes, 1):
            summary, obs = scrape_station(session, robots, code, metadata)
            if summary:
                summaries.append(summary)
                observations.extend(obs)
            log.info("Estació %s (%d/%d): %d franges", code, i, len(codes), len(obs))
    finally:                                            # si s'interromp, es desa el que hi hagi
        os.makedirs(args.outputDir, exist_ok=True)
        merge_save(os.path.join(args.outputDir, "meteocat_resum_diari.csv"),
                   summaries, ("codi_estacio", "data_observacio"))
        merge_save(os.path.join(args.outputDir, "meteocat_observacions_semihoraries.csv"),
                   observations, ("codi_estacio", "data_observacio", "periode_tu"))


if __name__ == "__main__":
    main()
