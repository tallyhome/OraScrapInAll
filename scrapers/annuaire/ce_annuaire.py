"""
CE et CSE depuis l'annuaire officiel des entreprises.

Source : https://recherche-entreprises.api.gouv.fr
(la même base que https://annuaire-entreprises.data.gouv.fr).
Aucune clé. L'annuaire publie le nom, l'adresse et le SIREN.
Il ne publie pas l'e-mail : les lignes n'en inventent pas.

Les catégories juridiques 8310 et 8311 sont celles des comités
d'entreprise, CSE, comités d'établissement et de leurs caisses sociales.
"""
import re
import time
import urllib.parse

from scrapers.annuaire.mairies import _get_json, _norm, load_geo
from scrapers.annuaire.organisations import _pretty

SEARCH_API = "https://recherche-entreprises.api.gouv.fr/search"
COMMUNES_API = "https://geo.api.gouv.fr/communes"
NATURES = ("8310", "8311")
FICHE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"
CE_RE = re.compile(r"(^|[^a-z])(cse|csec)([^a-z]|$)")
CE_PHRASES = (
    "comite social",
    "comite d entreprise",
    "comite entreprise",
    "comite d etablissement",
    "comite etablissement",
    "comite central",
    "comite inter",
)
SOCIAL_PHRASES = ("activite sociale", "oeuvre sociale", "caisse", "casi", "ccas")


def _key(text):
    return re.sub(r"[^a-z0-9]", "", _norm(text))


def classify(name):
    folded = _norm(name)
    if any(phrase in folded for phrase in CE_PHRASES) or CE_RE.search(folded):
        return "Comité d'entreprise"
    if any(phrase in folded for phrase in SOCIAL_PHRASES):
        return "Activité sociale"
    return ""


def resolve_place(text):
    """Ville, département, région ou France. None si rien ne correspond."""
    raw = (text or "").strip()
    if not raw:
        return None
    folded = _key(raw)
    if folded in ("france", "toutelafrance", "franceentiere"):
        return {"scope": "france", "label": "France"}
    regions, departements = load_geo()
    for dep in departements:
        if folded == _key(dep.get("code")) or folded == _key(dep.get("nom")):
            return {
                "scope": "departement",
                "code": dep["code"],
                "label": dep["nom"],
                "region": dep.get("region") or "",
            }
    for region in regions:
        region_key = _key(region.get("nom"))
        if folded == region_key or (len(folded) >= 6 and folded in region_key):
            return {"scope": "region", "code": region["code"], "label": region["nom"]}
    params = urllib.parse.urlencode({
        "nom": raw,
        "fields": "nom,code,codeDepartement",
        "boost": "population",
        "limit": 1,
    })
    try:
        rows = _get_json(f"{COMMUNES_API}?{params}")
    except Exception:
        rows = []
    if not rows:
        return None
    row = rows[0]
    dep = next((item for item in departements if item["code"] == row.get("codeDepartement")), {})
    return {
        "scope": "commune",
        "code": row["code"],
        "departement": row.get("codeDepartement") or "",
        "label": row.get("nom") or raw,
        "region": dep.get("region") or "",
        "departement_nom": dep.get("nom") or "",
    }


def _matches(record, place):
    siege = record.get("siege") or {}
    if place["scope"] == "departement":
        return str(siege.get("departement") or "") == str(place["code"])
    if place["scope"] == "commune":
        return str(siege.get("commune") or "") == str(place["code"])
    return True


def _pages(params, stop_flag, pause=0.12):
    page = 1
    while page <= 120 and not stop_flag():
        query = dict(params)
        query["page"] = page
        try:
            data = _get_json(SEARCH_API + "?" + urllib.parse.urlencode(query))
        except Exception:
            return
        batch = data.get("results") or []
        for record in batch:
            yield record
        total = data.get("total_results") or 0
        if not batch or page * int(params.get("per_page") or 25) >= total:
            return
        page += 1
        time.sleep(pause)


