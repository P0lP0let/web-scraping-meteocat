# Meteocat XEMA Scraper

Pràctica 1 de *Tipologia i cicle de vida de les dades* · Màster universitari en Ciència de Dades · UOC

**Integrants del grup**

- Pol Requena
- Aruna Carbó

**DOI del dataset (Zenodo):** https://doi.org/[DOI]

---

## Descripció

Aquest projecte és un scraper en Python que captura les dades de la Xarxa d'Estacions Meteorològiques Automàtiques (XEMA) del Servei Meteorològic de Catalunya, publicades a [meteo.cat](https://www.meteo.cat/).

Cada execució fa una instantània de l'estat de les estacions operatives: metadades de l'estació, resum diari i observacions semihoràries del dia en curs. Executant-lo diàriament, els fitxers CSV s'acumulen però sense duplicitats.

El `robots.txt` de meteo.cat prohibeix les URLs amb paràmetre de data (`/observacions/xema/dades?*dia=`). Per això el scraper **no** consulta l'històric dia a dia: només visita les pàgines d'estació sense data, i el dataset es forma acumulant captures diàries.

## Fitxers del repositori

```
.
├── README.md                  Aquest document
├── requirements.txt           Llibreries i versions necessàries
├── source/
│   └── meteocat.py   Codi de l'scraper
└── dataset/
    ├── meteocat_resum_diari.csv               Una fila per estació i dia
    └── meteocat_observacions_semihoraries.csv Una fila per estació, dia i franja de 30 min
```

## Requisits i instal·lació

Cal Python 3.8 o superior.

```bash
# 1. Crear i activar un entorn virtual
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux / macOS

# 2. Instal·lar les dependències
pip install -r requirements.txt
```

L'script s'identifica davant del servidor amb un User-Agent propi que inclou un correu de contacte (constant `USER_AGENT`, a dalt de `source/meteocat.py`).

## Ús

```bash
python source/meteocat.py [--maxStations N] [--maxPages N] [--maxDepth N] [--outputDir DIR]
```

| Paràmetre | Valor per defecte | Descripció |
|---|---|---|
| `--maxStations` | `5` | Nombre d'estacions que es visiten. `0` = totes les operatives. |
| `--maxPages` | `15` | Màxim de pàgines del crawl de descobriment (portada, llistat...). |
| `--maxDepth` | `3` | Profunditat màxima de navegació des de la portada. |
| `--outputDir` | `.` | Carpeta on es desen els CSV. |

**Exemples replicables**

```bash
# Prova ràpida amb 5 estacions, desant a la carpeta dataset/
python source/meteocat.py --maxStations 5 --outputDir dataset

# Captura completa de totes les estacions operatives (uns 8-10 minuts)
python source/meteocat.py --maxStations 0 --outputDir dataset
```

**Recomanació d'execució:** una vegada al dia, cap a les 23:45 hora local. Les pàgines de meteo.cat mostren el dia en Temps Universal (TU) i, passada la mitjanit TU (01:00 hora local a l'hivern), comencen un dia nou gairebé buit. Si s'executa més d'una vegada el mateix dia, les files d'aquell dia se sobreescriuen i no es dupliquen.

## Com funciona

**Fase 1: descobriment d'estacions.**

1. Llegeix `robots.txt` i en treu l'adreça del `sitemap.xml`. Del sitemap extrau els enllaços `…/observacions/xema/dades?codi=XX`. S'analitza amb una expressió regular perquè el sitemap fa servir el prefix `xhtml:` sense declarar-lo.
2. Fa un recorregut en amplada (BFS) des de `https://www.meteo.cat/`, limitat a la secció d'observacions, fins al llistat d'estacions (`/observacions/llistat-xema`). D'aquesta taula n'obté les metadades de cada estació.
3. Uneix les estacions trobades per les dues vies i es queda amb les operatives.

**Fase 2: extracció per estació.** Per a cada estació visita `…/dades?codi=XX` i en llegeix el resum diari i la taula d'observacions semihoràries.

**Bon ús del web scraping**

- Respecta el `robots.txt` abans de cada petició.
- Pausa aleatòria d'1,5 a 3,5 s entre peticions, reintents amb espera exponencial.
- User-Agent identificable amb un correu de contacte, que es comprova en cada execució.
- Límits de pàgines, profunditat i estacions; filtre de domini, rutes i tipus de fitxer.
- No utilitza l'API de Meteocat.

## Contingut del dataset

Els valors es desen tal com apareixen al web (per exemple `18.7 °C`). No s'ha fet neteja, que correspon a la Pràctica 2.

### `meteocat_resum_diari.csv`

| Camp | Descripció |
|---|---|
| `codi_estacio` | Codi de l'estació (p. ex. `X4`) |
| `data_observacio` | Data (TU) de la captura |
| `captura_utc` | Moment exacte de la captura (UTC) |
| `ultim_periode_tu` | Darrera franja horària disponible en el moment de la captura |
| `nom_estacio`, `comarca`, `municipi` | Identificació i ubicació |
| `latitud`, `longitud`, `altitud_m` | Coordenades i altitud |
| `data_alta`, `data_baixa`, `estat` | Alta de l'estació, baixa (si n'hi ha) i estat actual |
| `temp_mitjana`, `temp_maxima`, `temp_minima` | Temperatures del dia |
| `temp_maxima_hora_tu`, `temp_minima_hora_tu` | Hora (TU) dels extrems |
| `humitat_mitjana` | Humitat relativa mitjana |
| `precipitacio_acumulada` | Precipitació acumulada |
| `ratxa_maxima_vent`, `ratxa_maxima_vent_hora_tu` | Ratxa màxima de vent (10 m) i hora |
| `pressio_mitjana` | Pressió atmosfèrica mitjana |
| `irradiacio_global` | Irradiació solar global |

### `meteocat_observacions_semihoraries.csv`

| Camp | Descripció |
|---|---|
| `codi_estacio`, `data_observacio` | Estació i data (TU) |
| `periode_tu` | Franja de 30 minuts en TU (p. ex. `00:00 - 00:30`) |
| `TM`, `TX`, `TN` | Temperatura mitjana, màxima i mínima (°C) |
| `HRM` | Humitat relativa mitjana (%) |
| `PPT` | Precipitació (mm) |
| `VVM`, `DVM`, `VVX` | Velocitat mitjana del vent (km/h), direcció (graus) i velocitat màxima (km/h), a 10 m |
| `PM` | Pressió atmosfèrica (hPa) |
| `RS` | Radiació solar (W/m²) |

Totes les hores estan en Temps Universal (TU). Per passar a hora oficial cal sumar-hi una hora a l'hivern i dues a l'estiu.

## Llicència i condicions d'ús

- Font de les dades: Servei Meteorològic de Catalunya (Meteocat), [meteo.cat](https://www.meteo.cat/). Consulteu l'[avís legal](https://www.meteo.cat/wpweb/avis-legal/) per a les condicions de reutilització.
- Llicència del dataset: [pendent de decidir; justificar-la a la memòria].
- Llicència del codi: [pendent de decidir].

## Referències

- Servei Meteorològic de Catalunya (Meteocat). *Llistat d'estacions automàtiques*. https://www.meteo.cat/observacions/llistat-xema
- Servei Meteorològic de Catalunya (Meteocat). *Dades d'estació*. https://www.meteo.cat/observacions/xema/dades
