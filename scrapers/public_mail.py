"""
E-mails publics des organismes qui ne sont pas dans l'annuaire des mairies.

Sources : le site officiel (pages contact / culture, adresses masquées
par Cloudflare, PDF du site) et OpenStreetMap quand un contributeur
a publié contact:email ou un site.
"""
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from scrapers.mairies import (
    EMAIL_RE,
    USER_AGENT,
    _emails_from_html,
    _http_get,
    _junk_email,
    _norm,
)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PAGE_TOKENS = (
    ("annuaire", 100),
    ("contact", 90),
    ("arts-et-scene", 88),
    ("art-et-scene", 88),
    ("culture", 70),
    ("festival", 62),
    ("scene", 55),
    ("tourisme", 50),
    ("fete", 48),
    ("associ", 40),
)
PDF_KEEP = ("programme", "saison", "catalogue", "artset", "arts-et", "scene", "culture")
PDF_SKIP = ("accessibilite", "budget", "enquete", "marche", "deliberation")
LOCAL_GOOD = (
    "contact", "accueil", "info", "culture", "festival", "scene", "art",
    "animation", "tourisme", "communication", "spectacle", "mediatheque",
)
LOCAL_BAD = ("newsletter", "recrutement", "webmaster", "no-reply", "noreply", "dpo", "rgpd")
TYPE_PHRASES = {
    "Office de tourisme": ("office de tourisme", "office du tourisme", "office de tourisme"),
    "Comité des fêtes": ("comite des fetes", "comite de fete", "comite des fete"),
    "MJC": ("maison des jeunes", "mjc"),
    "Centre social": ("centre social", "centre socioculturel"),
    "Scène ou agence culturelle": ("scene nationale", "agence culturelle", "centre dramatique"),
    "Association culturelle": ("association culturelle",),
}


