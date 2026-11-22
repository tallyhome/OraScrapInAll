"""
Recherche de CE / CSE sur Internet.

Les moteurs (Google, Bing, DuckDuckGo) sont interrogés avec des requêtes
ciblées : sites web, pages Facebook, profils Instagram, pages LinkedIn.
Chaque résultat pertinent est ensuite lu tel qu'il est publié :
  - l'extrait du moteur (souvent un e-mail ou un téléphone),
  - la page publique (site de l'entreprise ou du CE, meta-données Facebook/Instagram),
  - la page contact d'un site dédié au CE.

Limites volontaires :
  - aucune connexion à un réseau social : si la page demande un compte, on
    garde le lien mais pas plus loin ;
  - aucun contournement de captcha : si un moteur en réclame un, il est
    abandonné pour la suite (en mode « Voir Chrome », on attend que
    l'utilisateur le résolve) ;
  - les profils personnels (LinkedIn /in/, comptes de particuliers) sont écartés.
"""
import base64
import html as html_lib
import random
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urlparse

from selenium.webdriver.common.by import By

from scrapers.annuaire.mairies import EMAIL_RE, _emails_from_html, _http_get, _junk_email, _norm
from scrapers.annuaire.public_mail import _contactish, _login_wall, _phones_from_html
from scrapers.systeme.netguard import ensure_public_url

NETWORKS = {
    "web": "Site web",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
}
NETWORK_ORDER = ("web", "facebook", "instagram", "linkedin")
API_ENGINES = ("brave_api", "google_api")
ENGINE_ORDER = ("brave_api", "google_api", "google", "bing", "duckduckgo")
ENGINE_LABELS = {
    "brave_api": "Brave (API)",
    "google_api": "Google (API)",
    "google": "Google",
    "bing": "Bing",
    "duckduckgo": "DuckDuckGo",
}

CE_TERMS = '("comité social et économique" OR "comité d\'entreprise" OR CSE)'
MAX_TASKS = 120
PER_SEARCH = 15
ACCEPT_SCORE = 6

SOCIAL_SITE = {
    "facebook": "facebook.com",
    "instagram": "instagram.com",
    "linkedin": "linkedin.com/company",
}

EXCLUDED_HOSTS = (
    "google.", "bing.com", "duckduckgo.com", "microsoft.com", "yahoo.com", "youtube.com",
    "wikipedia.org", "pagesjaunes.fr", "societe.com", "pappers.fr", "infogreffe.fr",
    "manageo.fr", "verif.com", "kompass.com", "indeed.", "glassdoor.", "welcometothejungle.com",
    "service-public.fr", "legifrance.gouv.fr", "travail-emploi.gouv.fr", "code.travail.gouv.fr",
    "lemonde.fr", "lefigaro.fr", "leparisien.fr", "ouest-france.fr", "francetvinfo.fr",
    "liberation.fr", "twitter.com", "x.com", "tiktok.com", "pinterest.", "reddit.com",
    "doctrine.fr", "juritravail.com", "village-justice.com", "jobteaser.com", "apec.fr",
    "francetravail.fr", "pole-emploi.fr", "cairn.info", "scribd.com", "slideshare.net",
)
SKIP_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4")
FB_RESERVED = {
    "login", "sharer", "sharer.php", "share", "dialog", "plugins", "watch", "events", "photo",
    "photos", "videos", "reel", "reels", "hashtag", "public", "search", "marketplace", "story.php",
    "permalink.php", "help", "policies", "privacy", "recover", "l.php", "tr", "ads", "business",
    "gaming", "stories", "login.php",
}
IG_RESERVED = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "directory", "web", "about", "legal"}

