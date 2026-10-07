"""
Structures hors mairie : offices de tourisme, comités des fêtes,
associations culturelles, MJC, centres sociaux, CE/CSE.

Le registre public donne le nom, la commune et l'adresse.
Il ne publie ni l'e-mail, ni le président. Chaque ligne a donc
une piste de recherche Google déjà formulée.
"""
import time
import urllib.parse

from scrapers.mairies import _get_json, _norm

SEARCH_API = "https://recherche-entreprises.api.gouv.fr/search"

QUERIES = {
    "ot": ["office de tourisme"],
    "fete": ["comite des fetes"],
    "asso": [
        "maison des jeunes et de la culture",
        "MJC",
        "centre social",
        "association culturelle",
    ],
    "ce": [
        "comite social et economique",
        "comite d entreprise",
    ],
    "prod": [
        "producteur de spectacles",
        "vigneron",
        "domaine viticole",
    ],
    "scene": [
        "agence culturelle",
        "scene nationale",
        "centre dramatique national",
        "scene conventionnee",
    ],
}


def piste_url(name, city, groupe):
    """Lien de recherche ciblé quand l'annuaire n'a pas l'e-mail."""
    city = city or ""
    folded = _norm(groupe)
    if "fete" in folded:
        query = f'"{name}" {city} président OR bureau email'
    elif "entreprise" in folded or folded.startswith("ce"):
        query = f'"{name}" {city} contact email facebook OR instagram OR linkedin'
    elif "communaut" in folded or "metropole" in folded or "intercommun" in folded or "conseil" in folded:
        query = f'"{name}" "service culture" OR "arts et scènes" OR animation email'
    elif "tourisme" in folded:
        query = f'"{name}" {city} contact email'
    else:
        query = f'"{name}" {city} contact email'
    return "https://www.google.fr/search?q=" + urllib.parse.quote(query)


def _accept(kind, name):
    folded = _norm(name)
    if kind == "ot":
        return "office" in folded and "tourisme" in folded
    if kind == "fete":
        return "comite" in folded and "fete" in folded
    if kind == "ce":
        return (
            "comite social" in folded
            or "comite d entreprise" in folded
            or "comite entreprise" in folded
            or "comite interentreprises" in folded
        )
    if kind == "prod":
        return any(token in folded for token in ("producteur", "vigneron", "viticole", "domaine"))
    if kind == "scene":
        return (
            "agence culturelle" in folded
            or "scene nationale" in folded
            or "centre dramatique" in folded
            or "scene conventionnee" in folded
            or "arts et scenes" in folded
            or "arts scenes" in folded
        )
    if "centre social" in folded and "ccas" not in folded and "action sociale" not in folded:
        return True
    if "maison des jeunes" in folded or folded.startswith("mjc") or " mjc" in folded or "(mjc)" in folded:
        return True
    if "culturel" in folded and "sport" not in folded:
        return True
    return False


def _pretty(name):
    if not name or not name.isupper():
        return name
    small = {"de", "des", "du", "la", "le", "les", "et", "d", "l", "au", "aux", "en"}
    words = []
    for index, word in enumerate(name.lower().split()):
        words.append(word if index and word in small else word.capitalize())
    return " ".join(words)


def _groupe(kind, name):
    folded = _norm(name)
    if kind == "ot":
        return "Office de tourisme"
    if kind == "fete":
        return "Comité des fêtes"
    if kind == "prod":
        return "Producteur"
    if kind == "ce":
        return "Comité d'entreprise"
    if kind == "scene":
        return "Scène ou agence culturelle"
    if "centre social" in folded:
        return "Centre social"
    if "maison des jeunes" in folded or "mjc" in folded:
        return "MJC"
    return "Association culturelle"


def _search_pages(query, dep_code, stop_flag):
    page = 1
    found = []
    while page <= 30:
        if stop_flag():
            break
        params = urllib.parse.urlencode({
            "q": query,
            "departement": dep_code,
            "per_page": 25,
            "page": page,
            "etat_administratif": "A",
        })
        try:
            data = _get_json(f"{SEARCH_API}?{params}")
        except Exception:
            break
        batch = data.get("results") or []
        found.extend(batch)
        total = data.get("total_results") or 0
        if not batch or page * 25 >= total:
            break
        page += 1
        time.sleep(0.12)
    return found


def fetch_organisations(dep_code, departement, communes, kinds, stop_flag):
    rows = []
    seen = set()
    for kind in ("ot", "fete", "asso", "ce", "prod", "scene"):
        if kind not in kinds or stop_flag():
            continue
        for query in QUERIES[kind]:
            for record in _search_pages(query, dep_code, stop_flag):
                siren = str(record.get("siren") or "")
                name = (record.get("nom_complet") or record.get("nom_raison_sociale") or "").strip()
                if not name or not _accept(kind, name):
                    continue
                if siren and siren in seen:
                    continue
                if siren:
                    seen.add(siren)
                siege = record.get("siege") or {}
                if str(siege.get("departement") or "") != str(dep_code):
                    continue
                insee = str(siege.get("commune") or "")
                commune = communes.get(insee) or {}
                city = siege.get("libelle_commune") or commune.get("nom") or ""
                population = commune.get("population")
                try:
                    population = int(population) if population is not None else None
                except (TypeError, ValueError):
                    population = None
                groupe = _groupe(kind, name)
                short = _pretty(name)
                city_label = _pretty(city) if city.isupper() else city
                label = short
                if city_label and _norm(city_label) not in _norm(short):
                    label = f"{short} — {city_label}"
                rows.append({
                    "nom": label,
                    "commune": label,
                    "code_postal": siege.get("code_postal") or "",
                    "code_insee": insee,
                    "population": population,
                    "departement": departement.get("nom") or "",
                    "code_departement": departement.get("code") or "",
                    "region": departement.get("region") or "",
                    "groupe": groupe,
                    "email": "",
                    "email_mairie": "",
                    "email_socioculturel": "",
                    "service_socioculturel": "",
                    "phone": "",
                    "url": "",
                    "adresse": siege.get("adresse") or "",
                    "recherche_url": piste_url(short, city_label, groupe),
                    "source": "mairie",
                })
    return rows
