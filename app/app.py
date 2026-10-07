import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "app" else _HERE
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import Flask, render_template, request, jsonify, send_file
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
import pandas as pd
import time
import re
import threading
import json
from datetime import datetime
from io import BytesIO
from scrapers.sources import SOURCES, SOURCE_GROUPS
from scrapers.engine import scrape_generic_group, scroll_to_bottom
from scrapers.universal_scraper import scrape_universal
from scrapers.smart_search import scrape_smart_search
from scrapers.mairies import collect_mairies, load_geo
from scrapers.workspace import (
    add_project, add_proxy, add_schedule, choose_proxy, chrome_proxy,
    delete_project, delete_proxy, delete_schedule, due_schedule,
    load_activity, load_exports, load_projects, load_schedules, load_settings,
    log_activity, mark_schedule_ran, next_schedule, public_proxies,
    record_export, save_settings, test_proxy, update_schedule,
)

app = Flask(__name__)

# Cache pour stocker les résultats
results_cache = []
search_in_progress = False
search_stop_requested = False
search_detail = ""
search_revision = 0
job_meta = {}

def _project_root():
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) == "app":
        return os.path.dirname(here)
    return here


# Historique des recherches (fichier JSON), hors du code pour survivre aux mises à jour
HISTORY_FILE = os.path.join(_project_root(), "data", "search_history.json")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erreur sauvegarde historique: {e}")

def add_to_history(keyword, max_results, results):
    history = load_history()
    entry = {
        "id": int(time.time() * 1000),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "keyword": keyword or "(tous)",
        "max_results": max_results,
        "count": len(results),
        "results": results,
        "project_id": job_meta.get("project_id"),
        "mode": job_meta.get("mode") or "",
        "schedule_id": job_meta.get("schedule_id"),
    }
    log_activity("INFO", f"Terminé : {entry['keyword']} · {entry['count']} fiches")
    history.insert(0, entry)
    # Garder les 200 dernières recherches (chaque recherche peut contenir des centaines de campings)
    history = history[:200]
    save_history(history)

def init_driver(debug_mode=False):
    options = Options()
    if not debug_mode:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    # Optimisations: bloquer images, CSS, fonts pour accélérer
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.fonts": 2,
        "profile.managed_default_content_settings.media_stream": 2,
        "profile.managed_default_content_settings.plugins": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.page_load_strategy = 'eager'  # Ne pas attendre tous les sous-resources
    proxy = choose_proxy()
    packed = chrome_proxy(proxy)
    if packed:
        if packed.get("server"):
            options.add_argument(packed["server"])
        if packed.get("extension"):
            options.add_argument("--load-extension=" + packed["extension"])
        log_activity("INFO", f"Chrome via le proxy {proxy.get('label') or proxy.get('host')}")
    return webdriver.Chrome(options=options)


def remember_job(data, mode):
    global job_meta
    project_id = (data or {}).get("project_id")
    try:
        project_id = int(project_id) if project_id else None
    except (TypeError, ValueError):
        project_id = None
    schedule_id = (data or {}).get("schedule_id")
    try:
        schedule_id = int(schedule_id) if schedule_id else None
    except (TypeError, ValueError):
        schedule_id = None
    job_meta = {"project_id": project_id, "mode": mode, "schedule_id": schedule_id}
    log_activity("INFO", f"Recherche {mode} lancée")

def is_camping_url(url):
    """Détecte si c'est un vrai camping (pas une région ni un département)"""
    if not url:
        return False
    # Un vrai camping a le format: camping-france-[region]-[nom]-FR.html
    # Régions: camping-france-[region]-FR-region.html
    # Départements: camping-france-[region]-[dept]-FR-departement.html
    # Pays: camping-[pays]-FR-pays.html
    if "camping-france-" not in url:
        return False
    # Exclure les pages catégories
    excluded = ["-FR-region.html", "-FR-pays.html", "-FR-departement.html"]
    if any(x in url for x in excluded):
        return False
    # Doit finir par -FR.html (vrai camping)
    if not url.endswith("-FR.html"):
        return False
    return True