CE_RE = re.compile(
    r"comites?\s+(?:social|d\W{0,2}\s?entreprise|d\W{0,2}\s?etablissement|central|inter-?entreprise)"
    r"|\bc\.?s\.?e\b|\bc\.e\b|oeuvres?\s+sociales?|amicale\s+du\s+personnel"
)
CE_LOCAL_RE = re.compile(r"(?:^|[^a-z])(?:cse|ce|comite|cos|amicale)(?:[^a-z]|$)|cse|comite")
NOISE_TITLE_RE = re.compile(
    r"election|formation|expert[- ]comptable|avocat|logiciel|offre d.?emploi|recrutement|"
    r"definition|role du|budget du|c'est quoi|comment |qu.?est-ce|billetterie pas cher|"
    r"modele de|guide du|cours |tuto|webinaire"
)
LOCAL_BAD = ("newsletter", "recrut", "webmaster", "noreply", "no-reply", "dpo", "rgpd", "abuse", "privacy")
PHONE_RE = re.compile(r"(?:\+33\s?|0033\s?|0)[1-9](?:[\s.\-]?\d{2}){4}")
META_RE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:description|description|og:title|twitter:description)[\"'][^>]*>",
    re.I,
)
CONTENT_RE = re.compile(r"content=[\"']([^\"']*)[\"']", re.I)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BLOCK_TOKENS = (
    "trafic inhabituel", "unusual traffic", "/sorry/", "captcha", "are you a robot",
    "not a robot", "pas un robot", "verifiez que vous etes", "verify you are human",
    "anomaly", "etes-vous un robot", "bots use duckduckgo", "made by a human",
    "n'etes pas un robot", "automated queries",
)

JS_RESULTS = {
    "google": """
        const out=[]; const seen=new Set();
        document.querySelectorAll('a h3').forEach(h=>{
          const a=h.closest('a'); if(!a||!a.href||seen.has(a.href)) return; seen.add(a.href);
          let box=a;
          for(let i=0;i<6&&box.parentElement;i++){ box=box.parentElement; if((box.innerText||'').length>140) break; }
          out.push({url:a.href,title:h.innerText||'',text:(box.innerText||'').slice(0,600)});
        });
        return out;""",
    "bing": """
        return Array.from(document.querySelectorAll('li.b_algo')).map(li=>{
          const h=li.querySelector('h2'); const a=li.querySelector('h2 a');
          return a?{url:a.href,title:((h&&h.textContent)||a.textContent||'').trim(),
                    text:((li.innerText||li.textContent)||'').slice(0,600)}:null;
        }).filter(Boolean);""",
    "duckduckgo": """
        return Array.from(document.querySelectorAll('div.result')).map(r=>{
          const a=r.querySelector('a.result__a'); if(!a) return null;
          const s=r.querySelector('.result__snippet');
          return {url:a.href,title:a.innerText||'',text:((s?s.innerText:r.innerText)||'').slice(0,600)};
        }).filter(Boolean);""",
}


def _fold(text):
    return _norm((text or "").replace("œ", "oe").replace("Œ", "oe"))


def is_ce_text(text):
    return bool(CE_RE.search(_fold(text)))


# ---------------------------------------------------------------- requêtes

def build_queries(companies, places, networks):
    """Liste de (entreprise, lieu, réseau, requête)."""
    companies = [c.strip() for c in (companies or []) if c and c.strip()] or [""]
    places = [p.strip() for p in (places or []) if p and p.strip()] or [""]
    tasks = []
    for company in companies:
        base = f'"{company}" ' if company else ""
        for place in places:
            where = f" {place}" if place else ""
            for network in networks:
                if network == "web":
                    tasks.append((company, place, network, f"{base}{CE_TERMS}{where} contact email".strip()))
                    if not company:
                        tasks.append((company, place, network,
                                      f'{CE_TERMS}{where} ("oeuvres sociales" OR "amicale du personnel" OR "billetterie")'.strip()))
                else:
                    site = SOCIAL_SITE[network]
                    tasks.append((company, place, network, f"site:{site} {base}{CE_TERMS}{where}".strip()))
    return tasks


def _engine_url(engine, query, limit):
    q = urllib.parse.quote_plus(query)
    if engine == "google":
        return f"https://www.google.fr/search?q={q}&hl=fr&gl=fr&num={min(limit, 30)}"
    if engine == "bing":
        return f"https://www.bing.com/search?q={q}&setlang=fr&cc=FR&count={min(limit, 30)}"
    return f"https://html.duckduckgo.com/html/?q={q}&kl=fr-fr"


# ---------------------------------------------------------------- liens de résultats

def _unwrap_redirect(href):
    """Rend l'adresse réelle derrière les liens de redirection des moteurs."""
    try:
        parsed = urlparse(href)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    if host.endswith("bing.com") and parsed.path.startswith("/ck/"):
        raw = (query.get("u") or [""])[0]
        if raw.startswith("a1"):
            raw = raw[2:]
        try:
            raw += "=" * (-len(raw) % 4)
            return base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
        except Exception:
            return ""
    if host.endswith("duckduckgo.com") and parsed.path.startswith("/l"):
        return (query.get("uddg") or [""])[0]
    if "google." in host and parsed.path == "/url":
        return (query.get("q") or query.get("url") or [""])[0]
    return href


