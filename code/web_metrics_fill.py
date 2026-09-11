"""Fill missing journal Impact Factor / JCR quartile from browser-like web search.

Fallback order: Bing browser-like search first, then Google with strict
site-restricted queries against journal-metric pages. The code never uses a
whole search page as evidence and never accepts a value without a strong
journal-name/ISSN match. Incorrect values are worse than an explicit missing value.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, List
from urllib.parse import quote_plus, urlparse

import requests

BING_HOSTS = ("https://cn.bing.com/search", "https://www.bing.com/search")
GOOGLE = "https://www.google.com/search"
GOOGLE_METRIC_DOMAINS = {"www.iikx.com", "iikx.com", "www.ablesci.com", "ablesci.com", "journalsimpactfactors.com", "www.journalsimpactfactors.com"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _norm(s: str) -> str:
    s = _clean(s).lower().replace("&", " and ")
    s = re.sub(r"\bthe\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _journal_match(journal: str, item: Dict[str, str], issn: str = "") -> bool:
    target = _norm(journal)
    title = _norm(item.get("title", ""))
    snippet = _norm(item.get("snippet", ""))
    if target and (target in title or target in snippet):
        return True
    raw = _clean(item.get("title", "") + " " + item.get("snippet", ""))
    if issn and issn.replace("-", "") in raw.replace("-", ""):
        return True
    tokens = [t for t in re.findall(r"[a-z0-9]+", _clean(journal).lower()) if t not in {"the"}]
    if len(tokens) >= 3:
        hay = re.findall(r"[a-z0-9]+", raw.lower())
        hits = sum(1 for t in set(tokens) if t in hay)
        return hits / len(set(tokens)) >= 0.75
    return False


def _extract_jif(text: str):
    text = _clean(text)
    patterns = [
        r"(?:20\d{2})(?:\s*年)?\s*(?:最新)?影响因子\s*(?:/[^：:]{0,20})?\s*[:：=\-]?\s*(\d+(?:\.\d+)?)",
        r"(?:最新)?影响因子\s*[:：=\-]\s*(\d+(?:\.\d+)?)",
        r"(?:20\d{2}\s+)?(?:journal\s+)?impact\s+factor\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
        r"(?:journal\s+)?impact\s+factor\s*(?:for\s+20\d{2})?\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
        r"\bJIF\s*(?:20\d{2})?\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            value = float(m.group(1))
            if 0 <= value <= 100:
                return value
    return None


def _extract_q(text: str) -> str:
    text = _clean(text)
    patterns = [
        r"(?:JCR\s*)?(?:分区|区)\s*[:：=\-]?\s*(?:\d+(?:\.\d+)?\s*/\s*)?(Q[1-4])",
        r"(?:JCR|WOS|Web\s+of\s+Science)[^Q]{0,100}(Q[1-4])",
        r"(?:JCR\s+)?quartile\s*[:=\-]?\s*(Q[1-4])",
        r"\b(Q[1-4])\s+(?:quartile|in\s+quartile)",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).upper()
    return ""


def _parse_bing_html(raw: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    blocks = re.findall(r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>.*?</li>', raw, flags=re.I | re.S)
    for block in blocks:
        tm = re.search(r'<h2.*?>\s*<a[^>]*>(.*?)</a>', block, re.I | re.S)
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.I | re.S)
        um = re.search(r'<h2.*?>\s*<a[^>]+href=["\']([^"\']+)', block, re.I | re.S)
        if tm or sm:
            out.append({"title": _clean(re.sub(r"<[^>]+>", " ", tm.group(1))) if tm else "", "snippet": _clean(re.sub(r"<[^>]+>", " ", sm.group(1))) if sm else "", "url": html.unescape(um.group(1)) if um else ""})
    return out


def _parse_bing_rss(raw: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for item in root.findall(".//item"):
        out.append({"title": _clean(item.findtext("title")), "snippet": _clean(item.findtext("description")), "url": _clean(item.findtext("link"))})
    return out


def _bing(query: str) -> List[Dict[str, str]]:
    """Search Bing in a browser-like Chinese locale, then standard Bing."""
    for host in BING_HOSTS:
        for params in (
            {"q": query, "count": 10, "setlang": "zh-CN", "cc": "CN"},
            {"q": query, "count": 10, "setlang": "en-US", "cc": "US"},
            {"q": query, "format": "rss"},
        ):
            try:
                r = requests.get(host, params=params, headers=HEADERS, timeout=10)
                r.raise_for_status()
                raw = r.text
                parsed = _parse_bing_rss(raw) if params.get("format") == "rss" else _parse_bing_html(raw)
                if parsed:
                    return parsed
            except Exception as exc:
                print(f"[bing] failed host={host} query={query!r}: {exc}")
    return []


def _parse_google_html(raw: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    blocks = re.findall(r'<div[^>]+class=["\'][^"\']*(?:MjjYud|Gx5Zad)[^"\']*["\'][^>]*>.*?</div>\s*</div>', raw, flags=re.I | re.S)
    for block in blocks:
        text = _clean(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", block, re.I | re.S))
        links = re.findall(r'<a[^>]+href=["\'](https?://[^"\']+)', block, re.I)
        if text:
            out.append({"title": text[:300], "snippet": text, "url": links[0] if links else ""})
    return out


def _google(query: str) -> List[Dict[str, str]]:
    try:
        r = requests.get(GOOGLE, params={"q": query, "num": 10, "hl": "zh-CN", "gl": "cn"}, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return _parse_google_html(r.text)
    except Exception as exc:
        print(f"[google] failed query={query!r}: {exc}")
        return []


def _score_candidate(journal: str, issn: str, item: Dict[str, str], require_metric_domain: bool = False):
    combined = _clean(item.get("title", "") + " " + item.get("snippet", ""))
    if not _journal_match(journal, item, issn):
        return None
    low = combined.lower()
    if not any(x in low for x in ("impact factor", "jif", "影响因子", "jcr", "分区", "quartile")):
        return None
    jif = _extract_jif(combined)
    q = _extract_q(combined)
    if jif is None and not q:
        return None
    host = urlparse(item.get("url", "")).netloc.lower()
    if require_metric_domain and host not in GOOGLE_METRIC_DOMAINS:
        return None
    title_hit = bool(_norm(journal) and _norm(journal) in _norm(item.get("title", "")))
    issn_hit = bool(issn and issn.replace("-", "") in combined.replace("-", ""))
    score = (8 if title_hit else 0) + (6 if issn_hit else 0) + (3 if jif is not None else 0) + (3 if q else 0)
    if host in {"www.google.com", "cn.bing.com", "www.bing.com"}:
        score -= 2
    return score, item, jif, q


def _pick(engine: str, journal: str, issn: str, queries: List[str], fn, require_metric_domain: bool = False) -> Dict:
    candidates: List[Dict[str, str]] = []
    for q in queries:
        candidates.extend(fn(q))
    best = None
    for item in candidates:
        scored = _score_candidate(journal, issn, item, require_metric_domain=require_metric_domain)
        if scored and (best is None or scored[0] > best[0]):
            best = scored
    if not best:
        return {}
    _, item, jif, quart = best
    return {
        "journal": journal, "issn": issn, "jif": jif,
        "jif_year": 2025 if jif is not None else None,
        "jcr_release": 2026 if quart else None,
        "jcr_quartile": quart, "jcr_category_rank": "",
        "metric_source": f"{engine} web search (third-party evidence; not independently verified as Clarivate JCR)",
        "metric_url": item.get("url", ""),
        "metric_search_query": queries[0],
        "metric_evidence": _clean(item.get("title", "") + " | " + item.get("snippet", "")),
    }


def _search(journal: str, issn: str = "") -> Dict:
    bing_queries = [f'"{journal}"影响因子', f'"{journal}" JCR 分区 影响因子', f'"{journal}" "impact factor" "JCR quartile"']
    if issn:
        bing_queries.insert(0, f'"{journal}" "{issn}" 影响因子')
    found = _pick("Bing", journal, issn, bing_queries, _bing)
    if found:
        return found

    # Bing may be reachable in a browser but blocked for server-side HTML requests.
    # Google is therefore a secondary search engine, restricted to known journal-metric pages.
    google_queries = [
        f'site:iikx.com/sci "{journal}" "2026" "影响因子"',
        f'site:ablesci.com/journal "{journal}" "2026" "影响因子"',
        f'site:journalsimpactfactors.com "{journal}" "Impact Factor" "Q1"',
    ]
    found = _pick("Google", journal, issn, google_queries, _google, require_metric_domain=True)
    return found


def fill_missing(records: Iterable[Dict]) -> List[Dict]:
    records = list(records)
    for r in records:
        m = r.get("journal_metrics") or {}
        complete = m.get("jif") is not None and m.get("jcr_quartile") and m.get("jcr_quartile") != "未检索到"
        if complete:
            continue
        found = _search(r.get("journal", ""), r.get("issn", ""))
        if not found:
            continue
        merged = dict(m)
        for key in ("jif", "jif_year", "jcr_release", "jcr_quartile", "jcr_category_rank", "metric_source", "metric_url", "metric_search_query", "metric_evidence"):
            value = found.get(key)
            if value in (None, ""):
                continue
            if key == "jif" and m.get("jif") is not None:
                continue
            if key == "jcr_quartile" and m.get("jcr_quartile") and m.get("jcr_quartile") != "未检索到":
                continue
            merged[key] = value
        r["journal_metrics"] = merged
    return records
