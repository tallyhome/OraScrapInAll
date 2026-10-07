"""
Stratégie Google : pour chaque camping d'un groupe, cherche sur Google
le site officiel individuel du camping, puis extrait email + téléphone.

Avantage : les sites officiels des campings exposent souvent leurs emails,
contrairement aux sites des groupes (Sandaya, Yelloh, etc.) qui n'ont que
des formulaires de contact.
"""
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from urllib.parse import urlparse


# Domaines à exclure des résultats Google (sites annuaires, pas sites officiels)
EXCLUDED_DOMAINS = {
    "booking.com", "tripadvisor.fr", "tripadvisor.com", "campingfrance.com",
    "camping.info", "eurocampings.fr", "eurocampings.com", "abritel.fr",
    "airbnb.fr", "leboncoin.fr", "pagesjaunes.fr", "yelp.fr",
    "google.com", "google.fr", "wikipedia.org", "facebook.com",
    "instagram.com", "youtube.com", "twitter.com",
    "sandaya.fr", "yellohvillage.fr", "capfun.com", "huttopia.com",
    "siblu.fr", "homair.com", "tohapi.fr", "flowercampings.com",
    "sunelia.com", "les-castels.com", "odalys-vacances.com",
    "toocamp.com", "campeole.com", "campingparadis.fr",
}

# Préfixes email à tester si aucun email trouvé sur le site
EMAIL_PREFIXES = ["contact", "reception", "info", "animation", "accueil", "camping"]


def is_excluded_domain(url):
    """Vérifie si l'URL appartient à un domaine à exclure."""
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(excl in domain for excl in EXCLUDED_DOMAINS)
    except Exception:
        return True


def google_search_camping(driver, camping_name, group_name, pause=1.5):
    """
    Cherche sur Google le site officiel d'un camping.
    Retourne l'URL du premier résultat pertinent.
    """
    query = f'"{camping_name}" {group_name} camping contact email'
    try:
        driver.get(f"https://www.google.fr/search?q={query.replace(' ', '+')}&hl=fr&num=10")
        time.sleep(pause)

        # Accepter les cookies Google si popup
        try:
            accept_btn = driver.find_element(By.XPATH,
                "//button[contains(., 'Tout accepter') or contains(., 'Accept all')]")
            accept_btn.click()
            time.sleep(0.5)
        except Exception:
            pass

        # Récupérer les liens de résultats
        result_links = driver.find_elements(By.CSS_SELECTOR, "div.yuRUbf a, div[data-hveid] a[href^='http']")
        for link in result_links:
            try:
                href = link.get_attribute("href")
                if not href or not href.startswith("http"):
                    continue
                if is_excluded_domain(href):
                    continue
                return href
            except Exception:
                continue
    except Exception as e:
        print(f"    ⚠ Erreur Google search '{camping_name}': {e}")

    return None


def extract_email_from_site(driver, site_url, pause=0.8):
    """
    Visite un site officiel de camping et tente d'extraire email + téléphone.
    Essaie la page principale, puis /contact, /nous-contacter, /infos.
    """
    email = None
    phone = None

    pages_to_try = [
        site_url,
        site_url.rstrip("/") + "/contact",
        site_url.rstrip("/") + "/nous-contacter",
        site_url.rstrip("/") + "/contact.html",
        site_url.rstrip("/") + "/contactez-nous",
    ]

    for page_url in pages_to_try:
        try:
            driver.get(page_url)
            time.sleep(pause)
            found_email, found_phone = _scrape_contact_page(driver)
            if found_email:
                email = found_email
            if found_phone:
                phone = found_phone
            if email and phone:
                break
        except Exception:
            continue

    # Si toujours pas d'email, tenter les prefixes connus sur le domaine
    if not email and site_url:
        domain = _extract_domain(site_url)
        if domain:
            email = _try_email_prefixes(driver, domain)

    return email, phone


