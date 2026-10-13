"""Salles de réception publiques : châteaux, manoirs et lieux d'événement."""
import html
import re
import time
import urllib.request
from urllib.parse import urljoin, urlparse

from scrapers.systeme.netguard import ensure_public_url

USER_AGENT = "OraScrapInAll/2.2.0 (contact public)"
EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9._%+\-]{0,64}@[a-z0-9.\-]+\.[a-z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+33[\s.\-]?(?:\(0\)[\s.\-]?)?|0)[1-9](?:[\s.\-]\d{2}){4}")
SKIP_HOSTS = {
    "1001salles.com", "1001traiteurs.com", "1001dj.com", "reserverunbar.fr",
    "123-location-salle.fr", "123weddinginfrance.com", "sallesenfrance.com",
    "123-sejours-en-france.fr", "cookie-script.com", "facebook.com",
    "facebook.net", "instagram.com", "google.com", "googleapis.com",
    "googletagmanager.com", "google-analytics.com", "googleadservices.com",
    "gstatic.com", "doubleclick.net", "youtube.com", "linkedin.com",
    "twitter.com", "x.com", "pinterest.com", "schema.org", "w3.org",
    "cloudflare.com", "cloudflareinsights.com", "hotjar.com", "jsdelivr.net",
    "fontawesome.com", "bootstrapcdn.com",
}
VENUE_RE = re.compile(
    r'data-fav-name="([^"]+)"\s+data-fav-type="venue"\s+data-fav-city="([^"]*)"\s+'
    r'data-fav-thumb="[^"]*"\s+data-fav-url="([^"]+)"'
)


def scrape_receptions(source_id, source_config, max_results, keyword, results_cache, seen_urls, stop_flag, log_fn):
    label = source_config.get("label") or "Salles de réception"
    log = log_fn or (lambda message: None)
    rows = []
    seen_names = set()

    for seed in source_config.get("seeds") or []:
        url = (seed.get("url") or "").strip()
        name = html.unescape(seed.get("nom") or "").strip()
        if not url or not name:
            continue
        key = _key(name)
        if key in seen_names:
            continue
        seen_names.add(key)
        rows.append({
            "nom": name,
            "ville": seed.get("ville") or "",
            "url": url,
            "direct": True,
        })

    for listing in source_config.get("listings") or []:
        if stop_flag and stop_flag():
            break
        log(f"[{label}] Lecture de l'annuaire…")
        page = _fetch(listing)
        if not page:
            log(f"[{label}] Annuaire illisible.")
            continue
        for name, city, href in VENUE_RE.findall(page):
            name = html.unescape(name).strip()
            key = _key(name)
            if not name or key in seen_names:
                continue
            if not href.startswith("/mariage/"):
                continue
            seen_names.add(key)
            rows.append({
                "nom": name,
                "ville": html.unescape(city).strip(),
                "url": urljoin(listing, href),
                "direct": False,
            })

    if keyword:
        words = [part.strip().lower() for part in keyword.split() if len(part.strip()) > 2]
        if words:
            rows = [
                row for row in rows
                if any(word in f"{row['nom']} {row['ville']}".lower() for word in words)
            ]

    log(f"[{label}] {len(rows)} lieux à lire")
    added = 0
    for row in rows:
        if stop_flag and stop_flag():
            break
        if max_results and len(results_cache) >= max_results:
            break
        site, email, phone = _contacts(row["url"], direct=row["direct"])
        final_url = site or row["url"]
        if final_url in seen_urls:
            continue
        results_cache.append({
            "nom": row["nom"],
            "region": "Côte-d'Or",
            "groupe": "Salle de réception",
            "ville": row["ville"],
            "email": email or None,
            "phone": phone or None,
            "url": final_url,
            "source": source_id,
        })
        seen_urls.add(final_url)
        added += 1
        log(f"  ✓ [{len(results_cache)}] {row['nom']} | {email or '—'} | {phone or '—'}")
        time.sleep(0.3)
    log(f"[{label}] {added} lieux ajoutés")
    return added


def _contacts(url, direct=False):
    if direct:
        email, phone = _read_site(url)
        return url, email, phone
    page = _fetch(url)
    if not page:
        return "", "", ""
    site = _official_site(page)
    if not site:
        return url, "", ""
    email, phone = _read_site(site)
    return site, email, phone


def _read_site(url):
    pages = [url]
    root = _origin(url)
    site_host = _host(url)
    if root:
        for path in ("/contact", "/contact/", "/receptions/", "/nous-contacter"):
            candidate = root + path
            if candidate.rstrip("/") != url.rstrip("/"):
                pages.append(candidate)
    email = ""
    phone = ""
    for page_url in pages[:4]:
        body = _fetch(page_url)
        if not body:
            continue
        if not email:
            email = _first_email(body, site_host)
        if not phone:
            phone = _first_phone(body)
        if email and phone:
            break
    return email, phone


def _official_site(page):
    for href in re.findall(r'href="(https?://[^"]+)"', page):
        host = _host(href)
        if not host or _skipped(host):
            continue
        if any(host.endswith(ext) for ext in (".png", ".jpg", ".css", ".js", ".svg")):
            continue
        return href.split("#")[0]
    return ""


def _fetch(url):
    try:
        ensure_public_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        with urllib.request.urlopen(request, timeout=18) as response:
            return response.read(500000).decode("utf-8", "replace")
    except Exception:
        return ""


def _first_email(page, site_host):
    for raw in EMAIL_RE.findall(page):
        mail = raw.lower().strip(".").strip()
        host = mail.split("@")[-1]
        if _skipped(host) or mail.endswith((".png", ".jpg", ".css", ".js", ".webp")):
            continue
        if any(bad in host for bad in ("sentry", "wixpress", "example.com")):
            continue
        if site_host and not _same_site(host, site_host):
            continue
        return mail
    return ""


def _same_site(email_host, site_host):
    email_host = email_host.lower().removeprefix("www.")
    site_host = site_host.lower().removeprefix("www.")
    return email_host == site_host or email_host.endswith("." + site_host) or site_host.endswith("." + email_host)


def _first_phone(page):
    for match in PHONE_RE.finditer(page):
        raw = re.sub(r"\s+", " ", match.group(0)).strip()
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("33"):
            digits = "0" + digits[2:]
        if len(digits) != 10 or not digits.startswith("0"):
            continue
        if len(set(digits[2:])) <= 1:
            continue
        return raw
    return ""


def _origin(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.hostname}"


def _host(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _skipped(host):
    host = host.lower()
    if "1001salles" in host or "1001traiteurs" in host or "1001dj" in host:
        return True
    return any(host == skip or host.endswith("." + skip) for skip in SKIP_HOSTS)


def _key(name):
    folded = name.lower()
    for src, dst in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ô", "o"), ("î", "i"), ("ï", "i"), ("ç", "c"), ("'", ""), ("’", "")):
        folded = folded.replace(src, dst)
    return re.sub(r"[^a-z0-9]+", "", folded)