def _host(url):
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def accept_url(raw_url, networks):
    """Retourne (réseau, adresse canonique) ou None."""
    url = _unwrap_redirect(raw_url or "")
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower().removeprefix("www.")
    path = parsed.path or "/"
    segments = [s for s in path.split("/") if s]

    if host == "facebook.com" or host.endswith(".facebook.com"):
        if "facebook" not in networks or not segments:
            return None
        first = segments[0].lower()
        if first in FB_RESERVED:
            return None
        if first == "profile.php":
            ident = (parse_qs(parsed.query).get("id") or [""])[0]
            return ("facebook", f"https://www.facebook.com/profile.php?id={ident}") if ident else None
        if first == "pages" and len(segments) >= 3:
            return "facebook", "https://www.facebook.com/" + "/".join(segments[:3])
        if first == "groups" and len(segments) >= 2:
            return "facebook", "https://www.facebook.com/" + "/".join(segments[:2])
        return "facebook", f"https://www.facebook.com/{segments[0]}"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        if "instagram" not in networks or not segments or segments[0].lower() in IG_RESERVED:
            return None
        return "instagram", f"https://www.instagram.com/{segments[0]}/"
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        if "linkedin" not in networks or len(segments) < 2 or segments[0].lower() != "company":
            return None
        return "linkedin", f"https://www.linkedin.com/company/{segments[1]}/"

    if "web" not in networks:
        return None
    if any(token in host for token in EXCLUDED_HOSTS):
        return None
    if path.lower().endswith(SKIP_EXT):
        return None
    return "web", urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def clean_title(title, url=""):
    text = html_lib.unescape(title or "").strip()
    text = re.sub(r"\s*\(@[^)]*\).*$", "", text)
    text = re.sub(r"\s*[|•·–—-]\s*(?:facebook|instagram|linkedin).*$", "", text, flags=re.I)
    text = re.sub(r"\s+-\s+(?:home|accueil|about|à propos|a propos|posts?|publications?|photos?).*$", "", text, flags=re.I)
    if len(text) > 70:
        for sep in (" | ", " - ", " – ", " : "):
            if sep in text:
                head = text.split(sep, 1)[0].strip()
                if len(head) >= 4:
                    text = head
                    break
    text = re.sub(r"\s+", " ", text).strip(" -|•·")
    if len(text) >= 3:
        return text[:120]
    host = _host(url)
    return re.sub(r"\.[a-z]{2,}$", "", host).replace("-", " ").title() or "CE"


# ---------------------------------------------------------------- moteurs

def _blocked(driver):
    try:
        url = (driver.current_url or "").lower()
        body = _fold(driver.find_element(By.TAG_NAME, "body").text[:2500])
    except Exception:
        return False
    return any(token in url or token in body for token in BLOCK_TOKENS)


def _accept_cookies(driver):
    for selector in (
        "//button[@id='L2AGLb']",
        "//button[contains(., 'Tout accepter')]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'Accepter')]",
    ):
        try:
            driver.find_element(By.XPATH, selector).click()
            time.sleep(0.5)
            return
        except Exception:
            continue


def run_engine(driver, engine, query, limit, interactive, stop_flag, log, dead):
    """Une recherche dans un moteur. Un moteur bloqué est mis de côté."""
    try:
        driver.get(_engine_url(engine, query, limit))
    except Exception as exc:
        log(f"  ⚠ {ENGINE_LABELS[engine]} inaccessible : {exc}")
        return []
    time.sleep(1.2)
    if engine == "google":
        _accept_cookies(driver)
        time.sleep(0.4)

    def read():
        try:
            return driver.execute_script(JS_RESULTS[engine]) or []
        except Exception:
            return []

    rows = read()
    if not rows and _blocked(driver):
        if interactive:
            log(f"  ⏸ {ENGINE_LABELS[engine]} demande une vérification. Résolvez-la dans Chrome (2 min max).")
            deadline = time.time() + 120
            while time.time() < deadline and not stop_flag() and _blocked(driver):
                time.sleep(2)
            rows = read()
        if not rows:
            dead.add(engine)
            log(f"  ⛔ {ENGINE_LABELS[engine]} bloque les recherches automatiques : abandonné pour cette session.")
            return []
    hits = []
    seen = set()
    for row in rows[:limit]:
        url = row.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        hits.append({"url": url, "title": row.get("title") or "", "text": row.get("text") or ""})
    return hits


