"""
Scraper intelligent pour personnes/entreprises
- Recherche ciblée sur le nom
- Identification des sites dédiés
- Google Établissements/Business
- Extraction d'infos pertinentes
"""
import time
import re
from selenium.webdriver.common.by import By
from urllib.parse import urlparse

# Domaines à exclure (réseaux sociaux, annuaires, Google...)
EXCLUDED_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "linkedin.com", "viadeo.com",
    "youtube.com", "tiktok.com", "pinterest.com", "snapchat.com",
    "tripadvisor.fr", "booking.com", "pagesjaunes.fr", "annuaire-inverse.com",
    "societe.com", "infogreffe.fr", "manageo.fr", "kompass.com",
    "lefigaro.fr", "lemonde.fr", "leparisien.fr", "wikipedia.org",
    # Google tout entier
    "google.com", "google.fr", "google.co", "googleapis.com", "googleusercontent.com",
    "google-analytics.com", "googleadservices.com", "googletagmanager.com",
    "googlemaps.com", "google.fr/maps", "google.fr/search",
    # Plateformes de réservation/events
    "billetreduc.com", "evenementielpourtous.com", "lasemaine.fr",
    "digitick.com", "fnac.com", "ticketmaster.fr"
}

# Mots-clés indiquant un site personnel/professionnel
PERSONAL_SITE_KEYWORDS = [
    "portfolio", "freelance", "consultant", "expert", "coach", "formateur",
    "développeur", "developpeur", "developer", "designer", "artist", "artiste",
    "photographe", "avocat", "notaire", "médecin", "dentiste", "architecte"
]


def is_personal_site(url, title="", query=""):
    """Détecte si l'URL semble être un site personnel/professionnel."""
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        url_lower = url.lower()
        title_lower = title.lower()
        query_lower = query.lower()
        
        # Exclure les plateformes connues
        if any(excl in domain for excl in EXCLUDED_DOMAINS):
            return False
            
        # Domaine personnalisé (pas de sous-domaine de plateforme)
        if len(domain.split('.')) >= 2:
            # Vérifier les mots-clés dans l'URL ou le titre
            text = (domain + " " + title).lower()
            if any(keyword in text for keyword in PERSONAL_SITE_KEYWORDS):
                return True
            
            # Si le nom de domaine contient des parties de la requête (site personnel)
            query_parts = query_lower.split()
            for part in query_parts:
                if len(part) > 3 and part in domain:
                    return True
            
            # Domaines courts et personnalisés (.biz, .me, .fr, .com avec nom)
            if any(tld in domain for tld in ['.biz', '.me', '.name', '.pro']):
                return True
                
            # TLDs courants avec nom dans le domaine
            if any(tld in domain for tld in ['.fr', '.com', '.net', '.org']):
                # Vérifier si le domaine ressemble à un nom/personne
                domain_name = domain.split('.')[0]
                if len(domain_name) > 4 and any(char.isalpha() for char in domain_name):
                    return True
                    
    except Exception:
        pass
    return False


def smart_search(driver, query, max_results=15, pause=1.5):
    """
    Recherche intelligente : Google + Google Établissements
    """
    print(f"🎯 Recherche intelligente : {query}")
    
    # 1. Recherche Google standard
    google_urls = _google_search_standard(driver, query, max_results, pause)
    
    # 2. Recherche Google Établissements/Business
    business_urls = _google_search_business(driver, query, pause)
    
    # Combiner et dédupliquer
    all_urls = []
    seen = set()
    for url in google_urls + business_urls:
        if url not in seen:
            all_urls.append(url)
            seen.add(url)
    
    print(f"  → {len(all_urls)} URLs uniques trouvées")
    return all_urls[:max_results]


