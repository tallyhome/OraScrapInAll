"""Mise à jour automatique depuis les releases GitHub, sur le modèle de Linkora."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "tallyhome/OraScrapInAll"
USER_AGENT = "OraScrapInAll"
API = f"https://api.github.com/repos/{REPO}/releases/latest"

_state = {
    "checked": False,
    "update_available": False,
    "current": "",
    "latest": "",
    "download_url": "",
    "notes": "",
    "release_url": "",
    "applied": False,
    "needs_restart": False,
    "restarting": False,
    "busy": False,
    "percent": 0,
    "message": "",
    "error": "",
}
_lock = threading.Lock()
_apply_lock = threading.Lock()
_CREATE_NO_WINDOW = 0x08000000


def project_root() -> Path:
    folder = Path(__file__).resolve().parent
    for _ in range(6):
        if (folder / "VERSION").is_file() and (folder / "runtime").is_dir():
            return folder
        if folder.parent == folder:
            break
        folder = folder.parent
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
VERSION_FILE = ROOT / "VERSION"
UPDATES_DIR = ROOT / "data" / "updates"
SKIP_TOP = {"data", "runtime", "archives", ".git", ".venv", "updates", "__pycache__"}


def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(raw: str) -> tuple:
    text = (raw or "0").strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def get_state() -> dict:
    with _lock:
        state = dict(_state)
    state["current"] = state.get("current") or read_version()
    state["repo"] = REPO
    return state


def _set(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def _allowed(url: str) -> bool:
    import urllib.parse

    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if host == "api.github.com":
        return path.startswith(f"/repos/{REPO}/")
    if host in ("github.com", "codeload.github.com"):
        return path.startswith(f"/{REPO}/")
    if host == "objects.githubusercontent.com":
        return True
    return False


def _get_json(url: str):
    if not _allowed(url):
        raise RuntimeError("Adresse de mise à jour refusée.")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub a répondu {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connexion à GitHub impossible. {exc.reason}") from exc
    return payload if isinstance(payload, dict) else None


def fetch_github_release():
    payload = _get_json(API)
    if payload:
        tag = str(payload.get("tag_name") or "").strip().lstrip("vV")
        if tag:
            zip_url = ""
            for asset in payload.get("assets") or []:
                name = str(asset.get("name") or "").lower()
                if name.endswith(".zip"):
                    zip_url = str(asset.get("browser_download_url") or "")
                    break
            if not zip_url:
                zip_url = f"https://github.com/{REPO}/archive/refs/tags/v{tag}.zip"
            if not _allowed(zip_url):
                raise RuntimeError("Adresse de téléchargement refusée.")
            return {
                "version": tag,
                "url": zip_url,
                "notes": str(payload.get("body") or "")[:1500],
                "release_url": str(payload.get("html_url") or ""),
            }
    return _fetch_branch_version()


def _fetch_branch_version():
    import base64
    url = f"https://api.github.com/repos/{REPO}/contents/VERSION?ref=master"
    payload = _get_json(url)
    if not payload:
        return None
    encoded = str(payload.get("content") or "").replace("\n", "")
    try:
        version = base64.b64decode(encoded).decode("utf-8").strip().lstrip("vV")
    except Exception:
        return None
    if not version:
        return None
    zip_url = f"https://github.com/{REPO}/archive/refs/heads/master.zip"
    if not _allowed(zip_url):
        raise RuntimeError("Adresse de téléchargement refusée.")
    return {
        "version": version,
        "url": zip_url,
        "notes": "",
        "release_url": f"https://github.com/{REPO}",
    }


def check_for_update() -> dict:
    current = read_version()
    _set(current=current, checked=True, error="", download_url="")
    try:
        info = fetch_github_release()
    except Exception as exc:
        _set(update_available=False, latest="", message="", error=str(exc)[:240])
        return get_state()
    if not info:
        _set(
            latest="",
            update_available=False,
            message="Aucune release GitHub pour l'instant.",
            error="",
        )
        return get_state()
    latest = info["version"]
    available = is_newer(latest, current)
    _set(
        latest=latest,
        download_url=info["url"],
        notes=info["notes"],
        release_url=info["release_url"],
        update_available=available,
        error="",
        message=(
            f"Mise à jour {latest} disponible."
            if available
            else f"Vous êtes à jour ({current})."
        ),
    )
    return get_state()


def _download(url: str) -> bytes:
    if not _allowed(url):
        raise RuntimeError("Adresse de téléchargement refusée.")
    _set(busy=True, percent=10, message="Téléchargement de la mise à jour…")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Téléchargement impossible. {exc.reason}") from exc
    if raw[:2] != b"PK":
        raise RuntimeError("Le fichier téléchargé n'est pas une archive zip.")
    _set(percent=55, message="Téléchargement terminé.")
    return raw


def _extract(raw: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as archive:
        names = [name for name in archive.namelist() if name and not name.endswith("/")]
        if not names:
            raise RuntimeError("Archive vide.")
        first = names[0]
        prefix = first.split("/", 1)[0] + "/" if "/" in first else ""
        if prefix and not all(name.startswith(prefix) for name in names):
            prefix = ""
        for info in archive.infolist():
            if info.is_dir():
                continue
            rel = info.filename
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            rel = rel.replace("\\", "/").lstrip("/")
            if not rel or rel.endswith("/"):
                continue
            top = rel.split("/", 1)[0]
            if top in SKIP_TOP or ".." in rel.split("/"):
                continue
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest_resolved):
                raise RuntimeError(f"Archive rejetée : {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)


def _schedule_replace(staging: Path) -> None:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    script = UPDATES_DIR / "apply.ps1"
    script.write_text(
        "\n".join([
            "param([int]$TargetPid, [string]$Src, [string]$Dst)",
            "$log = Join-Path $Dst 'data\\updates\\update.log'",
            "New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null",
            "function Log($m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) }",
            "Log ('attente du processus ' + $TargetPid)",
            "for ($i = 0; $i -lt 150; $i++) {",
            "  if (-not (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)) { break }",
            "  Start-Sleep -Milliseconds 400",
            "}",
            "Start-Sleep -Seconds 1",
            "$skip = @('data','runtime','archives','.git','.venv','updates','__pycache__')",
            "Get-ChildItem -LiteralPath $Src | Where-Object { $skip -notcontains $_.Name } | ForEach-Object {",
            "  Log ('copie ' + $_.Name)",
            "  Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dst $_.Name) -Recurse -Force",
            "}",
            "$bat = Join-Path $Dst 'Lancer.bat'",
            "Log 'relance'",
            "Start-Process -FilePath $bat -WorkingDirectory $Dst",
            "",
        ]),
        encoding="utf-8",
    )
    flags = _CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
            "-TargetPid",
            str(os.getpid()),
            "-Src",
            str(staging),
            "-Dst",
            str(ROOT),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )

    def _exit_soon():
        import time
        time.sleep(1.2)
        os._exit(0)

    threading.Thread(target=_exit_soon, daemon=True, name="orascrap-exit").start()


def _run_apply() -> None:
    try:
        _set(busy=True, error="", applied=False, percent=5, message="Vérification de la version…")
        state = check_for_update()
        if not state.get("update_available"):
            _set(busy=False, percent=100, message=state.get("message") or "Déjà à jour.")
            return
        raw = _download(state["download_url"])
        if UPDATES_DIR.exists():
            for old in UPDATES_DIR.glob("staging-*"):
                shutil.rmtree(old, ignore_errors=True)
        staging = UPDATES_DIR / f"staging-{os.getpid()}"
        _set(percent=70, message="Préparation de l'installation…")
        _extract(raw, staging)
        version = str(state.get("latest") or "").strip()
        if version:
            (staging / "VERSION").write_text(version + "\n", encoding="utf-8")
        _set(
            applied=True,
            needs_restart=True,
            restarting=True,
            busy=True,
            percent=100,
            update_available=False,
            current=version,
            message=f"Installation de {version}. L'application redémarre.",
        )
        try:
            from scrapers.systeme.workspace import log_activity
            log_activity("INFO", f"Mise à jour {version} : redémarrage")
        except Exception:
            pass
        _schedule_replace(staging)
    except Exception as exc:
        _set(busy=False, restarting=False, applied=False, error=str(exc)[:300], message="")


def apply_update() -> dict:
    if not _apply_lock.acquire(blocking=False):
        return get_state()

    def worker():
        try:
            _run_apply()
        finally:
            _apply_lock.release()

    _set(busy=True, percent=1, error="", message="Démarrage de la mise à jour…")
    threading.Thread(target=worker, daemon=True, name="orascrap-update").start()
    return get_state()


def startup_autoupdate(enabled: bool = True) -> None:
    if not enabled or os.environ.get("ORASCRAP_PORTABLE") != "1":
        return

    def worker():
        try:
            state = check_for_update()
            if state.get("update_available"):
                apply_update()
        except Exception as exc:
            _set(error=str(exc)[:240])

    threading.Thread(target=worker, daemon=True, name="orascrap-autoupdate").start()