def _origin(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _same_host(url, origin):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    base = urlparse(origin).netloc.lower().removeprefix("www.")
    return bool(host) and host == base


def _page_score(url):
    path = _norm(urlparse(url).path)
    if not path or path.endswith((".css", ".js", ".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp", ".zip")):
        return 0
    if any(token in path for token in ("widget-", "/wp-content/", "/wp-includes/", "/node_modules/")):
        return 0
    score = 0
    for token, weight in PAGE_TOKENS:
        if token in path:
            score = max(score, weight)
    if "/agenda/" in path or "/actualite/" in path or "/aide/" in path:
        score = max(0, score - 50)
    if "/node/" in path:
        score = max(score, 35)
    return score


def _pdf_urls(page_url, html):
    found = []
    for match in re.finditer(r'(?:href|src)=["\']([^"\']+\.pdf[^"\']*)', html, re.I):
        absolute = urljoin(page_url, match.group(1)).split("#", 1)[0]
        folded = _norm(urllib.parse.unquote(absolute))
        if any(token in folded for token in PDF_SKIP):
            continue
        if not any(token in folded for token in PDF_KEEP):
            continue
        if absolute not in found:
            found.append(absolute)
    return found[:2]


def _emails_from_pdf(url):
    try:
        import fitz
    except ImportError:
        return []
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(6_000_000)
    except Exception:
        return []
    if not data.startswith(b"%PDF"):
        return []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return []
    found = []
    try:
        for index, page in enumerate(doc):
            if index > 12:
                break
            text = page.get_text() or ""
            for match in EMAIL_RE.finditer(text):
                email = match.group(0)
                if _junk_email(email):
                    continue
                start = max(0, match.start() - 160)
                end = min(len(text), match.end() + 80)
                found.append((email, text[start:end]))
    finally:
        doc.close()
    return found


def _local_rank(email):
    local = _norm(email.split("@", 1)[0])
    if any(token in local for token in LOCAL_BAD):
        return -8
    rank = 0
    for token in LOCAL_GOOD:
        if token in local:
            rank += 4
    return rank


SOCIAL_HOSTS = (
    "facebook.com", "instagram.com", "linkedin.com", "linktr.ee", "linktree.com",
)
CONTACT_HINTS = ("contact", "mail", "email", "courriel", "telephone", "accueil", "ecrire")


def _host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_social(url):
    host = _host(url)
    return any(host == name or host.endswith("." + name) for name in SOCIAL_HOSTS)


def _social_urls(page_url, html):
    found = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(page_url, href).split("?", 1)[0].split("#", 1)[0]
        if not _is_social(absolute):
            continue
        low = absolute.lower()
        if any(token in low for token in ("/sharer", "/share", "/dialog", "/intent", "l.facebook.com")):
            continue
        if absolute not in found:
            found.append(absolute)
    return found[:3]


def _login_wall(final_url, html):
    low = (final_url or "").lower()
    if any(token in low for token in ("/login", "/accounts/login", "authwall", "checkpoint")):
        return True
    title = ""
    match = re.search(r"<title>(.*?)</title>", html or "", re.I | re.S)
    if match:
        title = _norm(re.sub(r"\s+", " ", match.group(1)))
    return any(token in title for token in ("log in", "sign up", "connectez", "se connecter", "inscrivez"))


def _contactish(context):
    folded = _norm(context or "")
    return any(token in folded for token in CONTACT_HINTS)


def _phones_from_html(html):
    phones = []
    for match in re.finditer(r'href=["\']tel:([^"\']+)["\']', html or "", re.I):
        phone = urllib.parse.unquote(match.group(1)).split("?")[0].strip()
        phone = re.sub(r"\s+", " ", phone)
        if phone and phone not in phones:
            phones.append(phone)
    return phones


def read_public_profile(url, stop_flag=None):
    """
    Lit une page de profil déjà publique.
    Si le site demande une connexion, on ne va pas plus loin.
    """
    stop_flag = stop_flag or (lambda: False)
    if not url or not _is_social(url) or stop_flag():
        return "", "", []
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            final_url = resp.geturl()
            html = resp.read(400000).decode("utf-8", "replace")
    except Exception:
        return "", "", []
    if not html or _login_wall(final_url, html):
        return "", "", []
    mentions = []
    for email, context in _emails_from_html(html):
        if _junk_email(email):
            continue
        short = len((context or "").strip()) < 60
        if not short and not _contactish(context):
            continue
        mentions.append((email.strip(), context or "", final_url))
    if len(mentions) > 5:
        mentions = [item for item in mentions if _contactish(item[1])]
    phone = (_phones_from_html(html) or [""])[0]
    email = mentions[0][0] if mentions else ""
    return email, phone, mentions


def _exact_domain(email, origin):
    host = _host(origin)
    domain = email.split("@", 1)[-1].lower().removeprefix("www.")
    return bool(host) and domain == host


def crawl_site(start_url, stop_flag=None):
    """
    Retourne le meilleur e-mail publié sur le site, un second e-mail
    plus culturel s'il est différent, et les e-mails vus avec leur contexte.
    """
    stop_flag = stop_flag or (lambda: False)
    origin = _origin(start_url)
    if not origin:
        return "", "", []
    seeds = [start_url, origin + "/contact", origin + "/recherche?key=culture"]
    seen = set()
    mentions = []
    pdfs = []
    discovered = []
    socials = []
    phone = ""

    def absorb(page_url, html):
        nonlocal phone
        for email, context in _emails_from_html(html):
            if not _junk_email(email):
                mentions.append((email.strip(), context or "", page_url))
        if not phone:
            found_phones = _phones_from_html(html)
            if found_phones:
                phone = found_phones[0]
        for social in _social_urls(page_url, html):
            if social not in socials:
                socials.append(social)
        for pdf in _pdf_urls(page_url, html):
            if pdf not in pdfs:
                pdfs.append(pdf)
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            absolute = urljoin(page_url, href).split("#", 1)[0]
            score = _page_score(absolute)
            if score <= 0 or absolute in seen or not _same_host(absolute, origin):
                continue
            discovered.append((score, absolute))

    def open_page(url):
        if not url or url in seen or stop_flag():
            return
        seen.add(url)
        try:
            final_url, html = _http_get(url, timeout=12, max_bytes=500000)
        except Exception:
            return
        if html:
            absorb(final_url, html)

    for seed in seeds:
        open_page(seed)
    discovered.sort(key=lambda item: item[0], reverse=True)
    picked = []
    for _score, url in discovered:
        if url in seen or url in picked:
            continue
        picked.append(url)
        if len(picked) >= 4:
            break
    nodes = 0
    for _score, url in discovered:
        if "/node/" not in url or url in seen or url in picked:
            continue
        picked.append(url)
        nodes += 1
        if nodes >= 4:
            break
    before = len(discovered)
    for url in picked:
        if stop_flag():
            break
        open_page(url)
    hopped = 0
    for score, url in sorted(discovered[before:], key=lambda item: item[0], reverse=True):
        if stop_flag() or hopped >= 3 or score < 55 or url in seen:
            continue
        open_page(url)
        hopped += 1

    for pdf in pdfs[:2]:
        if stop_flag():
            break
        for email, context in _emails_from_pdf(pdf):
            mentions.append((email.strip(), context or "", pdf))
    for social in socials[:2]:
        if stop_flag():
            break
        social_email, social_phone, social_mentions = read_public_profile(social, stop_flag)
        mentions.extend(social_mentions)
        if social_phone and not phone:
            phone = social_phone

    ranked = []
    seen_emails = set()
    for email, context, page in mentions:
        key = email.casefold()
        if key in seen_emails:
            continue
        seen_emails.add(key)
        local = _norm(email.split("@", 1)[0])
        if any(token in local for token in ("archiv", "marche", "recrut", "newsletter", "webmaster", "rh")):
            continue
        social_page = _is_social(page)
        if not _exact_domain(email, origin):
            if not (social_page and _contactish(f"{context} {email}")):
                continue
            score = 9
        else:
            score = _local_rank(email) + 12
        folded = _norm(f"{context} {page}")
        cultural_page = any(token in folded for token in ("culture", "scene", "festival", "spectacle", "animation"))
        if cultural_page:
            score += 4
        if any(token in local for token in ("mediatheque", "bibliotheque")):
            score -= 3
        ranked.append((score, cultural_page, email))
    if not ranked:
        return "", "", mentions, phone
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = ranked[0][2]
    extra = ""
    for score, cultural_page, email in ranked[1:]:
        if cultural_page and email.casefold() != best.casefold():
            extra = email
            break
    return best, extra, mentions, phone


def _iso_code(dep_code):
    return "FR-" + str(dep_code).upper()


def _overpass(query):
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        OVERPASS_URL,
        data=body,
        headers={"User-Agent": "OraScrapInAll/1.0 (contact public)"},
    )
    with urllib.request.urlopen(req, timeout=14) as resp:
        import json
        return json.loads(resp.read().decode("utf-8", "replace"))