def _google_search_standard(driver, query, max_results, pause):
    """Recherche Google standard avec requêtes optimisées."""
    print(f"  📝 Recherche Google standard pour : {query}")
    
    # Recherche standard uniquement (plus intelligent)
    queries = [
        f'"{query}"',            # Recherche standard avec nom exact
    ]
    
    all_urls = []
    
    for search_query in queries:
        try:
            search_url = f"https://www.google.fr/search?q={search_query.replace(' ', '+')}&hl=fr&num={max_results}"
            driver.get(search_url)
            time.sleep(pause)
        
        # Cookies
            for selector in ["//button[contains(., 'Tout accepter')]", "//button[contains(., 'Accept all')]", "//button[@id='L2AGLb']"]:
                try:
                    driver.find_element(By.XPATH, selector).click()
                    time.sleep(0.5)
                    break
                except Exception:
                    continue
            
            time.sleep(1)
            
            # Extraire les liens avec des sélecteurs plus précis
            urls = []
            selectors = [
                "div.yuRUbf a[href^='http']",  # Résultats principaux
                "div.g a[href^='http']:not([href*='google'])",  # Résultats généraux sauf Google
                "h3 a[href^='http']:not([href*='google'])"  # Liens dans les titres
            ]
            
            for selector in selectors:
                try:
                    links = driver.find_elements(By.CSS_SELECTOR, selector)
                    print(f"    → {len(links)} liens trouvés avec {selector}")
                    for link in links:
                        try:
                            href = link.get_attribute("href")
                            if href and href.startswith("http") and not is_excluded_domain(href):
                                # Vérifier que c'est un vrai site externe
                                domain = urlparse(href).netloc.lower()
                                if not any(google in domain for google in ["google", "youtube"]):
                                    if href not in all_urls:  # Éviter les doublons
                                        urls.append(href)
                                        all_urls.append(href)
                                        print(f"      ✓ Ajouté : {domain}")
                                if len(all_urls) >= max_results:
                                    break
                        except Exception:
                            continue
                    if urls:
                        break
                except Exception:
                    continue
            
            if urls:
                print(f"    → {len(urls)} URLs trouvées avec '{search_query}'")
                if len(all_urls) >= max_results:
                    break
            
        except Exception as e:
            print(f"    ⚠ Erreur avec requête '{search_query}': {e}")
            continue
    
    print(f"  📝 Google standard : {len(all_urls)} URLs totales")
    return all_urls[:max_results]


def _google_search_business(driver, query, pause):
    """Recherche Google Établissements/Business avec extraction téléphone."""
    try:
        print(f"  📍 Recherche Google Business pour : {query}")
        
        # URL de recherche Google Maps/Établissements
        maps_url = f"https://www.google.fr/maps/search/{query.replace(' ', '+')}/"
        driver.get(maps_url)
        time.sleep(pause + 1)
        
        # Attendre que les résultats se chargent
        time.sleep(2)
        
        phone = None
        urls = []
        
        # Vérifier s'il y a des résultats
        try:
            # Chercher un indicateur de "Aucun résultat"
            no_results = driver.find_elements(By.CSS_SELECTOR, "div[role='main']:contains('Aucun résultat'), div:contains('Aucun résultat')")
            if no_results:
                print("  📍 Aucun résultat Google Maps trouvé")
                return []
        except Exception:
            pass
        
        # Cliquer sur le premier résultat pour voir les détails
        try:
            first_result = driver.find_element(By.CSS_SELECTOR, "a[href*='/maps/place/']")
            first_result.click()
            time.sleep(pause)
        except Exception:
            # Si pas de résultat direct, essayer un autre sélecteur
            try:
                first_result = driver.find_element(By.CSS_SELECTOR, "div[role='article'] a")
                first_result.click()
                time.sleep(pause)
            except Exception:
                print("  📍 Pas de fiche Google Business trouvée (normal pour les personnes)")
                return []
        
        # Extraire le téléphone depuis le panneau de détails
        try:
            phone_selectors = [
                "span[data-tooltip='Copier le numéro de téléphone']",
                "span:contains('📞')",
                "a[href^='tel:']",
                "[data-dtype='d3ph']"
            ]
            for selector in phone_selectors:
                try:
                    phone_el = driver.find_element(By.CSS_SELECTOR, selector)
                    phone_text = phone_el.text.strip()
                    if phone_text and len(phone_text) > 5:
                        phone = phone_text
                        print(f"    📞 Téléphone trouvé : {phone}")
                        break
                except Exception:
                    continue
            
            # Alternative : chercher dans le texte du panneau
            if not phone:
                try:
                    panel_text = driver.find_element(By.CSS_SELECTOR, "div[role='main']").text
                    phone_match = re.search(r'(?:\+33\s?|0)[1-9](?:[\s.\-]?\d{2}){4}', panel_text)
                    if phone_match:
                        phone = phone_match.group().strip()
                        print(f"    📞 Téléphone (regex) : {phone}")
                except Exception:
                    pass
        except Exception as e:
            print(f"    ⚠ Erreur extraction téléphone : {e}")
        
        # Extraire l'URL du site web depuis le panneau de détails
        try:
            website_link = driver.find_element(By.CSS_SELECTOR, "a[data-tooltip='Site web'], a[href*='http']")
            href = website_link.get_attribute("href")
            if href and href.startswith("http"):
                urls.append(href)
                print(f"  🏢 Google Business site : {href}")
        except Exception:
            # Alternative : chercher tous les liens externes
            try:
                links = driver.find_elements(By.CSS_SELECTOR, "a[href^='http']")
                for link in links:
                    href = link.get_attribute("href")
                    if href and not is_excluded_domain(href):
                        urls.append(href)
                        break
            except Exception:
                pass
        
        # Stocker le téléphone trouvé pour l'utiliser plus tard
        if phone:
            _last_google_phone = phone
        
        return urls
    except Exception as e:
        print(f"  ⚠ Erreur Google Business : {e}")
        return []


