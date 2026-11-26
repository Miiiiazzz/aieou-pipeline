"""The contract every data source implements.

To add a source (DataForSEO, Bright Data, an LLM API, ...):
  1. subclass Provider in a new file in this folder,
  2. implement fetch() and normalize(),
  3. register the class in providers/__init__.py,
  4. add a [surfaces.<name>] section to config.toml that points at it.
Nothing else in the pipeline needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class FetchResult:
    # ok    -> store the payload, job is done
    # retry -> temporary problem, try again after a pause
    # fail  -> this request will never work (bad parameters); do not retry
    # abort -> stop the whole run (bad key, account out of searches)
    outcome: str
    request_params: dict                 # exactly what was sent, minus secrets
    http_status: int | None = None
    payload: dict | None = None          # verbatim provider response
    error: str | None = None
    latency_s: float = 0.0
    provider_search_id: str | None = None
    html_url: str | None = None          # where the raw results page can be fetched
    retry_after_s: float | None = None


@dataclass
class Normalized:
    answer_text: str = ""                # the AI-written answer, if the surface has one
    items: list[dict] = field(default_factory=list)   # see item()
    flags: dict = field(default_factory=dict)


def item(kind: str, position, title=None, link=None, snippet=None, source=None) -> dict:
    domain = ""
    if link:
        try:
            domain = urlparse(link).netloc.lower().removeprefix("www.")
        except ValueError:
            domain = ""
    return {"kind": kind, "position": position, "title": title, "link": link,
            "domain": domain, "snippet": snippet, "source": source}


class Provider:
    name = "base"

    def __init__(self, settings: dict, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run

    def check_ready(self) -> None:
        """Raise SystemExit if a live run cannot start (e.g. no API key)."""

    def fetch(self, surface: dict, prompt: str, timeout_s: float) -> FetchResult:
        raise NotImplementedError

    def fetch_html(self, url: str, timeout_s: float) -> str | None:
        return None

    def normalize(self, surface: dict, payload: dict) -> Normalized:
        raise NotImplementedError

    def redact(self, text: str) -> str:
        """Remove secrets from text before it is written to disk."""
        return text