def search_capfun(max_results=50, keyword=None, debug_mode=False):
    """Recherche les campings Capfun avec leur email"""
    global results_cache, search_in_progress, search_stop_requested
    search_in_progress = True
    search_stop_requested = False
    results_cache = []
    
    driver = init_driver(debug_mode)
    count = 0
    
    try:
        # Étape 1: Aller sur la page principale et récupérer les régions
        driver.get("https://www.capfun.com/camping-piscine-france-FR.html")
        time.sleep(1.5)
        
        # Scroller pour charger tout le contenu
        for _ in range(2):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.3)
        
        # Récupérer tous les liens de régions
        region_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='camping-france-']")
        print(f"Liens trouvés sur page principale: {len(region_links)}")
        
        regions = []
        seen_regions = set()
        
        for i, link in enumerate(region_links[:10]):  # Debug: voir les 10 premiers
            try:
                name = link.text.strip()
                href = link.get_attribute("href")
                print(f"  Lien {i}: '{name}' -> {href[:80]}...")
            except:
                pass
        
        for link in region_links:
            try:
                name = link.text.strip()
                href = link.get_attribute("href")
                
                # Une région contient "Campings en" ou "Campings aux"
                if name and href and ("Campings en" in name or "Campings aux" in name):
                    # Exclure les pseudo-régions trop globales (pages pays/monde)
                    excluded = {'Campings en France', 'Campings en Europe', 'Campings en Espagne',
                               'Campings en Italie', 'Campings en Belgique', 'Campings en Angleterre',
                               'Campings en Allemagne', 'Campings aux Pays_bas', 'Campings aux Pays-Bas'}
                    if name in excluded:
                        continue
                    # Exclure aussi via URL (pages -FR-pays.html)
                    if href.endswith('-FR-pays.html') or '/camping-france-FR' in href:
                        continue
                    if name not in seen_regions and len(name) < 60:
                        seen_regions.add(name)
                        regions.append({"name": name, "url": href})
                        print(f"  [RÉGION] {name}")
            except:
                pass
        
        print(f"\nTotal régions trouvées: {len(regions)}")
        
        # Set global pour éviter doublons entre régions (même camping listé dans plusieurs régions)
        seen_urls_global = set()
        
        # Étape 2: Pour chaque région, récupérer les campings
        for region in regions:
            if search_stop_requested:
                break
            if count >= max_results:
                break
            
            try:
                driver.get(region['url'])
                time.sleep(1)
                
                # Scroller plusieurs fois pour charger tous les campings (lazy-load)
                last_height = driver.execute_script("return document.body.scrollHeight")
                for i in range(8):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(0.4)
                    new_height = driver.execute_script("return document.body.scrollHeight")
                    if new_height == last_height and i >= 2:
                        break  # Plus de nouveau contenu chargé
                    last_height = new_height
                
                # ÉTAPE A : Collecter TOUS les campings de la région d'abord
                # (avant de naviguer ailleurs, pour éviter les "stale element")
                camping_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='camping-france-']")
                print(f"    Liens trouvés dans {region['name']}: {len(camping_links)}")
                
                # Dédupliquer par URL (clé), garder le meilleur nom
                # Mots/textes à ignorer pour le nom
                bad_names = {'cliquez ici', 'voir', 'plus', 'détails', 'en savoir plus', 'réserver'}
                campings_by_url = {}  # href -> best_name
                
                for link in camping_links:
                    try:
                        name = link.text.strip()
                        href = link.get_attribute("href")
                        
                        if not (name and href and is_camping_url(href)):
                            continue
                        if len(name) <= 3:
                            continue
                        # Ignorer les noms génériques
                        if name.lower() in bad_names:
                            continue
                        
                        # Nettoyer le nom : enlever ", Camping XYZ Région"
                        clean_name = re.sub(r',\s*Camping\s+.+$', '', name).strip()
                        if len(clean_name) > 3:
                            name = clean_name
                        
                        # Garder le meilleur nom : préférer le plus court mais propre
                        # (évite les noms-spam très longs avec virgules)
                        if href in campings_by_url:
                            existing = campings_by_url[href]
                            # Préférer le plus court qui ne contient pas de virgule
                            if ',' in existing and ',' not in name:
                                campings_by_url[href] = name
                            elif ',' not in existing and ',' not in name and len(name) < len(existing):
                                campings_by_url[href] = name
                        else:
                            campings_by_url[href] = name
                    except:
                        pass
                
                # Filtrer par mot-clé ET ignorer campings déjà scrapés (autres régions)
                campings_in_region = []
                skipped_duplicates = 0
                for href, name in campings_by_url.items():
                    # Skip si déjà scrapé dans une région précédente
                    if href in seen_urls_global:
                        skipped_duplicates += 1
                        continue
                    
                    if keyword:
                        stop_words = {'camping', 'capfun', 'sandaya', 'le', 'la', 'les', 'de', 'des', 'du'}
                        keywords = [k.strip().lower() for k in keyword.split() 
                                   if len(k.strip()) > 2 and k.strip().lower() not in stop_words]
                        if keywords and not any(k in name.lower() for k in keywords):
                            continue
                    campings_in_region.append((name, href))
                
                if skipped_duplicates:
                    print(f"    ⊘ {skipped_duplicates} doublons ignorés (déjà vus ailleurs)")
                print(f"    → {len(campings_in_region)} nouveaux campings dans {region['name']}")
                
                # ÉTAPE B : Pour chaque camping, visiter sa page contact pour récupérer email/tél
                camps_added = 0
                for name, href in campings_in_region:
                    if search_stop_requested or count >= max_results:
                        break
                    
                    email = None
                    phone = None
                    try:
                        contact_url = href.replace("camping-", "contacter-camping-")
                        driver.get(contact_url)
                        time.sleep(0.5)
                        
                        try:
                            mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
                            email = mail_el.text.strip() or mail_el.get_attribute("href").replace("mailto:", "")
                        except:
                            pass
                        
                        try:
                            tel_el = driver.find_element(By.CSS_SELECTOR, "a[href^='tel']")
                            phone = tel_el.text.strip() or tel_el.get_attribute("href").replace("tel:", "")
                        except:
                            pass
                        
                        if not email or not phone:
                            try:
                                page_text = driver.find_element(By.TAG_NAME, "body").text
                                if not email:
                                    emails_found = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', page_text)
                                    if emails_found:
                                        email = emails_found[0]
                                if not phone:
                                    phones = re.findall(r'(?:\+33|0)[1-9](?:[\s.-]?\d{2}){4}', page_text)
                                    if phones:
                                        phone = phones[0].strip()
                            except:
                                pass
                    except Exception as e:
                        print(f"    ⚠ Erreur contact {name}: {e}")
                    
                    results_cache.append({
                        "nom": name,
                        "region": region['name'],
                        "email": email,
                        "phone": phone,
                        "url": href
                    })
                    seen_urls_global.add(href)
                    count += 1
                    camps_added += 1
                    print(f"    ✓ [{count}] {name} | {email or '—'} | {phone or '—'}")
                
                print(f"    ✅ {camps_added} campings ajoutés depuis {region['name']}\n")
                        
            except Exception as e:
                print(f"Erreur région {region['name']}: {e}")
                pass
    
    finally:
        try:
            driver.quit()
        except:
            pass
        search_in_progress = False
        # Sauvegarder dans l'historique
        if results_cache:
            try:
                add_to_history(keyword, max_results, results_cache.copy())
            except Exception as e:
                print(f"Erreur historique: {e}")
        print(f"Recherche terminée: {count} campings trouvés")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/sources')