# Variable globale pour stocker le téléphone trouvé sur Google
_last_google_phone = None


def is_excluded_domain(url):
    """Vérifie si l'URL appartient à un domaine à exclure."""
    try:
        url = url.lower()
        domain = urlparse(url).netloc.replace("www.", "")
        path = urlparse(url).path.lower()
        
        # Exclusion stricte des domaines
        if any(excl in domain for excl in EXCLUDED_DOMAINS):
            return True
        
        # Exclusion des chemins Google
        if any(google_path in path for google_path in [
            "/policies/", "/support/", "/help/", "/search", "/maps", 
            "/intl/", "/about/", "/accounts/", "/services/"
        ]):
            return True
            
        # Exclusion des URLs contenant "google" n'importe où
        if "google" in url:
            return True
            
        return False
    except Exception:
        return True


def extract_smart_info(driver, url, query=""):
    """
    Extraction intelligente d'infos pour personnes/entreprises.
    """
    global _last_google_phone
    
    print(f"    🔍 Analyse : {url}")
    
    info = {
        "nom": "",
        "email": None,
        "phone": _last_google_phone,  # Utiliser le téléphone de Google Business si disponible
        "address": None,
        "description": "",
        "social_links": [],
        "url": url
    }
    
    # Réinitialiser le téléphone de Google après utilisation
    _last_google_phone = None
    
    try:
        driver.get(url)
        time.sleep(1)
        
        # 1. Titre de la page
        try:
            title = driver.title
            if title and title != "404" and title != "Erreur" and len(title.strip()) > 2:
                info["nom"] = title.split(" - ")[0].split(" | ")[0].strip()
                print(f"      📝 Titre : {info['nom']}")
            else:
                # Essayer d'extraire depuis l'URL si le titre est vide
                domain = urlparse(url).netloc.replace("www.", "")
                info["nom"] = domain.title()
                print(f"      🌐 Nom depuis domaine : {info['nom']}")
        except Exception:
            # Fallback : utiliser le domaine
            try:
                domain = urlparse(url).netloc.replace("www.", "")
                info["nom"] = domain.title()
                print(f"      🌐 Nom fallback : {info['nom']}")
            except Exception:
                info["nom"] = "Site inconnu"
        
        # 2. Email
        try:
            mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
            email = mail_el.get_attribute("href").replace("mailto:", "").split("?")[0].strip()
            if "@" in email:
                info["email"] = email
                print(f"      📧 Email : {email}")
        except Exception:
            pass
        
        # 3. Téléphone
        try:
            tel_el = driver.find_element(By.CSS_SELECTOR, "a[href^='tel']")
            phone = tel_el.text.strip() or tel_el.get_attribute("href").replace("tel:", "").strip()
            info["phone"] = phone
            print(f"      📞 Téléphone : {phone}")
        except Exception:
            pass
        
        # 4. Adresse (patterns français)
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            address_patterns = [
                r'\d+\s+[^\n]+,\s*\d{5}\s+[^\n]+',  # Numéro, rue, CP, ville
                r'[^\n]+\s+\d{5}\s+[^\n]+',        # Rue, CP, ville
            ]
            for pattern in address_patterns:
                addresses = re.findall(pattern, page_text)
                if addresses:
                    info["address"] = addresses[0].strip()
                    print(f"      📍 Adresse : {info['address']}")
                    break
        except Exception:
            pass
        
        # 5. Description (premier paragraphe significatif)
        try:
            paragraphs = driver.find_elements(By.CSS_SELECTOR, "p, .description, .about")
            for p in paragraphs:
                text = p.text.strip()
                if len(text) > 50 and len(text) < 500:  # Ni trop court, ni trop long
                    info["description"] = text
                    print(f"      📄 Description : {text[:100]}...")
                    break
        except Exception:
            pass
        
        # 6. Liens sociaux
        try:
            social_selectors = [
                "a[href*='facebook.com']", "a[href*='linkedin.com']", 
                "a[href*='twitter.com']", "a[href*='instagram.com']"
            ]
            for selector in social_selectors:
                try:
                    social_link = driver.find_element(By.CSS_SELECTOR, selector)
                    href = social_link.get_attribute("href")
                    if href:
                        info["social_links"].append(href)
                except Exception:
                    continue
            if info["social_links"]:
                print(f"      🔗 Réseaux sociaux : {len(info['social_links'])} trouvés")
        except Exception:
            pass
        
        # 7. Si pas d'email, essayer les pages contact
        if not info["email"]:
            contact_pages = [
                url.rstrip("/") + "/contact",
                url.rstrip("/") + "/nous-contacter",
                url.rstrip("/") + "/about"
            ]
            for page_url in contact_pages:
                try:
                    driver.get(page_url)
                    time.sleep(0.8)
                    mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
                    email = mail_el.get_attribute("href").replace("mailto:", "").split("?")[0].strip()
                    if "@" in email:
                        info["email"] = email
                        print(f"      📧 Email (page contact) : {email}")
                        break
                except Exception:
                    continue
        
    except Exception as e:
        print(f"      ⚠ Erreur extraction : {e}")
    
    return info