def engine_ready(engine, api_keys):
    """Les moteurs API demandent une clé enregistrée dans les Paramètres."""
    keys = api_keys or {}
    if engine == "brave_api":
        return bool(keys.get("brave"))
    if engine == "google_api":
        return bool(keys.get("google") and keys.get("google_cx"))
    return True


def _strip_tags(text):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _api_json(url, headers=None):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "OraScrapInAll/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        import json
        return json.loads(resp.read().decode("utf-8", "replace"))


def api_search(engine, query, limit, api_keys, log, dead):
    """Recherche par API officielle : l'opérateur site: est respecté, aucun captcha."""
    import json
    import urllib.error

    keys = api_keys or {}
    try:
        if engine == "brave_api":
            params = urllib.parse.urlencode({"q": query, "count": min(limit, 20), "country": "fr", "search_lang": "fr"})
            data = _api_json(
                "https://api.search.brave.com/res/v1/web/search?" + params,
                {"X-Subscription-Token": keys.get("brave", "")},
            )
            rows = [
                {"url": r.get("url") or "", "title": _strip_tags(r.get("title")), "text": _strip_tags(r.get("description"))}
                for r in ((data.get("web") or {}).get("results") or [])
            ]
        else:
            params = urllib.parse.urlencode({
                "key": keys.get("google", ""), "cx": keys.get("google_cx", ""), "q": query,
                "num": min(limit, 10), "hl": "fr", "gl": "fr", "lr": "lang_fr",
            })
            data = _api_json("https://www.googleapis.com/customsearch/v1?" + params)
            rows = [
                {"url": r.get("link") or "", "title": _strip_tags(r.get("title")), "text": _strip_tags(r.get("snippet"))}
                for r in (data.get("items") or [])
            ]
    except urllib.error.HTTPError as exc:
        reason = ""
        try:
            reason = json.loads(exc.read().decode("utf-8", "replace")).get("error", {})
            reason = reason.get("message") if isinstance(reason, dict) else str(reason)
        except Exception:
            pass
        if exc.code in (401, 403, 429):
            dead.add(engine)
            log(f"  ⛔ {ENGINE_LABELS[engine]} : clé refusée ou quota atteint ({exc.code}) {reason or ''}".rstrip())
        else:
            log(f"  ⚠ {ENGINE_LABELS[engine]} : erreur {exc.code} {reason or ''}".rstrip())
        return []
    except Exception as exc:
        log(f"  ⚠ {ENGINE_LABELS[engine]} injoignable : {exc}")
        return []
    return [row for row in rows if row["url"]][:limit]


# ---------------------------------------------------------------- lecture des pages

def _deobfuscate(text):
    text = re.sub(r"\s*[\[\(\{]\s*(?:at|arobase)\s*[\]\)\}]\s*", "@", text or "", flags=re.I)
    return re.sub(r"\s*[\[\(\{]\s*(?:dot|point)\s*[\]\)\}]\s*", ".", text, flags=re.I)


def _emails_in_text(text):
    found = []
    for match in EMAIL_RE.finditer(_deobfuscate(text)):
        email = match.group(0).strip(".")
        low = email.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")):
            continue
        if _junk_email(email) or email in found:
            continue
        found.append(email)
    return found


def _phones_in_text(text):
    phones = []
    for match in PHONE_RE.finditer(text or ""):
        phone = re.sub(r"\s+", " ", match.group(0)).strip()
        if phone not in phones:
            phones.append(phone)
    return phones


def _meta_text(html):
    parts = []
    for tag in META_RE.findall(html or ""):
        match = CONTENT_RE.search(tag)
        if match:
            parts.append(html_lib.unescape(match.group(1)))
    return " ".join(parts)


def _fetch_public(url, timeout=10, max_bytes=450000):
    """Page publique, avec une identité de navigateur classique. Aucun compte."""
    ensure_public_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final = resp.geturl()
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype and "html" not in ctype and "text" not in ctype:
            return final, ""
        raw = resp.read(max_bytes)
    return final, raw.decode("utf-8", "replace")


def _score_email(email, context, *, dedicated, host):
    local = _fold(email.split("@", 1)[0])
    domain = email.split("@", 1)[1].lower() if "@" in email else ""
    if any(token in local for token in LOCAL_BAD):
        return -10
    score = 2  # trouvé sur la page du résultat
    if CE_LOCAL_RE.search(local):
        score += 10
    if is_ce_text(context):
        score += 6
    elif _contactish(context):
        score += 2
    if dedicated:
        score += 3
    if host and domain and (domain == host or host.endswith("." + domain) or domain.endswith("." + host)):
        score += 2
    return score