def get_sources():
    """Retourne la liste des sources disponibles groupées"""
    result = {}
    for group_name, source_ids in SOURCE_GROUPS.items():
        result[group_name] = [
            {"id": sid, "label": SOURCES[sid]["label"], "logo": SOURCES[sid]["logo"]}
            for sid in source_ids if sid in SOURCES
        ]
    return jsonify(result)


def search_multi(sources, max_results, keyword, debug_mode):
    """Lance la recherche sur plusieurs sources."""
    global results_cache, search_in_progress, search_stop_requested
    search_in_progress = True
    search_stop_requested = False
    results_cache = []

    driver = init_driver(debug_mode)
    seen_urls_global = set()

    def stop_flag():
        return search_stop_requested

    try:
        for source_id in sources:
            if search_stop_requested:
                break
            if len(results_cache) >= max_results:
                break

            source_config = SOURCES.get(source_id)
            if not source_config:
                print(f"Source inconnue: {source_id}")
                continue

            source_type = source_config.get("type")

            if source_type == "capfun":
                # Utiliser le scraper Capfun existant (adapté)
                _scrape_capfun_into_cache(
                    driver, max_results, keyword, seen_urls_global, stop_flag
                )
            elif source_type in ("generic_group", "annuaire"):
                scrape_generic_group(
                    driver=driver,
                    source_id=source_id,
                    source_config=source_config,
                    max_results=max_results,
                    keyword=keyword,
                    results_cache=results_cache,
                    seen_urls_global=seen_urls_global,
                    stop_flag=stop_flag,
                    log_fn=print,
                )
            else:
                print(f"Type de source non supporté: {source_type}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        search_in_progress = False
        if results_cache:
            try:
                sources_label = ", ".join(SOURCES[s]["label"] for s in sources if s in SOURCES)
                kw = keyword or "(tous)"
                add_to_history(f"{kw} [{sources_label}]", max_results, results_cache.copy())
            except Exception as e:
                print(f"Erreur historique: {e}")
        print(f"Recherche terminée: {len(results_cache)} campings trouvés")


def _scrape_capfun_into_cache(driver, max_results, keyword, seen_urls_global, stop_flag):
    """Extrait la logique Capfun pour l'utiliser dans search_multi."""
    global results_cache
    count = len(results_cache)

    driver.get("https://www.capfun.com/camping-piscine-france-FR.html")
    time.sleep(1.5)
    for _ in range(2):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.3)

    region_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='camping-france-']")
    regions = []
    seen_regions = set()
    for link in region_links:
        try:
            name = link.text.strip()
            href = link.get_attribute("href")
            if name and href and ("Campings en" in name or "Campings aux" in name):
                excluded = {'Campings en France', 'Campings en Europe', 'Campings en Espagne',
                            'Campings en Italie', 'Campings en Belgique', 'Campings en Angleterre',
                            'Campings en Allemagne', 'Campings aux Pays_bas', 'Campings aux Pays-Bas'}
                if name in excluded:
                    continue
                if href.endswith('-FR-pays.html') or '/camping-france-FR' in href:
                    continue
                if name not in seen_regions and len(name) < 60:
                    seen_regions.add(name)
                    regions.append({"name": name, "url": href})
        except Exception:
            pass

    print(f"[Capfun] {len(regions)} régions")

    for region in regions:
        if stop_flag() or len(results_cache) >= max_results:
            break
        try:
            driver.get(region['url'])
            time.sleep(1)
            scroll_to_bottom(driver, max_scrolls=8, pause=0.4)

            camping_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='camping-france-']")
            bad_names = {'cliquez ici', 'voir', 'plus', 'détails', 'en savoir plus', 'réserver'}
            campings_by_url = {}
            for link in camping_links:
                try:
                    name = link.text.strip()
                    href = link.get_attribute("href")
                    if not (name and href and is_camping_url(href)):
                        continue
                    if len(name) <= 3 or name.lower() in bad_names:
                        continue
                    clean_name = re.sub(r',\s*Camping\s+.+$', '', name).strip()
                    if len(clean_name) > 3:
                        name = clean_name
                    if href in campings_by_url:
                        existing = campings_by_url[href]
                        if ',' in existing and ',' not in name:
                            campings_by_url[href] = name
                        elif ',' not in existing and ',' not in name and len(name) < len(existing):
                            campings_by_url[href] = name
                    else:
                        campings_by_url[href] = name
                except Exception:
                    pass

            campings_in_region = []
            for href, name in campings_by_url.items():
                if href in seen_urls_global:
                    continue
                if keyword:
                    stop_words = {'camping', 'capfun', 'sandaya', 'le', 'la', 'les', 'de', 'des', 'du'}
                    keywords = [k.strip().lower() for k in keyword.split()
                                if len(k.strip()) > 2 and k.strip().lower() not in stop_words]
                    if keywords and not any(k in name.lower() for k in keywords):
                        continue
                campings_in_region.append((name, href))

            for name, href in campings_in_region:
                if stop_flag() or len(results_cache) >= max_results:
                    break
                email = None
                phone = None
                try:
                    contact_url = href.replace("camping-", "contacter-camping-")
                    driver.get(contact_url)
                    time.sleep(0.5)
                    try:
                        mail_el = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']")
                        email = mail_el.text.strip() or mail_el.get_attribute("href").replace("mailto:", "")
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
                                if emails_found:
                                    email = emails_found[0]
                            if not phone:
                                phones = re.findall(r'(?:\+33|0)[1-9](?:[\s.-]?\d{2}){4}', page_text)
                                if phones:
                                    phone = phones[0].strip()
                        except Exception:
                            pass
                except Exception as e:
                    print(f"  ⚠ Erreur contact {name}: {e}")

                results_cache.append({
                    "nom": name,
                    "region": region['name'],
                    "groupe": "Capfun",
                    "email": email,
                    "phone": phone,
                    "url": href,
                    "source": "capfun",
                })
                seen_urls_global.add(href)
                print(f"  ✓ [{len(results_cache)}] {name} | {email or '—'} | {phone or '—'}")

            print(f"  ✅ {region['name']} terminé")
        except Exception as e:
            print(f"Erreur région {region['name']}: {e}")