def scrape_smart_search(driver, query, max_results=15, results_cache=None, seen_urls=None, stop_flag=None, log_fn=None):
    """
    Scraper intelligent pour personnes/entreprises.
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
    
    log(f"🎯 Scraping intelligent : '{query}'")
    
    # 1. Recherche intelligente
    urls = smart_search(driver, query, max_results, pause=1.5)
    
    # Vérifier si arrêt demandé avant de commencer les visites
    if stop_flag and stop_flag():
        log("⏹ Arrêt demandé avant l'analyse des URLs")
        return 0
    
    # 2. Visiter chaque URL
    for i, url in enumerate(urls):
        # Vérifier l'arrêt avant chaque URL
        if stop_flag and stop_flag():
            log(f"⏹ Arrêt demandé après {i} URLs analysées")
            break
            
        if url in seen_urls:
            continue
        
        log(f"  [{i+1}/{len(urls)}] Analyse de : {url}")
        
        info = extract_smart_info(driver, url, query)
        
        # Formater pour le tableau de résultats
        results_cache.append({
            "nom": info["nom"] or "Inconnu",
            "region": "Recherche intelligente",
            "groupe": "Smart Search",
            "email": info["email"],
            "phone": info["phone"],
            "url": info["url"],
            "source": "smart_search",
            "description": info["description"],
            "address": info["address"],
            "social_links": ", ".join(info["social_links"])
        })
        
        seen_urls.add(url)
        log(f"  ✓ [{len(results_cache)}] {info['nom']} | {info['email'] or '—'} | {info['phone'] or '—'}")
        
        # Vérifier l'arrêt après chaque URL
        if stop_flag and stop_flag():
            log(f"⏹ Arrêt demandé après analyse de {url}")
            break
    
    log(f"✅ {len(urls)} URLs analysées, {len(results_cache)} résultats obtenus")
    return len(urls)
