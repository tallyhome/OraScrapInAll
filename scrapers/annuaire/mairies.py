"""
Contacts des mairies de France.

L'e-mail et le téléphone de la mairie viennent de l'annuaire Service-public.
L'e-mail du service socioculturel est cherché sur le site de la mairie
(pages culture, animation, loisirs, contact), quand la commune le publie.
"""
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

ANNUAIRE_URL = (
    "https://api-lannuaire.service-public.gouv.fr/api/explore/v2.1/"
    "catalog/datasets/api-lannuaire-administration/records"
)
GEO_REGIONS = "https://geo.api.gouv.fr/regions?fields=nom,code"
GEO_DEPARTEMENTS = "https://geo.api.gouv.fr/departements?fields=nom,code,codeRegion"
USER_AGENT = "Mozilla/5.0 (compatible; OraScrapInAll/1.0)"
FREE_EMAIL_DOMAINS = {
    "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "gmail.com", "hotmail.fr",
    "hotmail.com", "outlook.fr", "outlook.com", "yahoo.fr", "yahoo.com",
    "laposte.net", "live.fr", "live.com", "icloud.com", "bbox.fr",
    "numericable.fr", "aol.com", "msn.com", "neuf.fr", "gmx.fr", "gmx.com",
    "protonmail.com", "proton.me",
}

_geo_cache = None

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
JUNK_EMAIL = (
    "exemple@", "example@", "noreply", "no-reply", "donotreply",
    "sentry.", "wixpress", "schema.org", "yourdomain", "domaine.com",
    "email@", "test@", "xxx@",
)
LOCAL_HINTS = (
    "culture", "socioculture", "socio-culture", "animation", "evenement",
    "loisir", "spectacle", "mediatheque", "bibliotheque", "conservatoire",
    "patrimoine", "musee", "theatre", "artistique", "mjc", "scene",
)
SERVICE_PHRASES = (
    ("service socioculturel", "Service socioculturel"),
    ("services socioculturels", "Service socioculturel"),
    ("socioculturel", "Service socioculturel"),
    ("socio-culturel", "Service socioculturel"),
    ("affaires culturelles", "Affaires culturelles"),
    ("service culturel", "Service culturel"),
    ("service culture", "Service culturel"),
    ("services culturels", "Service culturel"),
    ("direction de la culture", "Direction de la culture"),
    ("pole culture", "Pôle culture"),
    ("service animation", "Service animation"),
    ("vie culturelle", "Vie culturelle"),
    ("action culturelle", "Action culturelle"),
    ("animation du territoire", "Animation du territoire"),
    ("animation territoriale", "Animation du territoire"),
    ("developpement culturel", "Développement culturel"),
    ("politique culturelle", "Politique culturelle"),
    ("arts et scenes", "Arts et scènes"),
    ("arts scenes", "Arts et scènes"),
    ("agence culturelle", "Agence culturelle"),
)
LINK_SCORES = (
    ("socioculture", 100),
    ("socio-culture", 100),
    ("culture", 80),
    ("animation", 70),
    ("evenement", 60),
    ("loisir", 50),
    ("mediathe", 40),
    ("bibliothe", 40),
    ("spectacle", 40),
    ("artsetscenes", 90),
    ("arts-et-scenes", 90),
    ("scene", 75),
    ("conservatoire", 35),
    ("contact", 30),
    ("annuaire", 28),
    ("service", 18),
)


def _get_json(url, timeout=30):
    from scrapers.systeme.netguard import ensure_public_url
    ensure_public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json
        return json.load(resp)


def load_geo():
    """Régions et départements, mis en cache pour le processus."""
    global _geo_cache
    if _geo_cache is not None:
        return _geo_cache
    regions = _get_json(GEO_REGIONS)
    departements = _get_json(GEO_DEPARTEMENTS)
    region_name = {r["code"]: r["nom"] for r in regions}
    for dep in departements:
        dep["region"] = region_name.get(dep.get("codeRegion"), "")
    regions.sort(key=lambda r: r["nom"])
    departements.sort(key=lambda d: d["nom"])
    _geo_cache = (regions, departements)
    return _geo_cache


def _norm(text):
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    return folded.encode("ascii", "ignore").decode().lower()


def _clean_email(value):
    if not value:
        return ""
    if isinstance(value, list):
        first = value[0]
        value = first.get("valeur") if isinstance(first, dict) else str(first)
    text = str(value).strip()
    for part in re.split(r"[;\s,]+", text):
        part = part.strip()
        if EMAIL_RE.fullmatch(part):
            return part
    match = EMAIL_RE.search(text)
    return match.group(0) if match else ""