@app.route('/search', methods=['POST'])
def search():
    global search_in_progress, search_stop_requested

    if search_in_progress:
        return jsonify({"error": "Une recherche est déjà en cours"}), 429

    data = request.json
    keyword = data.get('keyword', '').strip() or None
    try:
        max_results = int(data.get('max_results') or 50)
        if max_results <= 0:
            max_results = 50
    except (ValueError, TypeError):
        max_results = 50
    debug_mode = data.get('debug_mode', False)
    sources = data.get('sources', ['capfun'])  # défaut: Capfun uniquement
    if not sources:
        sources = ['capfun']

    remember_job(data, "camping")
    thread = threading.Thread(target=search_multi, args=(sources, max_results, keyword, debug_mode))
    thread.start()

    return jsonify({"message": "Recherche lancée", "status": "running"})


@app.route('/search_universal', methods=['POST'])
def search_universal_route():
    global search_in_progress, search_stop_requested

    if search_in_progress:
        return jsonify({"error": "Une recherche est déjà en cours"}), 429

    data = request.json
    query = data.get('query', '').strip()
    if not query:
        return jsonify({"error": "Veuillez entrer une recherche"}), 400

    try:
        max_results = int(data.get('max_results') or 20)
        if max_results <= 0:
            max_results = 20
    except (ValueError, TypeError):
        max_results = 20

    debug_mode = data.get('debug_mode', False)

    remember_job(data, "universal")
    thread = threading.Thread(target=_search_universal_worker, args=(query, max_results, debug_mode))
    thread.start()

    return jsonify({"message": "Recherche universelle lancée", "status": "running"})


