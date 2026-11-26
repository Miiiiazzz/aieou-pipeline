"""SerpApi: Google Search, Google AI Mode and Yahoo Search.

Docs: https://serpapi.com/search-api (Google), /google-ai-mode-api, /yahoo-search-api
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import FetchResult, Normalized, Provider, item

ENDPOINT = "https://serpapi.com/search"
QUERY_PARAM = {"yahoo": "p"}          # every other engine takes the query as "q"


class SerpApiProvider(Provider):
    name = "serpapi"

    def __init__(self, settings: dict, dry_run: bool = False):
        super().__init__(settings, dry_run)
        self.key_env = settings.get("api_key_env", "SERPAPI_API_KEY")
        self.api_key = os.environ.get(self.key_env, "")

    def check_ready(self) -> None:
        if not self.dry_run and not self.api_key:
            raise SystemExit(
                f"SerpApi key not found. Set it first, e.g.  export {self.key_env}=your_key"
            )

    def redact(self, text: str) -> str:
        return text.replace(self.api_key, "REDACTED") if self.api_key else text

    # ------------------------------------------------------------- fetch
    def fetch(self, surface: dict, prompt: str, timeout_s: float) -> FetchResult:
        engine = surface["engine"]
        params = {"engine": engine, QUERY_PARAM.get(engine, "q"): prompt}
        for key, value in surface.get("params", {}).items():
            params[key] = str(value).lower() if isinstance(value, bool) else value

        started = time.monotonic()
        if self.dry_run:
            status, body = 200, _mock_payload(engine, prompt)
        else:
            status, body = self._get({**params, "api_key": self.api_key, "output": "json"}, timeout_s)
        result = FetchResult(outcome="retry", request_params=params, http_status=status,
                             latency_s=round(time.monotonic() - started, 3))

        if not isinstance(body, dict):
            result.error = f"HTTP {status}: {str(body)[:300]}"
            if status in (401, 403):
                result.outcome = "abort"
            elif status == 400:
                result.outcome = "fail"
            return result

        message = body.get("error")
        meta = body.get("search_metadata") or {}
        result.provider_search_id = meta.get("id")
        result.html_url = meta.get("raw_html_file")

        if status == 200 and meta.get("status") == "Success":
            # "Success" plus an error message means the engine returned nothing
            # for this query. That is a finding, so it is stored, not retried.
            result.outcome, result.payload, result.error = "ok", body, message
        elif status in (401, 403):
            result.outcome, result.error = "abort", message or f"HTTP {status}"
        elif status == 429:
            result.error = message or "HTTP 429"
            if "run out of searches" in (message or "").lower():
                result.outcome = "abort"
            else:                       # hourly throughput limit: wait it out
                result.retry_after_s = 120
        elif status == 400:
            result.outcome, result.error = "fail", message or "HTTP 400"
        else:
            result.error = message or f"HTTP {status}, status {meta.get('status')}"
        return result

    def _get(self, params: dict, timeout_s: float):
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # timeouts, DNS, connection resets
            return None, f"{type(exc).__name__}: {exc}"
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, text

    def fetch_html(self, url: str, timeout_s: float) -> str | None:
        if self.dry_run:
            return "<html><body>MOCK results page</body></html>"
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception:
            return None

    # --------------------------------------------------------- normalize
    def normalize(self, surface: dict, payload: dict) -> Normalized:
        engine = surface["engine"]
        out = Normalized()
        if payload.get("error"):
            out.flags["provider_note"] = payload["error"]

        if engine == "google_ai_mode":
            out.answer_text = (payload.get("reconstructed_markdown")
                               or _blocks_to_text(payload.get("text_blocks")))
            _add(out, "reference", payload.get("references"))
            _add(out, "shopping", payload.get("shopping_results"))
        else:
            overview = payload.get("ai_overview") or {}
            if overview:
                out.flags["ai_overview_present"] = True
                out.answer_text = _blocks_to_text(overview.get("text_blocks"))
                _add(out, "ai_overview_reference", overview.get("references"))
                if overview.get("page_token") and not out.answer_text:
                    # Google loaded the overview lazily; SerpApi serves it from a
                    # second request (engine=google_ai_overview). Not fetched here.
                    out.flags["ai_overview_needs_second_request"] = True
            _add(out, "organic", payload.get("organic_results"))
            _add(out, "ad", payload.get("ads"))            # Google
            _add(out, "ad", payload.get("ads_results"))    # Yahoo
            _add(out, "shopping", payload.get("shopping_results"))
            _add(out, "shopping", payload.get("inline_shopping_results"))
            _add(out, "related_question", payload.get("related_questions"))
            box = payload.get("answer_box")
            if isinstance(box, dict):
                out.items.append(item("answer_box", 1, box.get("title"), box.get("link"),
                                      box.get("snippet") or box.get("answer"), box.get("source")))
        out.flags["answer_present"] = bool(out.answer_text.strip())
        return out


def _add(out: Normalized, kind: str, entries) -> None:
    if not isinstance(entries, list):
        return
    for n, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        position = entry.get("position") or entry.get("index")
        out.items.append(item(
            kind,
            position if isinstance(position, int) else n,
            entry.get("title") or entry.get("question"),
            entry.get("link") or entry.get("product_link"),
            entry.get("snippet") or entry.get("description"),
            entry.get("source") or entry.get("displayed_link"),
        ))


def _blocks_to_text(blocks, depth: int = 0) -> str:
    """Flatten SerpApi text_blocks (paragraphs, headings, lists, tables)."""
    if not isinstance(blocks, list):
        return ""
    lines = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("snippet"):
            prefix = "  " * depth + ("- " if depth else "")
            lines.append(prefix + str(block["snippet"]))
        if isinstance(block.get("list"), list):
            lines.append(_blocks_to_text(block["list"], depth + 1))
        if isinstance(block.get("text_blocks"), list):
            lines.append(_blocks_to_text(block["text_blocks"], depth))
        if isinstance(block.get("table"), list):
            for row in block["table"]:
                if isinstance(row, list):
                    lines.append(" | ".join(str(cell) for cell in row))
        if block.get("code"):
            lines.append(str(block["code"]))
    return "\n".join(line for line in lines if line)


def _mock_payload(engine: str, prompt: str) -> dict:
    """Fake response in SerpApi's shape, for --dry-run. Contains no real data."""
    meta = {"id": "mock", "status": "Success", "raw_html_file": "mock://page.html"}
    organic = [{"position": i, "title": f"MOCK result {i}", "link": f"https://example{i}.com/page",
                "snippet": "MOCK snippet", "source": f"Example {i}"} for i in (1, 2, 3)]
    if engine == "google_ai_mode":
        return {"search_metadata": meta,
                "text_blocks": [{"type": "paragraph", "snippet": "MOCK answer.", "reference_indexes": [0]},
                                {"type": "list", "list": [{"snippet": "MOCK product A"},
                                                          {"snippet": "MOCK product B"}]}],
                "reconstructed_markdown": "MOCK answer.\n- MOCK product A\n- MOCK product B",
                "references": [{"title": "MOCK source", "link": "https://example.org/a",
                                "snippet": "MOCK", "source": "Example", "index": 0}]}
    if engine == "yahoo":
        return {"search_metadata": meta, "organic_results": organic,
                "ads_results": [{"position": 1, "title": "MOCK ad", "link": "https://ads.example.com",
                                 "snippet": "MOCK ad text"}]}
    return {"search_metadata": meta, "organic_results": organic,
            "ads": [{"position": 1, "title": "MOCK ad", "link": "https://ads.example.com",
                     "description": "MOCK ad text"}],
            "ai_overview": {"text_blocks": [{"type": "paragraph", "snippet": "MOCK overview."}],
                            "references": [{"title": "MOCK ref", "link": "https://example.org/b", "index": 0}]}}