def load_osm_places(dep_code):
    iso = _iso_code(dep_code)
    query = (
        f'[out:json][timeout:12];area["ISO3166-2"="{iso}"]->.a;'
        '('
        'nwr["office"="tourism"](area.a);'
        'nwr["amenity"="community_centre"](area.a);'
        'nwr["amenity"="arts_centre"](area.a);'
        'nwr["amenity"="theatre"](area.a);'
        'nwr["office"="association"](area.a);'
        ');out tags;'
    )
    try:
        data = _overpass(query)
    except Exception:
        return []
    places = []
    seen = set()
    for element in data.get("elements") or []:
            tags = element.get("tags") or {}
            name = (tags.get("name") or "").strip()
            if not name:
                continue
            key = (_norm(name), tags.get("addr:city") or "")
            if key in seen:
                continue
            seen.add(key)
            places.append({
                "name": name,
                "city": tags.get("addr:city") or "",
                "email": (tags.get("contact:email") or tags.get("email") or "").strip(),
                "url": (tags.get("contact:website") or tags.get("website") or "").strip(),
                "phone": (tags.get("contact:phone") or tags.get("phone") or "").strip(),
                "social": [
                    value.strip()
                    for value in (
                        tags.get("contact:facebook"),
                        tags.get("facebook"),
                        tags.get("contact:instagram"),
                        tags.get("instagram"),
                        tags.get("contact:linkedin"),
                        tags.get("linkedin"),
                    )
                    if value and str(value).strip().startswith("http")
                ],
                "blob": _norm(
                    " ".join([
                        name,
                        tags.get("addr:city") or "",
                        tags.get("office") or "",
                        tags.get("amenity") or "",
                        tags.get("tourism") or "",
                    ])
                ),
            })
    return places