def _scrape_contact_page(driver):
    """Extrait email et téléphone depuis la page courante."""
    email = None
    phone = None

    # Tentative 1 : balise mailto
    try:
        mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
        raw = mail_el.get_attribute("href").replace("mailto:", "").split("?")[0].strip()
        if "@" in raw and _is_valid_email(raw):
            email = raw
    except Exception:
        pass

    # Tentative 2 : balise tel
    try:
        tel_el = driver.find_element(By.CSS_SELECTOR, "a[href^='tel']")
        phone = tel_el.text.strip() or tel_el.get_attribute("href").replace("tel:", "").strip()
    except Exception:
        pass

    # Tentative 3 : regex dans le texte de la page
    if not email or not phone:
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            if not email:
                emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]{2,}', page_text)
                emails = [e for e in emails if _is_valid_email(e)]
                if emails:
                    email = emails[0]
            if not phone:
                phones = re.findall(r'(?:\+33\s?|0)[1-9](?:[\s.\-]?\d{2}){4}', page_text)
                if phones:
                    phone = phones[0].strip()
        except Exception:
            pass

    return email, phone


def _try_email_prefixes(driver, domain):
    """
    Si aucun email trouvé, cherche sur Google des emails probables
    du type contact@domaine.fr
    """
    query = f'email "@{domain}" camping contact reception'
    try:
        driver.get(f"https://www.google.fr/search?q={query.replace(' ', '+')}&hl=fr")
        time.sleep(1)
        page_text = driver.find_element(By.TAG_NAME, "body").text
        emails = re.findall(r'[\w.+-]+@' + re.escape(domain), page_text)
        emails = [e for e in emails if _is_valid_email(e)]
        if emails:
            return emails[0]
    except Exception:
        pass
    return None


def _extract_domain(url):
    """Extrait le domaine principal d'une URL."""
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return netloc if netloc else None
    except Exception:
        return None


def _is_valid_email(email):
    """Vérifie basiquement qu'un email est valide et pas une image/ressource."""
    if not email or "@" not in email:
        return False
    invalid_exts = {".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".woff", ".svg", ".ico"}
    if any(email.lower().endswith(ext) for ext in invalid_exts):
        return False
    domain_part = email.split("@")[-1]
    if "." not in domain_part:
        return False
    return True


def scrape_group_via_google(driver, source_id, source_config, campings_list,
                             max_results, results_cache, seen_urls_global,
                             stop_flag=None, log_fn=None):
    """
    Pour chaque camping de la liste, cherche sur Google son site officiel
    et extrait email + téléphone.

    campings_list : liste de tuples (name, group_url)
                    où group_url = URL sur le site du groupe
    """
    def log(msg):
        if log_fn:
            log_fn(msg)
        else:
            print(msg)

    label = source_config["label"]
    added = 0

    for name, group_url in campings_list:
        if stop_flag and stop_flag():
            break
        if len(results_cache) >= max_results:
            break
        if group_url in seen_urls_global:
            continue

        log(f"  🔍 Google → {name} ({label})")

        # 1. Chercher le site officiel sur Google
        official_url = google_search_camping(driver, name, label, pause=1.2)

        email = None
        phone = None

        if official_url:
            log(f"     → Site officiel : {official_url}")
            email, phone = extract_email_from_site(driver, official_url, pause=0.8)
        else:
            log(f"     → Pas de site officiel trouvé, tentative sur page groupe")
            # Fallback : page du groupe
            try:
                driver.get(group_url)
                time.sleep(0.5)
                email, phone = _scrape_contact_page(driver)
            except Exception:
                pass

        results_cache.append({
            "nom": name,
            "region": label,
            "groupe": label,
            "email": email,
            "phone": phone,
            "url": official_url or group_url,
            "source": source_id,
        })
        seen_urls_global.add(group_url)
        added += 1
        log(f"  ✓ [{len(results_cache)}] {name} | {email or '—'} | {phone or '—'}")

    return added
