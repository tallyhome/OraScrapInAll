"""
Scraper universel : recherche Google + extraction email/téléphone
Fonctionne pour n'importe quel mot-clé (entreprises, C.E, restaurants, etc.)
"""
import time
import re
from selenium.webdriver.common.by import By
from urllib.parse import urlparse

# Domaines à exclure des résultats Google (non pertinents)
EXCLUDED_DOMAINS = {
    "booking.com", "tripadvisor.fr", "tripadvisor.com", "campingfrance.com",
    "camping.info", "eurocampings.fr", "eurocampings.com", "abritel.fr",
    "airbnb.fr", "leboncoin.fr", "pagesjaunes.fr", "yelp.fr",
    "google.com", "google.fr", "wikipedia.org", "facebook.com",
    "instagram.com", "youtube.com", "twitter.com", "linkedin.com",
    "societe.com", "infogreffe.fr", "pappers.fr", "manageo.fr",
    "kompass.com", "eurodir.com", "annuaire-des-entreprises.com",
    "123entreprises.com", "societe.com", "verif.com", "linternaute.com",
    "lefigaro.fr", "lemonde.fr", "leparisien.fr", "ouest-france.fr",
    "cnews.fr", "francebleu.fr", "franceinfo.fr", "liberation.fr",
}

# Préfixes email à tester si aucun email trouvé
EMAIL_PREFIXES = ["contact", "info", "reception", "accueil", "commercial", "service", "admin", "support"]


def is_excluded_domain(url):
    """Vérifie si l'URL appartient à un domaine à exclure."""
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(excl in domain for excl in EXCLUDED_DOMAINS)
    except Exception:
        return True


def google_search(driver, query, max_results=20, pause=1.5):
    """
    Lance une recherche Google et retourne les URLs des résultats pertinents.
    """
    print(f"    🔍 Recherche Google : {query}")
    try:
        # Construire l'URL avec les bons paramètres
        search_url = f"https://www.google.fr/search?q={query.replace(' ', '+')}&hl=fr&num={max_results}"
        driver.get(search_url)
        time.sleep(pause)

        # Accepter les cookies Google si popup (plusieurs sélecteurs possibles)
        cookie_selectors = [
            "//button[contains(., 'Tout accepter')]",
            "//button[contains(., 'Accept all')]",
            "//button[contains(., 'Accepter')]",
            "//button[@id='L2AGLb']"
        ]
        for selector in cookie_selectors:
            try:
                accept_btn = driver.find_element(By.XPATH, selector)
                accept_btn.click()
                time.sleep(0.5)
                print(f"    ✓ Cookies acceptés")
                break
            except Exception:
                continue

        # Attendre que les résultats se chargent
        time.sleep(1)

        # Récupérer les liens de résultats (sélecteurs plus robustes)
        selectors = [
            "div.yuRUbf a",
            "div[data-hveid] a[href^='http']",
            "div.g a[href^='http']",
            "h3 a[href^='http']"
        ]
        
        urls = []
        for selector in selectors:
            try:
                result_links = driver.find_elements(By.CSS_SELECTOR, selector)
                print(f"    → {len(result_links)} liens trouvés avec {selector}")
                
                for link in result_links:
                    try:
                        href = link.get_attribute("href")
                        if not href or not href.startswith("http"):
                            continue
                        if is_excluded_domain(href):
                            continue
                        if href not in urls:
                            urls.append(href)
                        if len(urls) >= max_results:
                            break
                    except Exception:
                        continue
                
                if urls:
                    break
            except Exception:
                continue

        print(f"    ✓ {len(urls)} URLs uniques trouvées")
        return urls
    except Exception as e:
        print(f"    ⚠ Erreur Google search '{query}': {e}")
        return []