def _as_list(value):
    """L'annuaire renvoie parfois une liste, parfois cette liste encodée en texte."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                value = json.loads(text)
            except Exception:
                return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _first_value(items, key="valeur"):
    items = _as_list(items)
    if not items:
        return ""
    item = items[0]
    if isinstance(item, dict):
        return str(item.get(key) or "").strip()
    return str(item).strip()


def _guess_site_from_email(email):
    """Si l'annuaire n'a pas de site, le domaine de l'e-mail municipal en est souvent un."""
    if not email or "@" not in email:
        return ""
    domain = email.split("@", 1)[1].lower().strip(".")
    if domain in FREE_EMAIL_DOMAINS:
        return ""
    return "https://" + domain


def _pick_site(sites):
    sites = _as_list(sites)
    if not sites:
        return ""
    for site in sites:
        if isinstance(site, dict) and not (site.get("libelle") or "").strip():
            url = (site.get("valeur") or "").strip()
            if url.startswith("http"):
                return url
    for site in sites:
        if isinstance(site, dict):
            url = (site.get("valeur") or "").strip()
            if url.startswith("http"):
                return url
    return ""


def _format_address(adresse):
    rows = _as_list(adresse)
    if not rows or not isinstance(rows[0], dict):
        return ""
    adresse = rows[0]
    parts = [
        adresse.get("numero_voie") or "",
        adresse.get("code_postal") or "",
        adresse.get("nom_commune") or "",
    ]
    return " ".join(p.strip() for p in parts if p and str(p).strip())


def _annuaire_page(where, offset, limit=100):
    params = urllib.parse.urlencode({
        "where": where,
        "limit": limit,
        "offset": offset,
    })
    data = _get_json(f"{ANNUAIRE_URL}?{params}")
    return data.get("results") or []


def _fetch_records(where, stop_flag):
    offset = 0
    records = []
    while True:
        if stop_flag():
            break
        batch = _annuaire_page(where, offset)
        if not batch:
            break
        records.extend(batch)
        offset += len(batch)
        if len(batch) < 100:
            break
    return records


def _fetch_mairie_records(dep_code, stop_flag):
    where = f'search(pivot, "mairie") and startswith(code_insee_commune, "{dep_code}")'
    return _fetch_records(where, stop_flag)


def _fetch_epci_records(dep_code, stop_flag):
    where = f'search(pivot, "epci") and startswith(code_insee_commune, "{dep_code}")'
    return _fetch_records(where, stop_flag)


def _dedupe_epci(records, communes):
    by_key = {}
    for record in records:
        insee = str(record.get("code_insee_commune") or "").strip()
        code_epci = str((communes.get(insee) or {}).get("codeEpci") or "")
        key = code_epci or _norm(record.get("nom"))
        if not key:
            continue
        current = by_key.get(key)
        if current is None or _record_rank(record) > _record_rank(current):
            by_key[key] = record
    return list(by_key.values())


def _record_rank(record):
    nom = _norm(record.get("nom"))
    score = 0
    if record.get("adresse_courriel"):
        score += 10
    if nom.startswith("mairie"):
        score += 3
    if "centrale" in nom:
        score += 2
    if "annexe" in nom or "quartier" in nom:
        score -= 5
    return score


def _dedupe_mairies(records):
    by_insee = {}
    for record in records:
        code = str(record.get("code_insee_commune") or "").strip()
        if not code:
            continue
        current = by_insee.get(code)
        if current is None or _record_rank(record) > _record_rank(current):
            by_insee[code] = record
    return list(by_insee.values())


def _load_communes(dep_code):
    url = (
        "https://geo.api.gouv.fr/communes?codeDepartement="
        + urllib.parse.quote(dep_code)
        + "&fields=nom,code,codesPostaux,population,codeEpci&format=json"
    )
    try:
        rows = _get_json(url)
    except Exception:
        return {}
    return {str(row.get("code")): row for row in rows}


def _load_epcis(dep_code):
    url = (
        "https://geo.api.gouv.fr/epcis?codeDepartement="
        + urllib.parse.quote(dep_code)
        + "&fields=nom,code,population"
    )
    try:
        rows = _get_json(url)
    except Exception:
        return {}
    return {str(row.get("code")): row for row in rows if row.get("code")}


def _structure_type(nom):
    folded = _norm(nom)
    if "communaute de communes" in folded:
        return "Communauté de communes"
    if "agglomeration" in folded or "agglo" in folded:
        return "Communauté d'agglomération"
    if "metropole" in folded:
        return "Métropole"
    if "communaute urbaine" in folded:
        return "Communauté urbaine"
    return "Intercommunalité"