def _contact_links(page_url, html, limit=2):
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    host = _host(page_url)
    links = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html or "", re.I):
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urllib.parse.urljoin(page_url, href).split("#", 1)[0]
        if _host(absolute) != host or absolute.lower().endswith(SKIP_EXT):
            continue
        if any(token in _fold(urlparse(absolute).path) for token in ("contact", "nous-joindre", "ecrire")):
            if absolute not in links:
                links.append(absolute)
        if len(links) >= limit:
            break
    fallback = origin + "/contact"
    if fallback not in links:
        links.append(fallback)
    return links[: limit + 1]


def inspect_hit(hit, network, stop_flag):
    """Retourne {'email', 'phone', 'note'} pour un résultat de recherche."""
    url = hit["url"]
    host = _host(url)
    snippet = f"{hit.get('title') or ''} {hit.get('text') or ''}"
    dedicated = network != "web" or is_ce_text(f"{host} {urlparse(url).path} {hit.get('title') or ''}")
    candidates = []  # (score, email)
    phones = _phones_in_text(snippet)
    note = ""

    for email in _emails_in_text(snippet):
        candidates.append((_score_email(email, snippet, dedicated=dedicated, host=host), email))

    if not stop_flag():
        try:
            final_url, html = _fetch_public(url)
        except Exception:
            final_url, html = url, ""
        if html and network != "web" and _login_wall(final_url, html):
            note = "page publique limitée : connexion demandée par le réseau"
            html = ""
        if html:
            page_host = _host(final_url) or host
            meta = _meta_text(html)
            for email in _emails_in_text(meta):
                candidates.append((_score_email(email, meta, dedicated=dedicated, host=page_host), email))
            for email, context in _emails_from_html(html):
                if not _junk_email(email):
                    candidates.append((_score_email(email, context, dedicated=dedicated, host=page_host), email.strip()))
            phones.extend(p for p in _phones_from_html(html) if p not in phones)
            phones.extend(p for p in _phones_in_text(meta) if p not in phones)
            best = max((c[0] for c in candidates), default=-99)
            if network == "web" and dedicated and best < ACCEPT_SCORE and not stop_flag():
                for contact in _contact_links(final_url, html):
                    if stop_flag():
                        break
                    try:
                        contact_url, contact_html = _http_get(contact, timeout=8, max_bytes=300000)
                    except Exception:
                        continue
                    for email, context in _emails_from_html(contact_html or ""):
                        if not _junk_email(email):
                            candidates.append((
                                _score_email(email, context, dedicated=True, host=_host(contact_url) or host),
                                email.strip(),
                            ))
                    for phone in _phones_from_html(contact_html or ""):
                        if phone not in phones:
                            phones.append(phone)
                    if max((c[0] for c in candidates), default=-99) >= ACCEPT_SCORE:
                        break

    email = ""
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates[0][0] >= ACCEPT_SCORE:
            email = candidates[0][1]
    return {"email": email, "phone": phones[0] if phones else "", "note": note}


# ---------------------------------------------------------------- orchestration

def _hit_relevant(hit, network):
    title = _fold(hit.get("title") or "")
    if network == "web" and NOISE_TITLE_RE.search(title):
        return False
    return is_ce_text(f"{hit.get('title') or ''} {hit.get('text') or ''} {urllib.parse.unquote(hit.get('url') or '')}")


def _dedupe_key(network, url):
    if network == "web":
        return "web:" + _host(url)
    return network + ":" + url.rstrip("/").lower()