def _city_of(row):
    label = row.get("commune") or row.get("nom") or ""
    if " — " in label:
        return label.split(" — ", 1)[1].strip()
    return ""


def _kind_ok(groupe, blob):
    folded = _norm(groupe)
    if "tourisme" in folded:
        return "touris" in blob
    if "fete" in folded:
        return "fete" in blob or "comite" in blob
    if folded == "mjc":
        return "mjc" in blob or "jeune" in blob
    if "social" in folded:
        return "social" in blob or "community" in blob
    if "scene" in folded or "agence" in folded:
        return any(token in blob for token in ("scene", "theatre", "arts", "agence", "culture"))
    if "departemental" in folded:
        return "conseil" in blob and "departement" in blob
    if "regional" in folded:
        return "conseil" in blob and "region" in blob
    if "associ" in folded or "culturel" in folded:
        return "associ" in blob or "cultur" in blob
    return False


def _match_place(row, places):
    city = _norm(_city_of(row))
    label = _norm(row.get("commune") or "")
    best = None
    best_score = 0
    for place in places:
        if not _kind_ok(row.get("groupe") or "", place["blob"]):
            continue
        if city and city not in place["blob"] and city not in _norm(place["name"]):
            continue
        score = 1
        if city and city in place["blob"]:
            score += 3
        for token in label.split():
            if len(token) > 3 and token in place["blob"]:
                score += 1
        if place.get("email"):
            score += 1
        if score > best_score:
            best = place
            best_score = score
    return best if best_score >= 3 else None


def _contact_label(email):
    local = _norm((email or "").split("@", 1)[0])
    if "festival" in local:
        return "Festivals"
    if any(token in local for token in ("culturel", "culture", "scene", "art")):
        return "Service culturel"
    if "mediatheque" in local or "bibliotheque" in local:
        return "Médiathèque"
    return "Site de l'organisme"


def _apply_contact(row, email, label="", url="", phone=""):
    email = (email or "").strip()
    if email and not row.get("email_mairie") and not _junk_email(email):
        row["email_mairie"] = email
        row["email"] = email
        if label and not row.get("service_socioculturel"):
            row["service_socioculturel"] = label
    if url and not row.get("url"):
        row["url"] = url if url.startswith("http") else "https://" + url
    if phone and not row.get("phone"):
        row["phone"] = re.sub(r"\s+", " ", phone).strip()


def _phrase_hit(groupe, folded):
    for phrase in TYPE_PHRASES.get(groupe) or ():
        if phrase in folded:
            return True
    return False


def _assign_mentions(rows, mentions):
    used = set()
    for email, context, _page in mentions:
        key = email.casefold()
        if key in used or _junk_email(email):
            continue
        folded = _norm(f"{context} {email}")
        for row in rows:
            if row.get("groupe") == "Mairie" or row.get("email_mairie"):
                continue
            city = _norm(_city_of(row))
            if city and city not in folded:
                continue
            if not _phrase_hit(row.get("groupe") or "", folded):
                continue
            _apply_contact(row, email, "Publié sur une page du territoire")
            used.add(key)
            break


def _crawl_mairie_mentions(url, stop_flag):
    origin = _origin(url)
    if not origin:
        return []
    mentions = []
    try:
        final_url, html = _http_get(url, timeout=10, max_bytes=400000)
    except Exception:
        return []
    if not html:
        return []
    pages = [final_url]
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        absolute = urljoin(final_url, href).split("#", 1)[0]
        if not _same_host(absolute, origin):
            continue
        path = _norm(urlparse(absolute).path)
        if any(token in path for token in ("fete", "associ", "touris", "mjc", "social", "culture", "contact")):
            if absolute not in pages:
                pages.append(absolute)
        if len(pages) >= 4:
            break
    for page in pages:
        if stop_flag():
            break
        if page == final_url:
            current = html
            page_url = final_url
        else:
            try:
                page_url, current = _http_get(page, timeout=10, max_bytes=400000)
            except Exception:
                continue
        for email, context in _emails_from_html(current or ""):
            if not _junk_email(email):
                mentions.append((email, context, page_url))
    return mentions


