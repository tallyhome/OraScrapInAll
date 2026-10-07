"""Reprise d'une recherche Toute la France interrompue par la fermeture de la fenêtre."""
import json
import os

from scrapers.systeme.updater import project_root


def resume_path():
    return project_root() / "data" / "resume_france.json"


def load_resume():
    path = resume_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_resume(payload):
    path = resume_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def clear_resume():
    path = resume_path()
    try:
        path.unlink()
    except OSError:
        pass
