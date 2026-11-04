import argparse
import csv
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL_INICI = "https://www.meteo.cat/"
USER_AGENT = "UOC-Practica1-scraper/1.0 (estudiants UOC; contacte: prequenamal@uoc.edu)"

# Les dades que trobem a la pàgina web del meteocat
ETIQUETES = {
    "Temperatura mitjana": "temp_mitjana",
    "Temperatura màxima": "temp_maxima",
    "Temperatura mínima": "temp_minima",
    "Humitat relativa mitjana": "humitat_mitjana",
    "Precipitació acumulada": "precipitacio_acumulada",
    "Ratxa màxima del vent": "ratxa_maxima_vent",
    "Pressió atmosfèrica mitjana": "pressio_mitjana",
    "Irradiació solar global": "irradiacio_global",
}

COLUMNES_RESUM = [
    "codi_estacio", "data_observacio", "captura_utc", "ultim_periode_tu",
    "nom_estacio", "comarca", "municipi", "latitud", "longitud", "altitud_m",
    "data_alta", "data_baixa", "estat",
    "temp_mitjana", "temp_maxima", "temp_maxima_hora_tu",
    "temp_minima", "temp_minima_hora_tu", "humitat_mitjana",
    "precipitacio_acumulada", "ratxa_maxima_vent", "ratxa_maxima_vent_hora_tu",
    "pressio_mitjana", "irradiacio_global",
]
COLUMNES_OBS = ["codi_estacio", "data_observacio", "periode_tu", "TM", "TX", "TN",
                "HRM", "PPT", "VVM", "DVM", "VVX", "PM", "RS"]

# Camps que identifiquen una fila (per no guardar-ne de repetides)
CLAU_RESUM = ["codi_estacio", "data_observacio"]
CLAU_OBS = ["codi_estacio", "data_observacio", "periode_tu"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("meteocat")


def preparar_robots():
    '''
    Llegeix el robots.txt i retorna un objecte per fer la validació posteriorment.
    '''
    robots = robotparser.RobotFileParser()
    robots.set_url("https://www.meteo.cat/robots.txt")
    robots.read()
    return robots


def comprovar_user_agent(sessio):
    '''
    Mostra l'User-Agent que fem servir és acceptat pel servidor.
    '''
    try:
        resposta = sessio.get("https://httpbin.org/user-agent", timeout=15)
        log.info("User-Agent rebut pel servidor: %s", resposta.json()["user-agent"])
    except requests.RequestException as error:
        log.warning("No s'ha pogut comprovar el User-Agent: %s", error)


def baixar_text(sessio, robots, url):
    '''
    Baixa el text d'una pàgina web, amb comprovació del robots.txt i reintents en cas d'error.
    '''
    if not robots.can_fetch(USER_AGENT, url):
        log.warning("robots.txt no permet %s", url)
        return None
    for intent in range(1, 4):
        time.sleep(random.uniform(1.5, 3.5))    # pauses aleatòries per no saturar el servidor
        try:
            resposta = sessio.get(url, timeout=20)
        except requests.RequestException as error:
            log.warning("Error de xarxa: %s (intent %d)", error, intent)
            continue
        if resposta.status_code == 200:
            return resposta.text
        log.warning("HTTP %s a %s (intent %d)", resposta.status_code, url, intent)
        if resposta.status_code in (403, 404):
            return None
    return None


def baixar_pagina(sessio, robots, url):
    '''
    Baixa una pàgina web i la retorna com a BeautifulSoup.
    '''
    text = baixar_text(sessio, robots, url)
    if text is None:
        return None
    return BeautifulSoup(text, "html.parser")


def trobar_enllac(pagina, text):
    '''
    Busca un enllaç dins d'una pàgina que contingui un text concret a l'URL i el retorna complet.
    '''
    for enllac in pagina.find_all("a", href=True):
        if text in enllac["href"]:
            return urljoin(URL_INICI, enllac["href"])
    return None


def llegir_llistat(pagina):
    '''
    Llegeix el llistat d'estacions i retorna una llista de diccionaris amb les dades bàsiques.
    '''
    estacions = []
    for fila in pagina.find_all("tr"):
        caselles = []
        for casella in fila.find_all("td"):
            caselles.append(casella.get_text(" ", strip=True))
        enllac = fila.find("a", href=True)
        if len(caselles) < 8 or enllac is None or "codi=" not in enllac["href"]:
            continue

        estacio = {}
        estacio["url"] = urljoin(URL_INICI, enllac["href"])
        estacio["codi_estacio"] = enllac["href"].split("codi=")[1].split("&")[0]
        estacio["nom_estacio"] = caselles[2].split(" [")[0]
        estacio["comarca"] = caselles[0]
        estacio["municipi"] = caselles[1]
        estacio["latitud"] = caselles[3]
        estacio["longitud"] = caselles[4]
        estacio["altitud_m"] = caselles[5]
        estacio["data_alta"] = caselles[6]
        estacio["data_baixa"] = ""
        if len(caselles) >= 9:
            estacio["data_baixa"] = caselles[7]
        estacio["estat"] = caselles[-1]
        estacions.append(estacio)
    return estacions


def comparar_amb_sitemap(sessio, robots, estacions):
    '''
    Comprova que les estacions del llistat coincideixen amb les del sitemap, i informa de les diferències.
    '''
    adreces_sitemap = robots.site_maps()
    if adreces_sitemap is None:
        adreces_sitemap = ["https://www.meteo.cat/sitemap.xml"]

    codis_sitemap = []
    for adreca_sitemap in adreces_sitemap:
        text = baixar_text(sessio, robots, adreca_sitemap)
        if text is None:
            continue
        # Busquem les adreces entre <loc> i </loc> (aquest sitemap no es pot llegir com a XML)
        for adreca in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text):
            if "/observacions/xema/dades?codi=" in adreca:
                codis_sitemap.append(adreca.split("codi=")[1].split("&")[0])

    codis_llistat = []
    for estacio in estacions:
        codis_llistat.append(estacio["codi_estacio"])

    nomes_llistat = []
    for codi in codis_llistat:
        if codi not in codis_sitemap:
            nomes_llistat.append(codi)
    nomes_sitemap = []
    for codi in codis_sitemap:
        if codi not in codis_llistat:
            nomes_sitemap.append(codi)

    log.info("Comprovació amb el sitemap: %d al llistat, %d al sitemap, %d en comú",
             len(codis_llistat), len(codis_sitemap), len(codis_llistat) - len(nomes_llistat))
    if len(nomes_llistat) > 0:
        log.info("Només al llistat: %s", ", ".join(nomes_llistat))
    if len(nomes_sitemap) > 0:
        log.info("Només al sitemap (poden ser estacions desmantellades): %s",
                 ", ".join(nomes_sitemap))