def enrich_public_contacts(rows, dep_code, stop_flag, on_detail=None):
    """Complète e-mail, site et téléphone des lignes qui n'ont pas d'e-mail d'annuaire."""
    targets = [
        row for row in rows
        if row.get("groupe") != "Mairie" and not (row.get("email_mairie") or "").strip()
    ]
    if not targets or stop_flag():
        return 0

    def detail(message):
        if on_detail:
            on_detail(message)

    detail("E-mails publics : annuaire du territoire…")
    places = load_osm_places(dep_code)
    for row in targets:
        if stop_flag():
            break
        place = _match_place(row, places)
        if not place:
            continue
        _apply_contact(row, place.get("email") or "", "OpenStreetMap", place.get("url") or "", place.get("phone") or "")
        if place.get("social"):
            row.setdefault("_social", [])
            for link in place["social"]:
                if link not in row["_social"]:
                    row["_social"].append(link)

    to_crawl = [row for row in targets if row.get("url") and not row.get("email_mairie")]
    councils = [
        row for row in targets
        if not row.get("email_mairie") and "conseil" in _norm(row.get("groupe") or "") and row.get("url")
    ]
    for row in councils:
        if row not in to_crawl:
            to_crawl.append(row)

    done = 0
    if to_crawl and not stop_flag():
        detail(f"E-mails publics : {len(to_crawl)} site(s)…")
        pool = ThreadPoolExecutor(max_workers=4)
        futures = {pool.submit(crawl_site, row["url"], stop_flag): row for row in to_crawl}
        try:
            for future in as_completed(futures):
                if stop_flag():
                    break
                row = futures[future]
                done += 1
                try:
                    email, extra, mentions, phone = future.result()
                except Exception:
                    email, extra, mentions, phone = "", "", [], ""
                _apply_contact(row, email, _contact_label(email), phone=phone)
                if extra and extra.casefold() != (row.get("email_mairie") or "").casefold():
                    row["email_socioculturel"] = extra
                    row["service_socioculturel"] = row.get("service_socioculturel") or "Service culturel"
                _assign_mentions(targets, mentions)
                if done % 3 == 0 or email:
                    filled_now = sum(1 for item in targets if item.get("email_mairie"))
                    detail(f"E-mails publics : {filled_now} trouvé(s) · sites {done}/{len(to_crawl)}")
        finally:
            pool.shutdown(wait=not stop_flag(), cancel_futures=True)

    social_rows = [
        row for row in targets
        if not row.get("email_mairie") and row.get("_social")
    ]
    if social_rows and not stop_flag():
        detail(f"Profils publics : {len(social_rows)} page(s)…")
        for row in social_rows:
            if stop_flag():
                break
            for link in row.get("_social") or []:
                email, phone, mentions = read_public_profile(link, stop_flag)
                if email:
                    _apply_contact(row, email, "Profil public", phone=phone)
                    _assign_mentions(targets, mentions)
                    break
                if phone and not row.get("phone"):
                    row["phone"] = phone

    missing = [row for row in targets if not row.get("email_mairie") and row.get("code_insee")]
    missing.sort(key=lambda row: -(row.get("population") or 0))
    mairie_url = {}
    for row in rows:
        if row.get("groupe") == "Mairie" and row.get("url") and row.get("code_insee"):
            mairie_url.setdefault(str(row["code_insee"]), row["url"])
    urls = []
    for row in missing:
        url = mairie_url.get(str(row.get("code_insee") or ""))
        if url and url not in urls:
            urls.append(url)
    if urls and not stop_flag():
        detail(f"E-mails publics : pages des communes ({len(urls)})…")
        pool = ThreadPoolExecutor(max_workers=4)
        futures = [pool.submit(_crawl_mairie_mentions, url, stop_flag) for url in urls[:40]]
        try:
            for future in as_completed(futures):
                if stop_flag():
                    break
                try:
                    mentions = future.result()
                except Exception:
                    mentions = []
                _assign_mentions(targets, mentions)
        finally:
            pool.shutdown(wait=not stop_flag(), cancel_futures=True)

    for row in rows:
        row.pop("_social", None)
    return sum(1 for row in targets if row.get("email_mairie"))
