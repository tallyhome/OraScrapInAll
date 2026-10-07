"""Projets, planifications, proxies et réglages, stockés dans data/."""
import json
import os
import re
import shutil
from datetime import datetime, timedelta
from urllib.parse import quote

def _project_root():
    folder = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isfile(os.path.join(folder, "VERSION")) and os.path.isdir(os.path.join(folder, "runtime")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _project_root()
DATA_DIR = os.path.join(ROOT, "data")


def _path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _read(name, fallback):
    path = _path(name)
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def _write(name, payload):
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _now_id():
    return int(datetime.now().timestamp() * 1000)


def log_activity(level, message):
    rows = _read("activity.json", [])
    rows.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "day": datetime.now().strftime("%Y-%m-%d"),
        "level": level,
        "message": str(message)[:240],
    })
    _write("activity.json", rows[-300:])


def load_activity(limit=40):
    rows = _read("activity.json", [])
    return list(reversed(rows[-limit:]))


def default_settings():
    return {
        "display_name": "",
        "chrome_visible": False,
        "proxy_mode": "off",
        "active_proxy_id": None,
        "auto_update": True,
    }


def load_settings():
    saved = _read("settings.json", {})
    settings = default_settings()
    if isinstance(saved, dict):
        settings.update({key: saved.get(key, settings[key]) for key in settings})
    if settings["proxy_mode"] not in ("off", "selected", "rotate"):
        settings["proxy_mode"] = "off"
    settings["auto_update"] = bool(settings.get("auto_update", True))
    return settings


def save_settings(incoming):
    settings = load_settings()
    if "display_name" in incoming:
        settings["display_name"] = str(incoming.get("display_name") or "")[:80]
    if "chrome_visible" in incoming:
        settings["chrome_visible"] = bool(incoming.get("chrome_visible"))
    if incoming.get("proxy_mode") in ("off", "selected", "rotate"):
        settings["proxy_mode"] = incoming["proxy_mode"]
    if "active_proxy_id" in incoming:
        value = incoming.get("active_proxy_id")
        settings["active_proxy_id"] = int(value) if value else None
    if "auto_update" in incoming:
        settings["auto_update"] = bool(incoming.get("auto_update"))
    _write("settings.json", settings)
    return settings


def load_projects():
    rows = _read("projects.json", [])
    return rows if isinstance(rows, list) else []


def save_projects(rows):
    _write("projects.json", rows)