def llegir_resum(pagina):
    '''
    Llegeix el "Resum diari" d'una estació i retorna un diccionari amb les dades.
    '''
    resum = {}
    for fila in pagina.find_all("tr"):
        caselles = []
        for casella in fila.find_all(["th", "td"]):
            caselles.append(casella.get_text(" ", strip=True))
        if len(caselles) != 2 and len(caselles) != 3:
            continue
        for etiqueta in ETIQUETES:
            if caselles[0].startswith(etiqueta):
                columna = ETIQUETES[etiqueta]
                resum[columna] = caselles[1]
                if len(caselles) == 3 and caselles[2] != "":
                    resum[columna + "_hora_tu"] = caselles[2]
    return resum


def llegir_observacions(pagina):
    '''
    Llegeix les observacions semihoràries d'una estació i retorna una llista de diccionaris.
    '''
    for taula in pagina.find_all("table"):
        files = taula.find_all("tr")
        if len(files) == 0:
            continue

        # Noms de les columnes: de "TM °C" ens quedem amb "TM"
        noms = []
        for casella in files[0].find_all(["th", "td"]):
            text = casella.get_text(" ", strip=True)
            if text != "":
                noms.append(text.split()[0])
        if len(noms) == 0 or not noms[0].startswith("Per"):
            continue
        noms[0] = "periode_tu"

        observacions = []
        for fila in files[1:]:
            valors = []
            for casella in fila.find_all(["th", "td"]):
                valors.append(casella.get_text(strip=True))
            if len(valors) == len(noms):
                observacions.append(dict(zip(noms, valors)))
        return observacions
    return []