def _structure_name(nom):
    text = (nom or "").strip()
    if " - " in text:
        return text.split(" - ", 1)[1].strip() or text
    return text


def _postal_code(record, commune):
    codes = (commune or {}).get("codesPostaux") or []
    if codes:
        return ", ".join(codes)
    rows = _as_list(record.get("adresse"))
    if rows and isinstance(rows[0], dict):
        return str(rows[0].get("code_postal") or "")
    return ""


def _population(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _base_row(name, groupe, email, record, commune, departement, population):
    return {
        "nom": name,
        "commune": name,
        "code_postal": _postal_code(record, commune),
        "code_insee": str(record.get("code_insee_commune") or ""),
        "population": population,
        "departement": departement.get("nom") or "",
        "code_departement": departement.get("code") or "",
        "region": departement.get("region") or "",
        "groupe": groupe,
        "email": email,
        "email_mairie": email,
        "email_socioculturel": "",
        "service_socioculturel": "",
        "phone": re.sub(r"\s+", " ", _first_value(record.get("telephone"))).strip(),
        "url": _pick_site(record.get("site_internet")) or _guess_site_from_email(email),
        "adresse": _format_address(record.get("adresse")),
        "source": "mairie",
    }


def _row_from_record(record, commune, departement):
    email = _clean_email(record.get("adresse_courriel"))
    geo_name = (commune or {}).get("nom") or ""
    raw_name = record.get("nom") or ""
    commune_name = geo_name or re.sub(r"^Mairie\s*[-–]\s*", "", raw_name).strip() or raw_name
    return _base_row(
        commune_name,
        "Mairie",
        email,
        record,
        commune,
        departement,
        _population((commune or {}).get("population")),
    )


def _row_from_epci(record, commune, departement, epcis):
    email = _clean_email(record.get("adresse_courriel"))
    code_epci = str((commune or {}).get("codeEpci") or "")
    geo = epcis.get(code_epci) or {}
    raw_name = record.get("nom") or ""
    name = geo.get("nom") or _structure_name(raw_name) or raw_name
    population = _population(geo.get("population"))
    if population is None:
        population = _population((commune or {}).get("population"))
    return _base_row(name, _structure_type(raw_name), email, record, commune, departement, population)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        self._href = dict(attrs).get("href") or ""
        self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._buf).strip()))
            self._href = None
            self._buf = []


def _http_get(url, timeout=7, max_bytes=350000):
    from scrapers.systeme.netguard import ensure_public_url
    ensure_public_url(url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("content-type") or "").lower()
        final = resp.geturl()
        if ctype and "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            return final, ""
        raw = resp.read(max_bytes)
    return final, raw.decode("utf-8", "replace")


def _strip_html(html):
    text = re.sub(r"(?is)<script\b.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style\b.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _junk_email(email):
    low = email.lower()
    if any(flag in low for flag in JUNK_EMAIL):
        return True
    local = low.split("@", 1)[0]
    if "agriculture" in local and "culture" not in local.replace("agriculture", ""):
        return True
    return False


def _local_score(email):
    local = _norm(email.split("@", 1)[0])
    if "agriculture" in local:
        local = local.replace("agriculture", "")
    score = 0
    for hint in LOCAL_HINTS:
        if hint in local:
            score += 5
    return score


def _phrase_hit(context):
    folded = _norm(context)
    for phrase, label in SERVICE_PHRASES:
        if _norm(phrase) in folded:
            return 8, label
    return 0, ""


def _same_domain(email, site_url):
    host = urlparse(site_url).netloc.lower().removeprefix("www.")
    domain = email.split("@", 1)[-1].lower().removeprefix("www.")
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain) or domain.endswith("." + host)


def _candidate_pages(page_url, html, limit=4):
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    host = urlparse(page_url).netloc.lower().removeprefix("www.")
    ranked = []
    seen = set()
    for href, text in parser.links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower().removeprefix("www.") != host:
            continue
        path = (parsed.path or "").lower()
        if path.endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx")):
            continue
        blob = _norm(path + " " + text)
        score = 0
        for token, weight in LINK_SCORES:
            if token in blob:
                score = max(score, weight)
        if score <= 0:
            continue
        clean = absolute.split("#", 1)[0]
        if clean in seen:
            continue
        seen.add(clean)
        ranked.append((score, clean))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in ranked[:limit]]