def add_project(name, note=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Le projet a besoin d'un nom")
    rows = load_projects()
    row = {
        "id": _now_id(),
        "name": name[:80],
        "note": (note or "").strip()[:400],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    rows.insert(0, row)
    save_projects(rows)
    return row


def delete_project(project_id):
    save_projects([row for row in load_projects() if row.get("id") != project_id])


def load_proxies():
    rows = _read("proxies.json", [])
    return rows if isinstance(rows, list) else []


def save_proxies(rows):
    _write("proxies.json", rows)


def _clean_proxy(payload):
    host = str(payload.get("host") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", host):
        raise ValueError("Hôte invalide")
    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError):
        raise ValueError("Port invalide")
    if not 1 <= port <= 65535:
        raise ValueError("Port invalide")
    scheme = payload.get("scheme") if payload.get("scheme") in ("http", "https", "socks5") else "http"
    return {
        "id": _now_id(),
        "label": str(payload.get("label") or f"{host}:{port}")[:60],
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": str(payload.get("username") or "")[:80],
        "password": str(payload.get("password") or "")[:80],
        "enabled": bool(payload.get("enabled", True)),
    }


def add_proxy(payload):
    row = _clean_proxy(payload)
    rows = load_proxies()
    rows.insert(0, row)
    save_proxies(rows)
    return {key: value for key, value in row.items() if key != "password"}


def delete_proxy(proxy_id):
    save_proxies([row for row in load_proxies() if row.get("id") != proxy_id])
    settings = load_settings()
    if settings.get("active_proxy_id") == proxy_id:
        settings["active_proxy_id"] = None
        _write("settings.json", settings)


def public_proxies():
    cleaned = []
    for row in load_proxies():
        item = {key: value for key, value in row.items() if key != "password"}
        item["has_password"] = bool(row.get("password"))
        cleaned.append(item)
    return cleaned


def _enabled_proxies():
    return [row for row in load_proxies() if row.get("enabled")]


def choose_proxy():
    settings = load_settings()
    mode = settings.get("proxy_mode") or "off"
    enabled = _enabled_proxies()
    if mode == "off" or not enabled:
        return None
    if mode == "selected":
        chosen = next((row for row in enabled if row.get("id") == settings.get("active_proxy_id")), None)
        return chosen or enabled[0]
    last = _read("proxy_cursor.json", {}).get("id")
    ids = [row["id"] for row in enabled]
    try:
        index = (ids.index(last) + 1) % len(ids)
    except ValueError:
        index = 0
    chosen = enabled[index]
    _write("proxy_cursor.json", {"id": chosen["id"]})
    return chosen


def _proxy_url(proxy):
    auth = ""
    if proxy.get("username"):
        auth = quote(proxy["username"], safe="") + ":" + quote(proxy.get("password") or "", safe="") + "@"
    return f"{proxy['scheme']}://{auth}{proxy['host']}:{proxy['port']}"


def test_proxy(proxy_id):
    proxy = next((row for row in load_proxies() if row.get("id") == proxy_id), None)
    if not proxy:
        raise ValueError("Proxy introuvable")
    import urllib.request
    url = _proxy_url(proxy)
    handler = urllib.request.ProxyHandler({"http": url, "https": url})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open("https://geo.api.gouv.fr/communes?nom=Dijon&limit=1", timeout=8) as response:
            code = getattr(response, "status", 200)
        log_activity("INFO", f"Proxy {proxy['label']} joignable ({code})")
        return {"ok": True, "status": code}
    except Exception as exc:
        message = str(exc)
        if proxy.get("password"):
            message = message.replace(proxy["password"], "••••")
        log_activity("WARN", f"Proxy {proxy['label']} injoignable")
        return {"ok": False, "error": message[:180]}


def chrome_proxy(proxy):
    """Argument Chrome, plus une extension locale si le proxy a un identifiant."""
    if not proxy:
        return None
    server = f"{proxy['scheme']}://{proxy['host']}:{proxy['port']}"
    if not proxy.get("username"):
        return {"server": "--proxy-server=" + server, "extension": ""}
    folder = _path("proxy_ext")
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    user = json.dumps(proxy["username"])
    password = json.dumps(proxy.get("password") or "")
    host = json.dumps(proxy["host"])
    scheme = json.dumps(proxy["scheme"] if proxy["scheme"] != "https" else "http")
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "OraScrap Proxy",
        "permissions": ["proxy", "tabs", "unlimitedStorage", "storage", "<all_urls>", "webRequest", "webRequestBlocking"],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0",
    }
    background = f"""
var config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{ scheme: {scheme}, host: {host}, port: {int(proxy['port'])} }},
    bypassList: ["localhost", "127.0.0.1"]
  }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
chrome.webRequest.onAuthRequired.addListener(
  function() {{ return {{ authCredentials: {{ username: {user}, password: {password} }} }}; }},
  {{urls: ["<all_urls>"]}},
  ["blocking"]
);
"""
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    with open(os.path.join(folder, "background.js"), "w", encoding="utf-8") as handle:
        handle.write(background)
    return {"server": "", "extension": folder}


def load_schedules():
    rows = _read("schedules.json", [])
    return rows if isinstance(rows, list) else []


def save_schedules(rows):
    _write("schedules.json", rows)


def compute_next(job, after=None):
    after = after or datetime.now()
    match = re.fullmatch(r"(\d{2}):(\d{2})", str(job.get("time") or ""))
    if not match:
        raise ValueError("Heure invalide")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Heure invalide")
    frequency = job.get("frequency") or "daily"
    if frequency == "once":
        once = str(job.get("once_at") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", once):
            raise ValueError("Date invalide")
        return once
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if frequency == "weekly":
        weekday = int(job.get("weekday") or 0)
        if not 0 <= weekday <= 6:
            weekday = 0
        delta = (weekday - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=delta)
        if candidate <= after:
            candidate += timedelta(days=7)
        return candidate.strftime("%Y-%m-%d %H:%M")
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M")


def add_schedule(payload):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("La planification a besoin d'un nom")
    mode = payload.get("mode")
    if mode not in ("camping", "universal", "smart", "mairie"):
        raise ValueError("Type de recherche inconnu")
    job = {
        "id": _now_id(),
        "name": name[:80],
        "mode": mode,
        "project_id": int(payload["project_id"]) if payload.get("project_id") else None,
        "frequency": payload.get("frequency") if payload.get("frequency") in ("once", "daily", "weekly") else "daily",
        "time": str(payload.get("time") or "08:00"),
        "weekday": int(payload.get("weekday") or 0),
        "once_at": str(payload.get("once_at") or ""),
        "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
        "enabled": True,
        "last_run": "",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    job["next_run"] = compute_next(job)
    rows = load_schedules()
    rows.insert(0, job)
    save_schedules(rows)
    log_activity("INFO", f"Planification créée : {job['name']} · {job['next_run']}")
    return job


def update_schedule(schedule_id, patch):
    rows = load_schedules()
    found = None
    for row in rows:
        if row.get("id") == schedule_id:
            if "enabled" in patch:
                row["enabled"] = bool(patch["enabled"])
            found = row
            break
    if not found:
        raise ValueError("Planification introuvable")
    save_schedules(rows)
    return found


def delete_schedule(schedule_id):
    save_schedules([row for row in load_schedules() if row.get("id") != schedule_id])


def due_schedule(now=None):
    now = now or datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    for row in load_schedules():
        if row.get("enabled") and row.get("next_run") and row["next_run"] <= stamp:
            return row
    return None


def mark_schedule_ran(schedule_id):
    rows = load_schedules()
    now = datetime.now()
    for row in rows:
        if row.get("id") != schedule_id:
            continue
        row["last_run"] = now.strftime("%Y-%m-%d %H:%M")
        if row.get("frequency") == "once":
            row["enabled"] = False
            row["next_run"] = ""
        else:
            row["next_run"] = compute_next(row, now + timedelta(minutes=1))
        break
    save_schedules(rows)


def load_exports():
    rows = _read("exports.json", [])
    return rows if isinstance(rows, list) else []


def record_export(filename, rows, fmt, history_id=None):
    items = load_exports()
    items.insert(0, {
        "id": _now_id(),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
        "rows": rows,
        "format": fmt,
        "history_id": history_id,
    })
    _write("exports.json", items[:200])
    log_activity("INFO", f"Export {filename} · {rows} lignes")


def next_schedule():
    coming = [row for row in load_schedules() if row.get("enabled") and row.get("next_run")]
    if not coming:
        return None
    coming.sort(key=lambda row: row["next_run"])
    return {"name": coming[0]["name"], "next_run": coming[0]["next_run"], "mode": coming[0]["mode"]}