def fer_clau(fila, camps_clau):
    '''
    Defineix una clau única per a una fila, serveix per evitar duplicats.
    '''
    clau = ""
    for camp in camps_clau:
        clau = clau + str(fila.get(camp, "")) + "|"
    return clau


def guardar_csv(ruta, columnes, camps_clau, files_noves):
    '''
    Guarda un CSV amb les files noves, actualitzant les que ja hi havia amb la mateixa clau.
    '''
    files = {}

    # Primer llegim el que ja hi havia al fitxer
    if os.path.exists(ruta):
        with open(ruta, newline="", encoding="utf-8") as fitxer:
            for fila in csv.DictReader(fitxer):
                files[fer_clau(fila, camps_clau)] = fila

    # Després afegim les files noves (si ja hi eren, es substitueixen)
    for fila in files_noves:
        files[fer_clau(fila, camps_clau)] = fila

    with open(ruta, "w", newline="", encoding="utf-8") as fitxer:
        escriptor = csv.DictWriter(fitxer, fieldnames=columnes, extrasaction="ignore", restval="")
        escriptor.writeheader()
        for clau in files:
            escriptor.writerow(files[clau])
    log.info("%s: %d files", ruta, len(files))


def main():
    '''
    Programa principal: accedeix a la portada, busca les estacions, descarrega les dades d'aquestes i les guarda en CSV.
    '''
    parser = argparse.ArgumentParser(description="Instantània diària de la XEMA (Meteocat)")
    parser.add_argument("--maxStations", type=int, default=5, help="estacions a visitar (0 = totes)")
    parser.add_argument("--outputDir", default=".", help="carpeta dels CSV")
    args = parser.parse_args()

    sessio = requests.Session()
    sessio.headers.update({"User-Agent": USER_AGENT})
    robots = preparar_robots()
    comprovar_user_agent(sessio)

    # 1. Portada 
    portada = baixar_pagina(sessio, robots, URL_INICI)
    if portada is None:
        log.error("No s'ha pogut obrir la portada")
        return
    url_llistat = trobar_enllac(portada, "llistat-xema")
    if url_llistat is None:
        log.error("No s'ha trobat el llistat d'estacions")
        return

    # 2. Llistat d'estacions
    pagina_llistat = baixar_pagina(sessio, robots, url_llistat)
    if pagina_llistat is None:
        log.error("No s'ha pogut obrir el llistat d'estacions")
        return
    estacions = llegir_llistat(pagina_llistat)
    log.info("Estacions trobades: %d", len(estacions))
    comparar_amb_sitemap(sessio, robots, estacions)
    if args.maxStations > 0:
        estacions = estacions[:args.maxStations]

    # 3. Cada estació 
    resums = []
    observacions = []
    for i in range(len(estacions)):
        estacio = estacions[i]
        pagina = baixar_pagina(sessio, robots, estacio["url"])
        if pagina is None:
            continue

        resum = llegir_resum(pagina)
        files_obs = llegir_observacions(pagina)
        if len(resum) == 0 and len(files_obs) == 0:
            log.warning("Estació %s: no s'ha trobat cap dada", estacio["codi_estacio"])
            continue

        ara = datetime.now(timezone.utc)
        dia = ara.date().isoformat()

        fila_resum = dict(estacio)
        fila_resum["data_observacio"] = dia
        fila_resum["captura_utc"] = ara.isoformat(timespec="seconds")
        fila_resum["ultim_periode_tu"] = ""
        if len(files_obs) > 0:
            fila_resum["ultim_periode_tu"] = files_obs[-1]["periode_tu"]
        fila_resum.update(resum)
        resums.append(fila_resum)

        for obs in files_obs:
            obs["codi_estacio"] = estacio["codi_estacio"]
            obs["data_observacio"] = dia
            observacions.append(obs)
        log.info("Estació %s (%d/%d): %d franges",
                 estacio["codi_estacio"], i + 1, len(estacions), len(files_obs))

    # 4. Guardar
    os.makedirs(args.outputDir, exist_ok=True)
    guardar_csv(os.path.join(args.outputDir, "meteocat_resum_diari.csv"),
                COLUMNES_RESUM, CLAU_RESUM, resums)
    guardar_csv(os.path.join(args.outputDir, "meteocat_observacions_semihoraries.csv"),
                COLUMNES_OBS, CLAU_OBS, observacions)


if __name__ == "__main__":
    main()