def _decode_cfemail(encoded):
    """Adresse protégée par Cloudflare (attribut data-cfemail)."""
    try:
        raw = bytes.fromhex(encoded)
    except ValueError:
        return ""
    if len(raw) < 2:
        return ""
    key = raw[0]
    email = bytes(byte ^ key for byte in raw[1:]).decode("utf-8", "replace").strip()
    return email if EMAIL_RE.fullmatch(email) else ""


def _emails_from_html(html):
    found = []
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:
        parser.links = []
    for href, text in parser.links:
        if href.lower().startswith("mailto:"):
            email = urllib.parse.unquote(href.split(":", 1)[1].split("?", 1)[0]).strip()
            if EMAIL_RE.fullmatch(email):
                found.append((email, text or email))
    for match in re.finditer(r'data-cfemail="([0-9a-fA-F]+)"', html):
        email = _decode_cfemail(match.group(1))
        if not email:
            continue
        start = max(0, match.start() - 160)
        end = min(len(html), match.end() + 80)
        found.append((email, _strip_html(html[start:end])))
    text = _strip_html(html)
    for match in EMAIL_RE.finditer(text):
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 80)
        found.append((match.group(0), text[start:end]))
    return found


def find_sociocultural_email(site_url, mairie_email=""):
    """
    Retourne (email, libellé) du service socioculturel publié sur le site,
    ou ("", "") si rien de distinct n'est publié.
    """
    if not site_url:
        return "", ""
    try:
        final_url, html = _http_get(site_url)
    except Exception:
        return "", ""
    if not html:
        return "", ""

    pages = [(final_url, html, False)]
    for extra in _candidate_pages(final_url, html):
        try:
            extra_url, extra_html = _http_get(extra)
        except Exception:
            continue
        if extra_html:
            cultural = _norm(extra_url)
            pages.append((
                extra_url,
                extra_html,
                any(token in cultural for token in ("culture", "socio", "animation", "loisir", "evenement", "spectacle")),
            ))

    best = None
    mairie_key = (mairie_email or "").casefold()
    for page_url, html, cultural_page in pages:
        pairs = _emails_from_html(html)
        if len(pairs) > 25:
            # Annuaire fourre-tout : on ne garde que les adresses dont le nom évoque la culture.
            pairs = [(email, ctx) for email, ctx in pairs if _local_score(email) >= 5]
        for email, context in pairs:
            if _junk_email(email):
                continue
            local = _local_score(email)
            phrase, label = _phrase_hit(context)
            if local <= 0 and phrase <= 0:
                continue
            score = local + phrase
            if cultural_page:
                score += 2
            if mairie_key and email.casefold() == mairie_key and score < 8:
                continue
            if score < 5:
                continue
            if not label and local:
                label = "Service culturel"
            domain_bonus = 1 if _same_domain(email, page_url) else 0
            candidate = (score, domain_bonus, email, label)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if not best:
        return "", ""
    return best[2], best[3]


def _departements_for(region_code, departement_code, all_france=False):
    _, departements = load_geo()
    if all_france:
        rows = list(departements)
        rows.sort(key=lambda dep: ((dep.get("region") or ""), dep.get("nom") or ""))
        return rows
    if departement_code:
        return [d for d in departements if d["code"] == departement_code]
    if region_code:
        return [d for d in departements if d.get("codeRegion") == region_code]
    return []


def _is_department_council(nom):
    folded = _norm(nom)
    if not folded.startswith("conseil departemental -"):
        return False
    return "ordre" not in folded and "acces au droit" not in folded


def _is_region_council(nom):
    folded = _norm(nom)
    if not folded.startswith("conseil regional -"):
        return False
    return "ordre" not in folded


def _council_row(record, communes, departement, groupe, piste):
    email = _clean_email(record.get("adresse_courriel"))
    insee = str(record.get("code_insee_commune") or "")
    name = (record.get("nom") or "").strip()
    row = _base_row(name, groupe, email, record, communes.get(insee), departement, None)
    row["recherche_url"] = piste(name, "", groupe)
    if groupe == "Conseil régional":
        row["departement"] = "Région"
        row["code_departement"] = ""
    return row


def _passes_population(row, min_population):
    if not min_population:
        return True
    population = row.get("population")
    if population is None:
        return True
    return population >= min_population