def extract_email_and_phone(driver, url, pause=0.8):
    """
    Visite une URL et extrait email + téléphone.
    """
    email = None
    phone = None

    print(f"    🌐 Visite : {url}")

    # 1. Page principale
    try:
        driver.get(url)
        time.sleep(pause)
        email, phone = _scrape_page(driver)
        print(f"      → Page principale : email={email or '—'}, phone={phone or '—'}")
    except Exception as e:
        print(f"      ⚠ Erreur page principale : {e}")

    # 2. Pages contact si pas d'email
    if not email:
        contact_pages = [
            url.rstrip("/") + "/contact",
            url.rstrip("/") + "/nous-contacter",
            url.rstrip("/") + "/contactez-nous",
            url.rstrip("/") + "/contact.html",
        ]
        for page_url in contact_pages:
            try:
                print(f"      → Essai page contact : {page_url}")
                driver.get(page_url)
                time.sleep(pause)
                found_email, found_phone = _scrape_page(driver)
                if found_email:
                    email = found_email
                    print(f"        ✓ Email trouvé : {email}")
                if found_phone:
                    phone = found_phone
                    print(f"        ✓ Phone trouvé : {phone}")
                if email and phone:
                    break
            except Exception as e:
                print(f"        ⚠ Erreur page contact : {e}")
                continue

    # 3. Emails par défaut si toujours rien
    if not email and url:
        domain = _extract_domain(url)
        if domain:
            print(f"      → Test emails standards pour {domain}")
            email = _try_email_prefixes(driver, domain)
            if email:
                print(f"        ✓ Email par défaut trouvé : {email}")

    return email, phone


def _scrape_page(driver):
    """Extrait email et téléphone depuis la page courante."""
    email = None
    phone = None

    # 1. Balises mailto/tel
    try:
        mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
        raw = mail_el.get_attribute("href").replace("mailto:", "").split("?")[0].strip()
        if "@" in raw and _is_valid_email(raw):
            email = raw
    except Exception:
        pass

    try:
        tel_el = driver.find_element(By.CSS_SELECTOR, "a[href^='tel']")
        phone = tel_el.text.strip() or tel_el.get_attribute("href").replace("tel:", "").strip()
    except Exception:
        pass

    # 2. Regex dans le texte de la page
    if not email or not phone:
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            if not email:
                emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]{2,}', page_text)
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


def _extract_domain(url):
    """Extrait le domaine principal d'une URL."""
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return netloc if netloc else None
    except Exception:
        return None


def _try_email_prefixes(driver, domain):
    """
    Cherche sur Google des emails probables du type contact@domaine.fr
    """
    query = f'email "@{domain}" contact info reception'
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


def _is_valid_email(email):
    """Vérifie basiquement qu'un email est valide."""
    if not email or "@" not in email:
        return False
    invalid_exts = {".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".woff", ".svg", ".ico"}
    if any(email.lower().endswith(ext) for ext in invalid_exts):
        return False
    domain_part = email.split("@")[-1]
    if "." not in domain_part:
        return False
    return True


def scrape_universal(driver, query, max_results=20, results_cache=None, seen_urls=None, stop_flag=None, log_fn=None):
    """
    Scraper universel : recherche Google + extraction email/téléphone.
    """
    if results_cache is None:
        results_cache = []
    if seen_urls is None:
        seen_urls = set()

    def log(msg):
        if log_fn:
            log_fn(msg)
        else:
            print(msg)

    log(f"🔍 Recherche universelle : '{query}'")

    # 1. Recherche Google
    urls = google_search(driver, query, max_results=max_results, pause=1.2)
    log(f"  → {len(urls)} URLs trouvées")

    # 2. Visiter chaque URL
    for url in urls:
        if stop_flag and stop_flag():
            break
        if url in seen_urls:
            continue

        log(f"  🌐 {url}")

        # Essayer d'extraire le nom de l'entreprise depuis le domaine
        name = _extract_name_from_url(url)

        email, phone = extract_email_and_phone(driver, url, pause=0.8)

        results_cache.append({
            "nom": name,
            "region": "Universel",
            "groupe": "Recherche Google",
            "email": email,
            "phone": phone,
            "url": url,
            "source": "universal",
        })
        seen_urls.add(url)
        log(f"  ✓ [{len(results_cache)}] {name} | {email or '—'} | {phone or '—'}")

    log(f"✅ {len(urls)} URLs explorées")
    return len(urls)


def _extract_name_from_url(url):
    """Essaie d'extraire un nom d'entreprise depuis l'URL."""
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        # Enlever les TLD
        name = re.sub(r'\.(fr|com|org|net|io|co|eu)$', '', domain)
        # Remplacer les tirets par des espaces
        name = name.replace('-', ' ').title()
        return name
    except Exception:
        return "Inconnu"
