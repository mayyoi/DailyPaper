"""Fill missing journal Impact Factor / JCR quartile from browser-like web search.

The fallback is deliberately conservative. It first reproduces the Bing web-search
pattern that a human can use (including Chinese queries on cn.bing.com), parses the
actual result title/snippet, and only accepts a value when the same result clearly
mentions the target journal and an Impact Factor/JCR quartile. Google is a secondary
search engine, not a generic text-matching fallback. Incorrect values are worse than
an explicit missing value.
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
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _norm(s: str) -> str:
    """Normalize journal names while treating '&' and 'and' as equivalent."""
    s = _clean(s).lower().replace("&", " and ")
    s = re.sub(r"\bthe\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _journal_match(journal: str, item: Dict[str, str], issn: str = "") -> bool:
    target = _norm(journal)
    title = _norm(item.get("title", ""))
    snippet = _norm(item.get("snippet", ""))
    combined = title + " " + snippet
    if target and (target in title or target in snippet or target in combined):
        return True
    if issn:
        raw = _clean(item.get("title", "") + " " + item.get("snippet", ""))
        if issn.replace("-", "") and issn.replace("-", "") in raw.replace("-", ""):
            return True
    # For subtitles/abbreviations, require at least 75% of meaningful journal tokens.
    tokens = [t for t in re.findall(r"[a-z0-9]+", _clean(journal).lower()) if t not in {"the"}]
    if len(tokens) >= 3:
        hay = re.findall(r"[a-z0-9]+", _clean(item.get("title", "") + " " + item.get("snippet", "")).lower())
        hits = sum(1 for t in set(tokens) if t in hay)
        return hits / len(set(tokens)) >= 0.75
    return False


def _extract_jif(text: str):
    """Extract a likely current Impact Factor, preferring an explicit year label."""
    text = _clean(text)
    patterns = [
        # Chinese web snippets such as: 2026年影响因子：5.5
        r"(?:20\d{2})\s*年\s*(?:最新)?影响因子\s*[:：=\-]?\s*(\d+(?:\.\d+)?)",
        r"(?:最新)?影响因子\s*[:：=\-]\s*(\d+(?:\.\d+)?)",
        # English snippets such as: 2025 Impact Factor: 5.5
        r"(?:20\d{2})\s+(?:journal\s+)?impact\s+factor\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
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
    """Require an explicit JCR/WOS/quartile cue; never treat a bare Q1 as enough."""
    text = _clean(text)
    patterns = [
        r"(?:JCR\s*)?(?:分区|区)\s*[:：=\-]?\s*(Q[1-4])",
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
    blocks = re.findall(
        r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>.*?</li>',
        raw,
        flags=re.I | re.S,
    )
    for block in blocks:
        tm = re.search(r'<h2.*?>\s*<a[^>]*>(.*?)</a>', block, re.I | re.S)
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.I | re.S)
        um = re.search(r'<h2.*?>\s*<a[^>]+href=["\']([^"\']+)', block, re.I | re.S)
        if tm or sm:
            out.append({
                "title": _clean(re.sub(r"<[^>]+>", " ", tm.group(1))) if tm else "",
                "snippet": _clean(re.sub(r"<[^>]+>", " ", sm.group(1))) if sm else "",
                "url": html.unescape(um.group(1)) if um else "",
            })
    return out


def _parse_bing_rss(raw: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for item in root.findall(".//item"):
        out.append({
            "title": _clean(item.findtext("title")),
            "snippet": _clean(item.findtext("description")),
            "url": _clean(item.findtext("link")),
        })
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
                r = requests.get(host, params=params, headers=HEADERS, timeout=20)
                r.raise_for_status()
                raw = r.text
                parsed = _parse_bing_rss(raw) if params.get("format") == "rss" else _parse_bing_html(raw)
                if parsed:
                    return parsed
            except Exception as exc:
                print(f"[bing] failed host={host} query={query!r}: {exc}")
    return []


def _parse_google_html(raw: str) -> List[Dict[str, str]]:
    """Parse only individual Google result cards; never use the whole page as a metric."""
    out: List[Dict[str, str]] = []
    blocks = re.findall(
        r'<div[^>]+class=["\'][^"\']*(?:MjjYud|Gx5Zad)[^"\']*["\'][^>]*>.*?</div>\s*</div>',
        raw,
        flags=re.I | re.S,
    )
    for block in blocks:
        text = _clean(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", block, re.I | re.S))
        links = re.findall(r'<a[^>]+href=["\'](https?://[^"\']+)', block, re.I)
        if text:
            out.append({"title": text[:300], "snippet": text, "url": links[0] if links else ""})
    return out


def _google(query: str) -> List[Dict[str, str]]:
    try:
        r = requests.get(
            GOOGLE,
            params={"q": query, "num": 10, "hl": "zh-CN", "gl": "cn"},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        return _parse_google_html(r.text)
    except Exception as exc:
        print(f"[google] failed query={query!r}: {exc}")
        return []


def _score_candidate(journal: str, issn: str, item: Dict[str, str]):
    combined = _clean(item.get("title", "") + " " + item.get("snippet", ""))
    if not _journal_match(journal, item, issn):
        return None
    low = combined.lower()
    metric_cue = any(x in low for x in ("impact factor", "jif", "影响因子"))
    quartile_cue = any(x in low for x in ("jcr", "quartile", "分区"))
    if not metric_cue and not quartile_cue:
        return None
    jif = _extract_jif(combined)
    q = _extract_q(combined)
    if jif is None and not q:
        return None
    title_hit = _norm(journal) and _norm(journal) in _norm(item.get("title", ""))
    issn_hit = bool(issn and issn.replace("-", "") in combined.replace("-", ""))
    # Strongly prefer the journal title in the result title, then ISSN, then snippet.
    score = (8 if title_hit else 0) + (6 if issn_hit else 0) + (3 if jif is not None else 0) + (3 if q else 0)
    # A generic search page or a non-specific result is not acceptable.
    host = urlparse(item.get("url", "")).netloc.lower()
    if host in {"www.google.com", "cn.bing.com", "www.bing.com"}:
        score -= 2
    return score, item, jif, q


def _search(journal: str, issn: str = "") -> Dict:
    # The first two queries intentionally mirror the user's successful manual Bing search.
    queries = [
        f'"{journal}"影响因子',
        f'"{journal}" JCR 分区 影响因子',
        f'"{journal}" "impact factor" "JCR quartile"',
    ]
    if issn:
        queries.insert(0, f'"{journal}" "{issn}" 影响因子')

    for engine, fn in (("Bing", _bing), ("Google", _google)):
        candidates: List[Dict[str, str]] = []
        for q in queries:
            candidates.extend(fn(q))
        best = None
        for item in candidates:
            scored = _score_candidate(journal, issn, item)
            if scored and (best is None or scored[0] > best[0]):
                best = scored
        if best:
            _, item, jif, quart = best
            return {
                "journal": journal,
                "issn": issn,
                "jif": jif,
                "jif_year": 2025 if jif is not None else None,
                "jcr_release": 2026 if quart else None,
                "jcr_quartile": quart,
                "jcr_category_rank": "",
                "metric_source": f"{engine} web search (third-party evidence; not independently verified as Clarivate JCR)",
                "metric_url": item.get("url") or (BING_HOSTS[0] if engine == "Bing" else GOOGLE),
                "metric_search_query": queries[0],
                "metric_evidence": _clean(item.get("title", "") + " | " + item.get("snippet", "")),
            }
    return {}


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
        # Never overwrite a known structured metric with a weaker web result.
        for key in ("jif", "jif_year", "jcr_release", "jcr_quartile", "jcr_category_rank", "metric_source", "metric_url", "metric_search_query", "metric_evidence"):
            value = found.get(key)
            if value not in (None, ""):
                if key == "jif" and m.get("jif") is not None:
                    continue
                if key == "jcr_quartile" and m.get("jcr_quartile") and m.get("jcr_quartile") != "未检索到":
                    continue
                merged[key] = value
        r["journal_metrics"] = merged
    return records