def collect_mairies(region_code, departement_code, results_cache, stop_flag, scan_sites=True, max_results=0, on_detail=None, all_france=False, min_population=0, kinds=None, skip_codes=None, on_department=None):
    """
    Remplit results_cache avec une ligne par commune.
    max_results <= 0 : pas de plafond.
    """
    from scrapers.annuaire.organisations import fetch_organisations, piste_url

    selected = set(kinds or ["mairie", "epci"])
    regional_records = []
    if "cr" in selected:
        regional_records = _fetch_records('nom like "Conseil régional -%"', stop_flag)
    regions_done = set()
    skip = {str(code) for code in (skip_codes or [])}
    departements = _departements_for(region_code, departement_code, all_france=all_france)
    if not departements:
        raise ValueError("Région ou département introuvable")

    def detail(message):
        if on_detail:
            on_detail(message)

    socio_found = 0
    for departement in departements:
        if stop_flag():
            break
        code = departement["code"]
        if code in skip:
            region_name = departement.get("region") or ""
            if region_name:
                regions_done.add(region_name)
            continue
        detail(f"Annuaire {departement['nom']}…")
        communes = _load_communes(code)
        epcis = _load_epcis(code) if "epci" in selected else {}
        rows = []
        if "mairie" in selected:
            records = _dedupe_mairies(_fetch_mairie_records(code, stop_flag))
            rows.extend(
                _row_from_record(record, communes.get(str(record.get("code_insee_commune"))), departement)
                for record in records
            )
        if "epci" in selected:
            for record in _dedupe_epci(_fetch_epci_records(code, stop_flag), communes):
                insee = str(record.get("code_insee_commune") or "")
                row = _row_from_epci(record, communes.get(insee), departement, epcis)
                row["recherche_url"] = piste_url(row["commune"], "", row["groupe"])
                rows.append(row)
        if "cd" in selected:
            where = f'nom like "Conseil départemental -%" and startswith(code_insee_commune, "{code}")'
            for record in _fetch_records(where, stop_flag):
                if _is_department_council(record.get("nom") or ""):
                    rows.append(_council_row(record, communes, departement, "Conseil départemental", piste_url))
        region_name = departement.get("region") or ""
        if "cr" in selected and region_name and region_name not in regions_done:
            regions_done.add(region_name)
            for record in regional_records:
                nom = record.get("nom") or ""
                if not _is_region_council(nom):
                    continue
                label = nom.split(" - ", 1)[1].strip() if " - " in nom else ""
                if _norm(label) == _norm(region_name):
                    rows.append(_council_row(record, communes, departement, "Conseil régional", piste_url))
        if selected & {"ot", "fete", "asso", "ce", "prod", "scene"}:
            detail(f"{departement['nom']} : offices, comités, scènes…")
            rows.extend(fetch_organisations(code, departement, communes, selected, stop_flag))
        rows = [row for row in rows if _passes_population(row, min_population)]
        from scrapers.annuaire.public_mail import enrich_public_contacts
        enrich_public_contacts(rows, code, stop_flag, detail)
        rows.sort(key=lambda row: (
            0 if row.get("groupe") == "Mairie" else 1,
            -(row.get("population") or 0),
            row.get("commune") or "",
        ))
        if max_results and len(results_cache) + len(rows) > max_results:
            rows = rows[: max(0, max_results - len(results_cache))]
        results_cache.extend(rows)

        def mairie_total():
            return sum(1 for row in results_cache if row.get("groupe") == "Mairie")

        detail(f"{len(results_cache)} fiches dont {mairie_total()} mairies · {departement['nom']}")

        if scan_sites and rows:
            pending = [row for row in rows if row.get("url")]
            done = 0
            pool = ThreadPoolExecutor(max_workers=6)
            futures = {
                pool.submit(find_sociocultural_email, row["url"], row.get("email_mairie") or ""): row
                for row in pending
            }
            try:
                for future in as_completed(futures):
                    if stop_flag():
                        break
                    row = futures[future]
                    done += 1
                    try:
                        email, label = future.result()
                    except Exception:
                        email, label = "", ""
                    if email:
                        row["email_socioculturel"] = email
                        row["service_socioculturel"] = label
                        socio_found += 1
                    if done % 5 == 0 or email:
                        detail(
                            f"{len(results_cache)} fiches dont {mairie_total()} mairies"
                            f" · visite des sites {done}/{len(pending)}"
                            f" · {socio_found} e-mail{'s' if socio_found > 1 else ''} socioculturel{'s' if socio_found > 1 else ''}"
                        )
            finally:
                pool.shutdown(wait=not stop_flag(), cancel_futures=True)
        if on_department and not stop_flag():
            on_department(code)
        if stop_flag():
            break
        if max_results and len(results_cache) >= max_results:
            break

    detail(
        f"Terminé : {len(results_cache)} fiches dont {sum(1 for row in results_cache if row.get('groupe') == 'Mairie')} mairies"
        f" · {socio_found} e-mail{'s' if socio_found > 1 else ''} socioculturel{'s' if socio_found > 1 else ''}"
    )
    return results_cache