def _search_universal_worker(query, max_results, debug_mode):
    """Worker pour le scraper universel."""
    global results_cache, search_in_progress, search_stop_requested
    search_in_progress = True
    search_stop_requested = False
    results_cache = []

    driver = init_driver(debug_mode)
    seen_urls = set()

    def stop_flag():
        return search_stop_requested

    try:
        scrape_universal(
            driver=driver,
            query=query,
            max_results=max_results,
            results_cache=results_cache,
            seen_urls=seen_urls,
            stop_flag=stop_flag,
            log_fn=print,
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        search_in_progress = False
        if results_cache:
            try:
                add_to_history(f"Universel: {query}", max_results, results_cache.copy())
            except Exception as e:
                print(f"Erreur historique: {e}")
        print(f"Recherche universelle terminée: {len(results_cache)} résultats")


@app.route('/search_smart', methods=['POST'])
def search_smart_route():
    global search_in_progress, search_stop_requested

    if search_in_progress:
        return jsonify({"error": "Une recherche est déjà en cours"}), 429

    data = request.json
    query = data.get('query', '').strip()
    if not query:
        return jsonify({"error": "Veuillez entrer une recherche"}), 400

    try:
        max_results = int(data.get('max_results') or 15)
        if max_results <= 0:
            max_results = 15
    except (ValueError, TypeError):
        max_results = 15

    debug_mode = data.get('debug_mode', False)

    remember_job(data, "smart")
    thread = threading.Thread(target=_search_smart_worker, args=(query, max_results, debug_mode))
    thread.start()

    return jsonify({"message": "Recherche intelligente lancée", "status": "running"})


def _search_smart_worker(query, max_results, debug_mode):
    """Worker pour le scraper intelligent."""
    global results_cache, search_in_progress, search_stop_requested
    search_in_progress = True
    search_stop_requested = False
    results_cache = []

    driver = init_driver(debug_mode)
    seen_urls = set()

    def stop_flag():
        return search_stop_requested

    try:
        scrape_smart_search(
            driver=driver,
            query=query,
            max_results=max_results,
            results_cache=results_cache,
            seen_urls=seen_urls,
            stop_flag=stop_flag,
            log_fn=print,
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        search_in_progress = False
        if results_cache:
            try:
                add_to_history(f"Smart: {query}", max_results, results_cache.copy())
            except Exception as e:
                print(f"Erreur historique: {e}")
        print(f"Recherche intelligente terminée: {len(results_cache)} résultats")


@app.route('/geo/regions')
def geo_regions():
    try:
        regions, _ = load_geo()
    except Exception as e:
        return jsonify({"error": f"Annuaire géographique indisponible: {e}"}), 502
    return jsonify([{"code": r["code"], "nom": r["nom"]} for r in regions])


@app.route('/geo/departements')
def geo_departements():
    try:
        _, departements = load_geo()
    except Exception as e:
        return jsonify({"error": f"Annuaire géographique indisponible: {e}"}), 502
    region = (request.args.get("region") or "").strip()
    rows = []
    for dep in departements:
        if region and dep.get("codeRegion") != region:
            continue
        rows.append({
            "code": dep["code"],
            "nom": dep["nom"],
            "codeRegion": dep.get("codeRegion") or "",
            "region": dep.get("region") or "",
        })
    return jsonify(rows)


@app.route('/search_mairies', methods=['POST'])
def search_mairies_route():
    global search_in_progress, search_stop_requested

    if search_in_progress:
        return jsonify({"error": "Une recherche est déjà en cours"}), 429

    data = request.json or {}
    region = (data.get("region") or "").strip()
    departement = (data.get("departement") or "").strip()
    all_france = region == "ALL"
    if all_france:
        departement = ""
    elif not region and not departement:
        return jsonify({"error": "Choisissez toute la France, une région ou un département"}), 400

    scan_sites = bool(data.get("scan_sites", True))
    try:
        max_results = int(data.get("max_results") or 0)
        if max_results < 0:
            max_results = 0
    except (ValueError, TypeError):
        max_results = 0
    try:
        min_population = int(data.get("min_population") or 0)
        if min_population < 0:
            min_population = 0
    except (ValueError, TypeError):
        min_population = 0

    allowed = {"mairie", "epci", "ot", "fete", "asso", "ce", "prod", "cd", "cr", "scene"}
    raw_kinds = data.get("kinds") or ["mairie", "epci"]
    if isinstance(raw_kinds, str):
        raw_kinds = [raw_kinds]
    kinds = [kind for kind in raw_kinds if kind in allowed]
    if not kinds:
        return jsonify({"error": "Choisissez au moins un type de structure"}), 400

    remember_job(data, "mairie")
    thread = threading.Thread(
        target=_search_mairies_worker,
        args=(region, departement, scan_sites, max_results, all_france, min_population, kinds),
    )
    thread.start()
    return jsonify({"message": "Recherche mairies lancée", "status": "running"})


def _search_mairies_worker(region, departement, scan_sites, max_results, all_france=False, min_population=0, kinds=None):
    global results_cache, search_in_progress, search_stop_requested, search_detail, search_revision
    search_in_progress = True
    search_stop_requested = False
    results_cache = []
    search_detail = "Chargement de l'annuaire des mairies…"
    search_revision += 1

    def stop_flag():
        return search_stop_requested

    def on_detail(message):
        global search_detail, search_revision
        search_detail = message
        search_revision += 1

    label = "Toute la France" if all_france else (departement or region or "France")
    try:
        _, departements = load_geo()
        regions, _ = load_geo()
        if all_france:
            pass
        elif departement:
            match = next((d for d in departements if d["code"] == departement), None)
            if match:
                label = match["nom"]
                if match.get("region"):
                    label = f"{match['region']} — {match['nom']}"
        elif region:
            match = next((r for r in regions if r["code"] == region), None)
            if match:
                label = match["nom"]
        collect_mairies(
            region_code=region,
            departement_code=departement,
            results_cache=results_cache,
            stop_flag=stop_flag,
            scan_sites=scan_sites,
            max_results=max_results,
            on_detail=on_detail,
            all_france=all_france,
            min_population=min_population,
            kinds=kinds,
        )
    except Exception as e:
        print(f"Erreur mairies: {e}")
        search_detail = f"Erreur : {e}"
    finally:
        search_in_progress = False
        search_revision += 1
        if results_cache:
            try:
                add_to_history(f"Mairies: {label}", max_results or len(results_cache), results_cache.copy())
            except Exception as e:
                print(f"Erreur historique: {e}")
        print(f"Recherche mairies terminée: {len(results_cache)} résultats")


@app.route('/stop', methods=['POST'])
def stop_search():
    global search_stop_requested
    search_stop_requested = True
    return jsonify({"message": "Arrêt demandé"})

@app.route('/status')
def status():
    return jsonify({
        "in_progress": search_in_progress,
        "count": len(results_cache),
        "detail": search_detail,
        "revision": search_revision,
    })

@app.route('/results')
def results():
    return jsonify(results_cache)

@app.route('/export')
def export():
    """Export les résultats actuels.
    Params:
      - format: 'xlsx' (défaut) ou 'csv'
      - mode: 'all' (défaut), 'emails' (que campings avec email), 'emails_only' (que la colonne email)
      - history_id: si fourni, exporte depuis l'historique au lieu du cache courant
    """
    fmt = request.args.get('format', 'xlsx').lower()
    mode = request.args.get('mode', 'all').lower()
    history_id = request.args.get('history_id')
    
    # Récupérer les données (cache courant ou historique)
    if history_id:
        history = load_history()
        entry = next((h for h in history if str(h['id']) == str(history_id)), None)
        if not entry:
            return "Recherche introuvable", 404
        data = entry['results']
        suffix = re.sub(r"[^\w\-]+", "_", entry['keyword'] or "export", flags=re.UNICODE).strip("_")[:40] or "export"
    else:
        if not results_cache:
            return "Aucun résultat", 404
        data = results_cache
        suffix = "current"
    
    is_mairie = bool(data) and data[0].get("source") == "mairie"

    # Filtrer selon le mode
    if mode == 'emails':
        if is_mairie:
            data = [d for d in data if d.get('email_mairie') or d.get('email_socioculturel')]
        else:
            data = [d for d in data if d.get('email')]
    
    if not data:
        return "Aucun résultat à exporter", 404
    
    df = pd.DataFrame(data)

    if is_mairie:
        preferred = [
            "region", "departement", "groupe", "commune", "population", "code_postal", "code_insee",
            "email_mairie", "email_socioculturel", "service_socioculturel",
            "phone", "adresse", "url", "recherche_url",
        ]
        columns = [c for c in preferred if c in df.columns]
        df = df[columns]
    
    # Mode "emails_only" = ne garder que la colonne email
    if mode == 'emails_only':
        if is_mairie:
            keep = [c for c in ["commune", "email_mairie", "email_socioculturel"] if c in df.columns]
            df = df[keep]
            if "email_mairie" in df.columns or "email_socioculturel" in df.columns:
                has_mail = False
                for col in ("email_mairie", "email_socioculturel"):
                    if col in df.columns:
                        has_mail = has_mail | (df[col].fillna("") != "")
                df = df[has_mail]
        elif 'email' in df.columns:
            df = df[df['email'].notna() & (df['email'] != '')][['email']]
        else:
            return "Pas d'emails", 404
        if df.empty:
            return "Pas d'emails", 404

    if is_mairie:
        df = df.rename(columns={
            "region": "Région",
            "departement": "Département",
            "groupe": "Type",
            "commune": "Nom",
            "population": "Habitants",
            "code_postal": "Code postal",
            "code_insee": "Code INSEE",
            "email_mairie": "E-mail",
            "email_socioculturel": "E-mail socioculturel",
            "service_socioculturel": "Service socioculturel",
            "phone": "Téléphone",
            "adresse": "Adresse",
            "url": "Site",
            "recherche_url": "Piste de recherche",
        })
    
    # Générer le fichier en mémoire
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    buffer = BytesIO()
    prefix = "mairies" if is_mairie else "campings"
    
    if fmt == 'csv':
        df.to_csv(buffer, index=False, encoding='utf-8-sig', sep=';')
        filename = f"{prefix}_{suffix}_{timestamp}.csv"
        mimetype = 'text/csv'
    else:
        df.to_excel(buffer, index=False, engine='openpyxl')
        filename = f"{prefix}_{suffix}_{timestamp}.xlsx"
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    
    buffer.seek(0)
    record_export(filename, int(len(df)), "csv" if fmt == "csv" else "xlsx", history_id)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=filename)

@app.route('/history')
def get_history():
    """Retourne la liste de l'historique (sans les résultats détaillés pour alléger)"""
    history = load_history()
    summary = [{
        "id": h['id'],
        "date": h['date'],
        "keyword": h['keyword'],
        "max_results": h.get('max_results', 0),
        "count": h['count'],
        "project_id": h.get("project_id"),
        "mode": h.get("mode") or "",
    } for h in history]
    return jsonify(summary)

@app.route('/history/<int:history_id>')
def get_history_entry(history_id):
    """Retourne les résultats d'une recherche de l'historique"""
    history = load_history()
    entry = next((h for h in history if h['id'] == history_id), None)
    if not entry:
        return jsonify({"error": "Recherche introuvable"}), 404
    return jsonify(entry)

@app.route('/history/<int:history_id>', methods=['DELETE'])
def delete_history_entry(history_id):
    """Supprime une entrée de l'historique"""
    history = load_history()
    history = [h for h in history if h['id'] != history_id]
    save_history(history)
    return jsonify({"message": "Supprimé"})

@app.route('/history/clear', methods=['POST'])
def clear_history():
    save_history([])
    return jsonify({"message": "Historique vidé"})


def _row_has_email(row):
    for key in ("email", "email_mairie", "email_socioculturel"):
        if str(row.get(key) or "").strip():
            return True
    return False


def _contact_rows():
    history = load_history()
    rows = []
    for entry in history:
        for item in entry.get("results") or []:
            if not isinstance(item, dict):
                continue
            email = item.get("email") or item.get("email_mairie") or ""
            rows.append({
                "history_id": entry.get("id"),
                "date": entry.get("date") or "",
                "keyword": entry.get("keyword") or "",
                "project_id": entry.get("project_id"),
                "name": item.get("commune") or item.get("nom") or "",
                "groupe": item.get("groupe") or item.get("source") or "",
                "email": email,
                "email_extra": item.get("email_socioculturel") or "",
                "phone": item.get("phone") or "",
                "city": item.get("departement") or item.get("region") or "",
                "population": item.get("population"),
                "url": item.get("url") or "",
            })
    return rows


@app.route('/api/overview')
def api_overview():
    history = load_history()
    rows = 0
    mailed = 0
    scanned = 0
    by_day = {}
    for entry in history:
        count = int(entry.get("count") or 0)
        rows += count
        day = str(entry.get("date") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + count
        for item in entry.get("results") or []:
            if not isinstance(item, dict):
                continue
            scanned += 1
            if _row_has_email(item):
                mailed += 1
    today = datetime.now().date()
    series = []
    for offset in range(6, -1, -1):
        day = today.fromordinal(today.toordinal() - offset)
        key = day.strftime("%Y-%m-%d")
        series.append({"day": key, "label": day.strftime("%d/%m"), "rows": by_day.get(key, 0)})
    recent = [{
        "id": entry.get("id"),
        "date": entry.get("date"),
        "keyword": entry.get("keyword"),
        "count": entry.get("count") or 0,
        "mode": entry.get("mode") or "",
        "project_id": entry.get("project_id"),
    } for entry in history[:8]]
    rate = round(100 * mailed / scanned, 1) if scanned else 0
    return jsonify({
        "jobs": len(history),
        "rows": rows,
        "emails": mailed,
        "email_rate": rate,
        "running": search_in_progress,
        "detail": search_detail,
        "live_count": len(results_cache),
        "series": series,
        "recent": recent,
        "next_schedule": next_schedule(),
        "projects": len(load_projects()),
        "proxies": len([row for row in public_proxies() if row.get("enabled")]),
    })


@app.route('/api/activity')
def api_activity():
    return jsonify(load_activity(30))


@app.route('/api/projects', methods=['GET', 'POST'])
def api_projects():
    if request.method == 'POST':
        data = request.json or {}
        try:
            row = add_project(data.get("name"), data.get("note") or "")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(row)
    return jsonify(load_projects())


@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def api_project_delete(project_id):
    delete_project(project_id)
    return jsonify({"message": "Supprimé"})


@app.route('/api/schedules', methods=['GET', 'POST'])
def api_schedules():
    if request.method == 'POST':
        try:
            row = add_schedule(request.json or {})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(row)
    return jsonify(load_schedules())


@app.route('/api/schedules/<int:schedule_id>', methods=['PATCH', 'DELETE'])
def api_schedule_one(schedule_id):
    if request.method == 'DELETE':
        delete_schedule(schedule_id)
        return jsonify({"message": "Supprimé"})
    try:
        row = update_schedule(schedule_id, request.json or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(row)


@app.route('/api/schedules/<int:schedule_id>/run', methods=['POST'])
def api_schedule_run(schedule_id):
    job = next((row for row in load_schedules() if row.get("id") == schedule_id), None)
    if not job:
        return jsonify({"error": "Planification introuvable"}), 404
    if not _launch_schedule(job, mark=True):
        return jsonify({"error": "Une recherche est déjà en cours"}), 429
    return jsonify({"message": "Lancée"})


@app.route('/api/contacts')
def api_contacts():
    query = (request.args.get("q") or "").strip().lower()
    try:
        limit = min(400, max(1, int(request.args.get("limit") or 120)))
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        limit, offset = 120, 0
    only_email = request.args.get("email") == "1"
    matched = []
    for row in _contact_rows():
        if only_email and not (row["email"] or row["email_extra"]):
            continue
        blob = " ".join(str(row.get(key) or "") for key in ("name", "email", "email_extra", "phone", "groupe", "city", "keyword")).lower()
        if query and query not in blob:
            continue
        matched.append(row)
    return jsonify({"total": len(matched), "rows": matched[offset:offset + limit]})


@app.route('/api/exports')
def api_exports():
    return jsonify(load_exports())


@app.route('/api/proxies', methods=['GET', 'POST'])
def api_proxies():
    if request.method == 'POST':
        try:
            row = add_proxy(request.json or {})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(row)
    return jsonify(public_proxies())


@app.route('/api/proxies/<int:proxy_id>', methods=['DELETE'])
def api_proxy_delete(proxy_id):
    delete_proxy(proxy_id)
    return jsonify({"message": "Supprimé"})


@app.route('/api/proxies/<int:proxy_id>/test', methods=['POST'])
def api_proxy_test(proxy_id):
    try:
        return jsonify(test_proxy(proxy_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        return jsonify(save_settings(request.json or {}))
    return jsonify(load_settings())


_schedule_lock = threading.Lock()


def _launch_schedule(job, mark=True):
    global search_in_progress
    with _schedule_lock:
        if search_in_progress:
            return False
        payload = dict(job.get("payload") or {})
        payload["project_id"] = job.get("project_id")
        payload["schedule_id"] = job.get("id")
        mode = job.get("mode")
        remember_job(payload, mode)
        log_activity("INFO", f"Planification lancée : {job.get('name')}")
        if mode == "mairie":
            region = str(payload.get("region") or "")
            departement = str(payload.get("departement") or "")
            all_france = region == "ALL"
            kinds = payload.get("kinds") or ["mairie"]
            target = _search_mairies_worker
            args = (
                region,
                "" if all_france else departement,
                bool(payload.get("scan_sites", True)),
                int(payload.get("max_results") or 0),
                all_france,
                int(payload.get("min_population") or 0),
                kinds,
            )
        elif mode == "universal":
            target = _search_universal_worker
            args = (payload.get("query") or "", int(payload.get("max_results") or 20), bool(payload.get("debug_mode")))
        elif mode == "smart":
            target = _search_smart_worker
            args = (payload.get("query") or "", int(payload.get("max_results") or 15), bool(payload.get("debug_mode")))
        else:
            sources = payload.get("sources") or ["capfun"]
            target = search_multi
            args = (sources, int(payload.get("max_results") or 50), payload.get("keyword") or None, bool(payload.get("debug_mode")))
        search_in_progress = True
        if mark:
            mark_schedule_ran(job["id"])
        threading.Thread(target=_guard_job, args=(target, args)).start()
        return True


def _guard_job(target, args):
    global search_in_progress, search_detail
    try:
        target(*args)
    except Exception as exc:
        search_in_progress = False
        search_detail = f"Erreur : {exc}"
        log_activity("ERROR", str(exc)[:180])
        print(f"Recherche planifiée: {exc}")


def _scheduler_loop():
    while True:
        time.sleep(20)
        try:
            job = due_schedule()
            if job:
                _launch_schedule(job, mark=True)
        except Exception as exc:
            print(f"Planification: {exc}")


def start_scheduler():
    threading.Thread(target=_scheduler_loop, daemon=True, name="orascrap-planner").start()


@app.route('/api/update/status')
def api_update_status():
    from scrapers.updater import get_state
    return jsonify(get_state())


@app.route('/api/update/check', methods=['POST'])
def api_update_check():
    from scrapers.updater import check_for_update
    return jsonify(check_for_update())


@app.route('/api/update', methods=['POST'])
def api_update_apply():
    from scrapers.updater import apply_update
    if search_in_progress:
        return jsonify({"error": "Une recherche est en cours. Arrêtez-la avant la mise à jour."}), 409
    return jsonify(apply_update())


if __name__ == '__main__':
    start_scheduler()
    try:
        from scrapers.updater import startup_autoupdate
        startup_autoupdate(enabled=bool(load_settings().get("auto_update", True)))
    except Exception as exc:
        print(f"Mise à jour automatique : {exc}")
    app.run(debug=True, port=5001, use_reloader=False)