def scrape_ce_web(driver, places, companies, networks, engines, max_results, results_cache,
                  seen_urls, stop_flag, on_detail=None, log_fn=print, only_email=False, interactive=False,
                  api_keys=None):
    log = log_fn or print

    def detail(message):
        if on_detail:
            on_detail(message)

    networks = [n for n in NETWORK_ORDER if n in set(networks or ["web"])] or ["web"]
    engines = [e for e in ENGINE_ORDER if e in set(engines or ENGINE_ORDER)] or list(ENGINE_ORDER)
    missing = [e for e in engines if not engine_ready(e, api_keys)]
    for engine in missing:
        log(f"  ⚠ {ENGINE_LABELS[engine]} ignoré : clé non enregistrée dans les Paramètres.")
    engines = [e for e in engines if e not in missing]
    if not engines:
        detail("Aucun moteur utilisable : ajoutez une clé dans Paramètres ou cochez un moteur de navigateur.")
        return 0
    places = [p.strip() for p in (places or []) if p and p.strip()]
    companies = [c.strip() for c in (companies or []) if c and c.strip()]
    tasks = build_queries(companies, places, networks)
    if len(tasks) > MAX_TASKS:
        log(f"⚠ {len(tasks)} recherches demandées, limitées à {MAX_TASKS}. Réduisez les entreprises ou les lieux.")
        tasks = tasks[:MAX_TASKS]
    zone_default = " ; ".join(places) if places else "France entière"

    dead = set()
    seen_emails = {str(row.get("email") or "").casefold() for row in results_cache if row.get("email")}
    log(f"🔎 CE sur Internet : {len(tasks)} requêtes · {', '.join(ENGINE_LABELS[e] for e in engines)} · "
        f"{', '.join(NETWORKS[n] for n in networks)}")

    for index, (company, place, network, query) in enumerate(tasks, 1):
        if stop_flag() or len(results_cache) >= max_results:
            break
        if all(engine in dead for engine in engines):
            log("⛔ Tous les moteurs ont bloqué la recherche automatique. Réessayez plus tard, avec « Voir Chrome » ou un proxy.")
            detail("Tous les moteurs bloquent la recherche automatique pour le moment.")
            break
        candidates = []
        for engine in engines:
            if engine in dead or stop_flag():
                continue
            detail(f"{len(results_cache)} CE trouvés · requête {index}/{len(tasks)} · {ENGINE_LABELS[engine]}"
                   f" · {NETWORKS[network]}{' · ' + company if company else ''}")
            log(f"  🔍 [{ENGINE_LABELS[engine]}] {query}")
            if engine in API_ENGINES:
                found = api_search(engine, query, PER_SEARCH, api_keys, log, dead)
            else:
                found = run_engine(driver, engine, query, PER_SEARCH, interactive, stop_flag, log, dead)
            for hit in found:
                accepted = accept_url(hit["url"], networks)
                if not accepted:
                    continue
                hit_network, clean_url = accepted
                if not _hit_relevant(hit, hit_network):
                    continue
                key = _dedupe_key(hit_network, clean_url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                candidates.append({**hit, "url": clean_url, "network": hit_network, "engine": engine})
            if not stop_flag():
                time.sleep(0.3 if engine in API_ENGINES else random.uniform(1.2, 2.4))
        if not candidates or stop_flag():
            continue

        pool = ThreadPoolExecutor(max_workers=4)
        futures = {pool.submit(inspect_hit, hit, hit["network"], stop_flag): hit for hit in candidates}
        try:
            for future in as_completed(futures):
                if stop_flag() or len(results_cache) >= max_results:
                    break
                hit = futures[future]
                try:
                    info = future.result()
                except Exception:
                    info = {"email": "", "phone": "", "note": ""}
                email = info["email"]
                if email and email.casefold() in seen_emails:
                    continue
                if only_email and not email:
                    continue
                if email:
                    seen_emails.add(email.casefold())
                name = clean_title(hit.get("title"), hit["url"])
                snippet = re.sub(r"\s+", " ", hit.get("text") or "").strip()[:220]
                results_cache.append({
                    "nom": name,
                    "region": place or zone_default,
                    "groupe": f"CE · {NETWORKS[hit['network']]}",
                    "reseau": NETWORKS[hit["network"]],
                    "entreprise": company,
                    "email": email,
                    "phone": info["phone"],
                    "url": hit["url"],
                    "moteur": ENGINE_LABELS[hit["engine"]],
                    "extrait": snippet,
                    "remarque": info["note"],
                    "source": "ce_web",
                })
                log(f"  ✓ [{len(results_cache)}] {name} | {email or '—'} | {info['phone'] or '—'} | {NETWORKS[hit['network']]}")
        finally:
            pool.shutdown(wait=not stop_flag(), cancel_futures=True)

    with_mail = sum(1 for row in results_cache if row.get("email"))
    detail(f"Terminé : {len(results_cache)} CE trouvés dont {with_mail} avec e-mail")
    log(f"✅ CE sur Internet : {len(results_cache)} résultats, {with_mail} e-mails")
    return len(results_cache)