def _row(record, groupe, dep_names):
    siege = record.get("siege") or {}
    code = str(siege.get("departement") or "")
    info = dep_names.get(code) or {}
    city = siege.get("libelle_commune") or ""
    if city.isupper():
        city = _pretty(city)
    siren = str(record.get("siren") or "")
    name = _pretty((record.get("nom_complet") or "").strip())
    return {
        "nom": name,
        "commune": city,
        "code_postal": siege.get("code_postal") or "",
        "code_insee": str(siege.get("commune") or ""),
        "departement": info.get("nom") or code,
        "code_departement": code,
        "region": info.get("region") or "",
        "groupe": groupe,
        "siren": siren,
        "adresse": siege.get("adresse") or siege.get("geo_adresse") or "",
        "email": "",
        "phone": "",
        "url": (FICHE + siren) if siren else "",
        "source": "ce_annuaire",
    }


def collect_ce(places, companies, results_cache, stop_flag, max_results=200, include_social=True, on_detail=None):
    """Ajoute les CE/CSE dont le siège est dans la zone demandée."""
    def detail(message):
        if on_detail:
            on_detail(message)

    zones = []
    unknown = []
    for text in places or []:
        place = resolve_place(text)
        if place is None:
            unknown.append(text)
        elif place not in zones:
            zones.append(place)
    if unknown:
        detail("Lieu non reconnu : " + ", ".join(unknown))
    if not zones and not companies:
        if not unknown:
            detail("Indiquez une ville, un département, une région ou France.")
        return results_cache
    if not zones:
        zones = [{"scope": "france", "label": "France"}]

    _, departements = load_geo()
    dep_names = {dep["code"]: dep for dep in departements}
    seen = {str(row.get("siren") or "") for row in results_cache if row.get("siren")}
    queries = [c.strip() for c in (companies or []) if c and c.strip()] or [""]

    for place in zones:
        targets = [place]
        if place["scope"] == "region":
            targets = [
                {
                    "scope": "departement",
                    "code": dep["code"],
                    "label": dep["nom"],
                    "region": dep.get("region") or place["label"],
                }
                for dep in departements
                if dep.get("codeRegion") == place["code"]
            ]
        for target in targets:
            if stop_flag() or len(results_cache) >= max_results:
                break
            for company in queries:
                if stop_flag() or len(results_cache) >= max_results:
                    break
                for nature in NATURES:
                    if stop_flag() or len(results_cache) >= max_results:
                        break
                    detail(
                        f"{len(results_cache)} CE · {target['label']}"
                        + (f" · {company}" if company else "")
                    )
                    params = {
                        "nature_juridique": nature,
                        "per_page": 25,
                        "etat_administratif": "A",
                    }
                    if company:
                        params["q"] = company
                    if target["scope"] == "departement":
                        params["departement"] = target["code"]
                    elif target["scope"] == "commune":
                        params["code_commune"] = target["code"]
                        if target.get("departement"):
                            params["departement"] = target["departement"]
                    for record in _pages(params, stop_flag):
                        if stop_flag() or len(results_cache) >= max_results:
                            break
                        if record.get("statut_diffusion") == "N":
                            continue
                        if record.get("opposition_diffusion_commerciale") is True:
                            continue
                        if not _matches(record, target):
                            continue
                        siren = str(record.get("siren") or "")
                        if not siren or siren in seen:
                            continue
                        groupe = classify(record.get("nom_complet") or "")
                        if not groupe:
                            continue
                        if groupe != "Comité d'entreprise" and not include_social:
                            continue
                        seen.add(siren)
                        results_cache.append(_row(record, groupe, dep_names))
    kept = len(results_cache)
    detail(f"Terminé : {kept} organisme{'s' if kept > 1 else ''} trouvé{'s' if kept > 1 else ''} dans l'annuaire de l'État")
    return results_cache
