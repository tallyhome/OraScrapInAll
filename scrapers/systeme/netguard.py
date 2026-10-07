"""Refuse les adresses qui ne sont pas des sites publics."""
import ipaddress
import socket
from urllib.parse import urlparse


def ensure_public_url(url):
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Adresse refusée")
    host = parsed.hostname.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("Adresse locale refusée")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValueError("Nom d'hôte introuvable") from exc
        addresses = [info[4][0] for info in infos]
    if not addresses:
        raise ValueError("Adresse refusée")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global:
            raise ValueError("Adresse privée refusée")
    return url
