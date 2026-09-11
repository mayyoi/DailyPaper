"""Journal metrics for the DR x lipid weekly brief.

Metric lookup order:
1) public JCR-based directory;
2) journal/publisher homepage when it explicitly labels an Impact Factor/JIF;
3) Bing web search as a last-resort discovery layer for Impact Factor + JCR quartile.

Bing-derived values are explicitly marked as web-search evidence and are NOT
presented as independently verified Clarivate JCR data.
"""
from __future__ import annotations

import html as html_lib
import os
import re
from typing import Dict, Iterable, List
from urllib.parse import quote_plus

import requests

BASE = "https://journalsimpactfactors.com"
OPENALEX = "https://api.openalex.org"
BING = "https://www.bing.com/search"
CATEGORY_PAGES = [
    ("ophthalmology-vision-science", "Medicine"),
    ("endocrinology-metabolism", "Medicine"),
    ("molecular-cell-biology", "Life Sciences"),
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36 DailyPaper/1.0",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).lower().replace("&", " and "))


def _cells(row_html: str) -> List[str]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
    out = []
    for cell in cells:
        cell = re.sub(r"<br\s*/?>", " ", cell, flags=re.I)
        cell = re.sub(r"<[^>]+>", " ", cell)
        out.append(_clean(cell))
    return out


def _parse_page(text: str) -> Dict[str, Dict]:
    metrics = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = _cells(row)
        if len(cells) < 5 or cells[0].lower() in {"journal", "rank"}:
            continue
        journal = cells[0]
        issns = re.findall(r"\b\d{4}-\d{3}[\dXx]\b", journal)
        journal_name = re.sub(r"\b\d{4}-\d{3}[\dXx]\b", "", journal).strip(" -")
        if not journal_name:
            continue
        jif = cells[1] if len(cells) > 1 else ""
        quartile = cells[4] if len(cells) > 4 else ""
        if not re.fullmatch(r"(?:\d+(?:\.\d+)?|N/A)", jif or "") or jif == "N/A" or not quartile or quartile == "N/A":
            continue
        record = {
            "journal": journal_name,
            "issn": issns[0] if issns else "",
            "jif": float(jif),
            "jif_year": 2025,
            "jcr_release": 2026,
            "jcr_quartile": quartile,
            "jcr_category_rank": "",
            "metric_source": "Journals Impact Factors (JCR-based public directory)",
            "metric_url": BASE + "/",
        }
        metrics[_norm(journal_name)] = record
        for issn in issns:
            metrics[issn.replace("-", "")] = record
    return metrics


def fetch_jcr_metrics(timeout: int = 30, max_pages: int | None = None) -> Dict[str, Dict]:
    metrics: Dict[str, Dict] = {}
    if max_pages is None:
        max_pages = int(os.getenv("JCR_MAX_PAGES", "20"))
    for slug, subject in CATEGORY_PAGES:
        empty_streak = 0
        for page in range(1, max_pages + 1):
            try:
                response = requests.get(f"{BASE}/subsubject.php", params={"slug": slug, "subject": subject, "page": page}, timeout=timeout, headers=HEADERS)
                response.raise_for_status()
                parsed = _parse_page(response.text)
                if not parsed:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0
                    metrics.update(parsed)
            except Exception as exc:
                print(f"[jcr] failed {slug} page={page}: {exc}")
                break
    print(f"[jcr] loaded {len(metrics)} journal metric keys")
    return metrics


def _openalex_source(journal: str, issn: str = "") -> Dict:
    try:
        params = {"search": journal, "per-page": 10}
        if issn:
            params["filter"] = f"issn:{issn}"
        data = requests.get(f"{OPENALEX}/sources", params=params, timeout=20, headers=HEADERS).json()
        results = data.get("results") or []
        if not results:
            return {}
        target = _norm(journal)
        for item in results:
            if _norm(item.get("display_name", "")) == target:
                return item
        return results[0]
    except Exception:
        return {}


def _homepage_impact_factor(journal: str, issn: str = "") -> Dict:
    src = _openalex_source(journal, issn)
    homepage = _clean(src.get("homepage_url"))
    if not homepage:
        return {}
    try:
        r = requests.get(homepage, timeout=20, headers=HEADERS, allow_redirects=True)
        r.raise_for_status()
        text = _clean(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", r.text, flags=re.I | re.S))
        patterns = [
            r"(?:journal\s+)?impact\s+factor[^0-9]{0,80}(\d+(?:\.\d+)?)",
            r"(?:JIF|JCR)\s*(?:2025|2024|2023)?[^0-9]{0,40}(\d+(?:\.\d+)?)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                value = float(m.group(1))
                if 0 <= value <= 100:
                    return {"jif": value, "jif_year": 2025, "jcr_release": 2026, "jcr_quartile": "", "jcr_category_rank": "", "metric_source": "Journal homepage (explicit Impact Factor label; not independently verified as Clarivate JCR)", "metric_url": homepage, "journal": journal, "issn": issn}
    except Exception as exc:
        print(f"[jif-home] failed {journal}: {exc}")
    return {}


def _bing_results(query: str, timeout: int = 10) -> List[Dict[str, str]]:
    """Browser-like Bing search with Chinese locale first."""
    for host, lang, cc in [("https://cn.bing.com/search", "zh-CN", "CN"), (BING, "en-US", "US")]:
        try:
            r = requests.get(host, params={"q": query, "count": 10, "setlang": lang, "cc": cc}, timeout=timeout, headers=HEADERS)
            r.raise_for_status()
            results = []
            for block in re.findall(r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>.*?</li>', r.text, flags=re.I | re.S):
                title_m = re.search(r'<h2.*?>\s*<a[^>]*>(.*?)</a>', block, flags=re.I | re.S)
                snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.I | re.S)
                url_m = re.search(r'<h2.*?>\s*<a[^>]+href=["\']([^"\']+)', block, flags=re.I | re.S)
                if title_m or snippet_m:
                    results.append({"title": _clean(re.sub(r"<[^>]+>", " ", title_m.group(1))) if title_m else "", "snippet": _clean(re.sub(r"<[^>]+>", " ", snippet_m.group(1))) if snippet_m else "", "url": html_lib.unescape(url_m.group(1)) if url_m else ""})
            if results:
                return results
        except Exception as exc:
            print(f"[bing] failed for {query!r}: {exc}")
    return []


def _extract_jif(text: str) -> float | None:
    patterns = [
        r"(?:20\d{2})\s*年\s*(?:最新)?影响因子\s*[:：=\-]?\s*(\d+(?:\.\d+)?)",
        r"(?:最新)?影响因子\s*[:：=\-]\s*(\d+(?:\.\d+)?)",
        r"(?:20\d{2}\s+)?(?:journal\s+)?impact\s+factor\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
        r"\bJIF\s*(?:20\d{2})?\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, _clean(text), flags=re.I)
        if m:
            value = float(m.group(1))
            if 0 <= value <= 100:
                return value
    return None


def _extract_quartile(text: str) -> str:
    patterns = [
        r"(?:JCR\s*)?(?:分区|区)\s*[:：=\-]?\s*(Q[1-4])",
        r"(?:JCR|WOS|Web\s+of\s+Science)[^Q]{0,100}(Q[1-4])",
        r"(?:JCR\s+)?quartile\s*[:=\-]?\s*(Q[1-4])",
        r"\b(Q[1-4])\s+(?:quartile|in\s+quartile)",
    ]
    for pat in patterns:
        m = re.search(pat, _clean(text), flags=re.I)
        if m:
            return m.group(1).upper()
    return ""


def _bing_journal_metrics(journal: str, issn: str = "") -> Dict:
    queries = [
        f'"{journal}"影响因子',
        f'"{journal}" JCR 分区 影响因子',
        f'"{journal}" "impact factor" "JCR quartile"',
    ]
    if issn:
        queries.insert(0, f'"{journal}" "{issn}" 影响因子')
    candidates = []
    for query in queries:
        candidates.extend(_bing_results(query))
    best = None
    for item in candidates:
        combined = _clean(item.get("title", "") + " " + item.get("snippet", ""))
        low = combined.lower()
        if not any(x in low for x in ("impact factor", "jif", "影响因子")):
            continue
        title_norm = _norm(item.get("title", "")); snippet_norm = _norm(item.get("snippet", "")); target = _norm(journal)
        journal_hit = bool(target and (target in title_norm or target in snippet_norm))
        issn_hit = bool(issn and issn.replace("-", "") in combined.replace("-", ""))
        if not (journal_hit or issn_hit):
            continue
        jif = _extract_jif(combined); quartile = _extract_quartile(combined)
        if jif is None and not quartile:
            continue
        score = (8 if journal_hit else 0) + (6 if issn_hit else 0) + (3 if jif is not None else 0) + (3 if quartile else 0)
        if best is None or score > best[0]:
            best = (score, item, jif, quartile)
    if best is None:
        return {}
    _, item, jif, quartile = best
    return {"journal": journal, "issn": issn, "jif": jif, "jif_year": 2025 if jif is not None else None, "jcr_release": 2026 if quartile else None, "jcr_quartile": quartile, "jcr_category_rank": "", "metric_source": "Bing web search (third-party evidence; not independently verified as Clarivate JCR)", "metric_url": item.get("url", BING + "?q=" + quote_plus(queries[0])), "metric_search_query": queries[0], "metric_evidence": _clean(item.get("title", "") + " | " + item.get("snippet", ""))}


def lookup_journal_metric(journal: str, issn: str, metrics: Dict[str, Dict]) -> Dict:
    for key in [issn.replace("-", "").strip(), _norm(journal)]:
        if key and key in metrics:
            return metrics[key]
    homepage = _homepage_impact_factor(journal, issn)
    if homepage:
        return homepage
    bing = _bing_journal_metrics(journal, issn)
    if bing:
        return bing
    return {"journal": journal, "issn": issn, "jif": None, "jif_year": 2025, "jcr_release": 2026, "jcr_quartile": "未检索到", "jcr_category_rank": "", "metric_source": "未能从公开JCR-based目录、期刊首页或Bing搜索可靠匹配；不填猜测值", "metric_url": BASE + "/"}


def annotate_journal_metrics(records: Iterable[Dict]) -> List[Dict]:
    records = list(records)
    metrics = fetch_jcr_metrics()
    missing = []
    for record in records:
        metric = lookup_journal_metric(record.get("journal", ""), record.get("issn", ""), metrics)
        record["journal_metrics"] = metric
        if metric.get("jif") is None or not metric.get("jcr_quartile"):
            missing.append(record.get("journal", ""))
    if missing:
        missing_names = ", ".join(sorted(set(x for x in missing if x)))
        print(f"[jcr] incomplete journals after Bing fallback: {missing_names}")
    return records
