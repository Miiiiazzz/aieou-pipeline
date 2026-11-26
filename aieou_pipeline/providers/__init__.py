"""Registry of data sources. Add new provider classes here."""
from .base import Provider
from .serpapi import SerpApiProvider

REGISTRY: dict[str, type[Provider]] = {
    "serpapi": SerpApiProvider,
}


def get_provider(name: str, cfg: dict) -> Provider:
    if name not in REGISTRY:
        raise SystemExit(f"Unknown provider '{name}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[name](cfg["providers"].get(name, {}), dry_run=cfg["dry_run"])
