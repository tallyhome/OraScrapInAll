"""
Moteur de scraping générique — supporte tous les groupes de campings.
Chaque source est configurée dans sources.py.
"""
import time
import re
from selenium.webdriver.common.by import By
from urllib.parse import unquote, urlparse
from scrapers.campings.google_strategy import scrape_group_via_google


def scroll_to_bottom(driver, max_scrolls=8, pause=0.4):
    """Scroll jusqu'en bas pour charger le lazy-load."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height and i >= 2:
            break
        last_height = new_height


def extract_contact(driver, url, contact_strategy):
    """
    Extrait email et téléphone depuis une page.
    Strategies:
      - 'page_contact': navigue vers /contact ou /nous-contacter
      - 'sandaya_contact': navigue vers /infos-pratiques
      - 'yelloh_contact': navigue vers /infos_pratiques
      - 'inline': cherche directement sur la page courante
    """
    email = None
    phone = None

    if contact_strategy == "sandaya_contact":
        candidates = [
            url.rstrip("/") + "/infos-pratiques",
            url.rstrip("/") + "/contact",
        ]
        for contact_url in candidates:
            try:
                driver.get(contact_url)
                time.sleep(0.5)
                email, phone = _extract_from_page(driver)
                if email or phone:
                    break
            except Exception:
                continue

    elif contact_strategy == "yelloh_contact":
        candidates = [
            url.rstrip("/") + "/infos_pratiques",
            url.rstrip("/") + "/contact",
        ]
        for contact_url in candidates:
            try:
                driver.get(contact_url)
                time.sleep(0.5)
                email, phone = _extract_from_page(driver)
                if email or phone:
                    break
            except Exception:
                continue

    elif contact_strategy == "page_contact":
        contact_candidates = [
            url.rstrip("/") + "/contact",
            url.rstrip("/") + "/nous-contacter",
            url.rstrip("/") + "/contacter",
        ]
        for contact_url in contact_candidates:
            try:
                driver.get(contact_url)
                time.sleep(0.5)
                email, phone = _extract_from_page(driver)
                if email or phone:
                    break
            except Exception:
                continue
    else:
        # inline : chercher directement sur la page courante
        email, phone = _extract_from_page(driver)

    return email, phone


def _extract_from_page(driver):
    """Extrait email et phone depuis la page courante."""
    email = None
    phone = None

    try:
        mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
        email = mail_el.text.strip() or mail_el.get_attribute("href").replace("mailto:", "")
        if email:
            email = email.split("?")[0].strip()
    except Exception:
        pass

    try:
        tel_el = driver.find_element(By.CSS_SELECTOR, "a[href^='tel']")
        phone = tel_el.text.strip() or tel_el.get_attribute("href").replace("tel:", "")
    except Exception:
        pass

    if not email or not phone:
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            if not email:
                emails_found = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', page_text)
                # Filtrer les emails système (images, fonts, etc.)
                emails_found = [e for e in emails_found if "." in e.split("@")[-1]
                                and not e.endswith((".png", ".jpg", ".css", ".js", ".woff"))]
                if emails_found:
                    email = emails_found[0]
            if not phone:
                phones = re.findall(r'(?:\+33|0)[1-9](?:[\s.\-]?\d{2}){4}', page_text)
                if phones:
                    phone = phones[0].strip()
        except Exception:
            pass

    return email, phone


def is_valid_camping_url(href, source_config):
    """Vérifie si l'URL est bien celle d'un camping (pas une catégorie/région)."""
    if not href:
        return False

    # Exclure les fragments et ancres
    if href.startswith("#") or href.startswith("javascript"):
        return False

    excluded = source_config.get("excluded_url_parts", [])
    if any(ex in href for ex in excluded):
        return False

    # Vérifier le nombre de segments d'URL pour éviter les pages trop génériques
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    allowed_hosts = source_config.get("allowed_hosts")
    if allowed_hosts and host not in allowed_hosts:
        return False
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    min_seg = source_config.get("min_url_segments", 2)
    if len(segments) < min_seg:
        return False
    max_seg = source_config.get("max_url_segments")
    if max_seg is not None and len(segments) > max_seg:
        return False

    return True


def _name_from_url(href):
    """Nom lisible à partir du dernier morceau d'adresse, quand le lien n'a pas de texte."""
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    raw = segments[-1] if segments else host.split(".")[0]
    raw = re.sub(r"(casino|elysees|club)", r" \1 ", raw, flags=re.IGNORECASE)
    raw = raw.replace("-", " ").replace("_", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return " ".join(part.capitalize() for part in raw.split())


def collect_camping_links(driver, source_config):
    """
    Collecte tous les liens de campings sur la page courante.
    Retourne un dict {href: name}.

    collect_mode:
      - 'link_text'     : nom = texte du lien (défaut)
      - 'parent_heading': nom = h2/h3/h4 dans l'élément parent de la carte
    link_text_filter: si défini, ne prendre que les liens dont le texte == cette valeur
    """
    selector = source_config.get("camping_link_selector", "a[href*='camping']")
    collect_mode = source_config.get("collect_mode", "link_text")
    link_text_filter = source_config.get("link_text_filter")  # ex: "Voir camping"

    try:
        links = driver.find_elements(By.CSS_SELECTOR, selector)
    except Exception:
        return {}

    campings_by_url = {}

    for link in links:
        try:
            href = link.get_attribute("href")
            if not href or not is_valid_camping_url(href, source_config):
                continue

            link_text = link.text.strip()

            # Filtre optionnel sur le texte du lien
            if link_text_filter and link_text != link_text_filter:
                continue

            # Récupérer le nom selon le mode
            if collect_mode == "parent_heading":
                name = _get_name_from_parent(link)
            elif collect_mode == "url_slug":
                name = link_text if len(link_text) > 3 else _name_from_url(href)
            else:
                name = link_text

            # Nettoyer
            if not name or len(name) <= 3:
                continue
            name = re.sub(r'^camping\s+', '', name, flags=re.IGNORECASE).strip()
            name = re.sub(r',\s*Camping\s+.+$', '', name).strip()
            if len(name) <= 3:
                continue

            if href not in campings_by_url:
                campings_by_url[href] = name
        except Exception:
            continue

    return campings_by_url


def _get_name_from_parent(link_el, max_levels=8):
    """Remonte dans le DOM pour trouver un titre h2/h3/h4 ou élément [class*=name/title]."""
    try:
        el = link_el
        for _ in range(max_levels):
            el = el.find_element(By.XPATH, "..")
            for tag in ["h2", "h3", "h4"]:
                try:
                    heading = el.find_element(By.TAG_NAME, tag)
                    text = heading.text.strip()
                    if text and len(text) > 3:
                        return text
                except Exception:
                    pass
            # Essayer les classes name/title
            for css in ["[class*='name']", "[class*='title']", "[class*='nom']"]:
                try:
                    el2 = el.find_element(By.CSS_SELECTOR, css)
                    text = el2.text.strip()
                    if text and len(text) > 3:
                        return text
                except Exception:
                    pass
    except Exception:
        pass
    return None


def scrape_generic_group(driver, source_id, source_config, max_results,
                          keyword=None, results_cache=None, seen_urls_global=None,
                          stop_flag=None, log_fn=None):
    """
    Moteur générique pour les groupes de campings.
    Navigue sur la page principale, collecte les liens, visite chaque camping.
    """
    if results_cache is None:
        results_cache = []
    if seen_urls_global is None:
        seen_urls_global = set()

    def log(msg):
        if log_fn:
            log_fn(msg)
        else:
            print(msg)

    count_added = 0
    label = source_config["label"]

    try:
        log(f"\n[{label}] Chargement de la page principale...")
        driver.get(source_config["index_url"])
        time.sleep(2)
        scroll_to_bottom(driver, max_scrolls=6, pause=0.4)

        campings_by_url = collect_camping_links(driver, source_config)
        log(f"[{label}] {len(campings_by_url)} campings détectés")

        if not campings_by_url:
            log(f"[{label}] ⚠ Aucun camping trouvé. Vérifier le sélecteur CSS ou la structure du site.")
            return count_added

        # Filtrer keyword + doublons globaux
        to_scrape = []
        for href, name in campings_by_url.items():
            if href in seen_urls_global:
                continue
            if keyword:
                stop_words = {'camping', 'village', 'le', 'la', 'les', 'de', 'des', 'du', 'en'}
                keywords = [k.strip().lower() for k in keyword.split()
                            if len(k.strip()) > 2 and k.strip().lower() not in stop_words]
                if keywords and not any(k in name.lower() or k in href.lower() for k in keywords):
                    continue
            to_scrape.append((name, href))

        log(f"[{label}] {len(to_scrape)} campings à scraper après filtres")

        contact_strategy = source_config.get("contact_strategy", "page_contact")

        # Stratégie Google : chercher le site officiel de chaque camping sur Google
        google_strategies = {"sandaya_contact", "yelloh_contact", "google"}
        if contact_strategy in google_strategies:
            count_added = scrape_group_via_google(
                driver=driver,
                source_id=source_id,
                source_config=source_config,
                campings_list=to_scrape,
                max_results=max_results,
                results_cache=results_cache,
                seen_urls_global=seen_urls_global,
                stop_flag=stop_flag,
                log_fn=log,
            )
        else:
            for name, href in to_scrape:
                if stop_flag and stop_flag():
                    break
                if len(results_cache) >= max_results:
                    break

                email, phone = None, None
                try:
                    driver.get(href)
                    time.sleep(0.5)
                    email, phone = extract_contact(driver, href, contact_strategy)
                except Exception as e:
                    log(f"  ⚠ Erreur {name}: {e}")

                results_cache.append({
                    "nom": name,
                    "region": label,
                    "groupe": label,
                    "email": email,
                    "phone": phone,
                    "url": href,
                    "source": source_id,
                })
                seen_urls_global.add(href)
                count_added += 1
                log(f"  ✓ [{len(results_cache)}] {name} | {email or '—'} | {phone or '—'}")

    except Exception as e:
        log(f"[{label}] Erreur générale: {e}")

    log(f"[{label}] ✅ {count_added} campings ajoutés")
    return count_added
