"""Journal metrics for the DR x lipid weekly brief.

JCR 2026 corresponds to the 2025 metric year. The machine-readable public
JCR-based directory is the first source. To improve coverage, the module also
uses OpenAlex to discover a journal homepage and checks the journal/publisher
page for an explicitly labelled Impact Factor/JIF. Homepage values are only
accepted when the page contains an explicit impact-factor label; otherwise the
paper remains eligible but the report marks the metric as unavailable.
"""
from __future__ import annotations

import html as html_lib
import re
from typing import Dict, Iterable, List

import requests

BASE = "https://journalsimpactfactors.com"
OPENALEX = "https://api.openalex.org"
CATEGORY_PAGES = [
    ("ophthalmology-vision-science", "Medicine"),
    ("endocrinology-metabolism", "Medicine"),
    ("molecular-cell-biology", "Life Sciences"),
]
HEADERS = {"User-Agent": "mayyoi/DailyPaper DR lipid weekly literature radar"}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).lower())


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
        record = {"journal":journal_name,"issn":issns[0] if issns else "","jif":float(jif),"jif_year":2025,"jcr_release":2026,"jcr_quartile":quartile,"jcr_category_rank":"","metric_source":"Journals Impact Factors (JCR-based public directory)","metric_url":BASE+"/"}
        metrics[_norm(journal_name)] = record
        for issn in issns:
            metrics[issn.replace("-","")] = record
    return metrics


def fetch_jcr_metrics(timeout: int = 30, max_pages: int = 20) -> Dict[str, Dict]:
    metrics: Dict[str, Dict] = {}
    for slug, subject in CATEGORY_PAGES:
        empty_streak = 0
        for page in range(1, max_pages + 1):
            try:
                response = requests.get(f"{BASE}/subsubject.php", params={"slug":slug,"subject":subject,"page":page}, timeout=timeout, headers=HEADERS)
                response.raise_for_status()
                parsed = _parse_page(response.text)
                if not parsed:
                    empty_streak += 1
                    if empty_streak >= 2: break
                else:
                    empty_streak = 0; metrics.update(parsed)
            except Exception as exc:
                print(f"[jcr] failed {slug} page={page}: {exc}")
                break
    print(f"[jcr] loaded {len(metrics)} journal metric keys")
    return metrics


def _openalex_source(journal: str, issn: str = "") -> Dict:
    try:
        params = {"search":journal,"per-page":10}
        if issn:
            params["filter"] = f"issn:{issn}"
        data = requests.get(f"{OPENALEX}/sources", params=params, timeout=20, headers=HEADERS).json()
        results = data.get("results") or []
        if not results: return {}
        # Prefer exact normalized display-name match.
        target = _norm(journal)
        for item in results:
            if _norm(item.get("display_name","")) == target:
                return item
        return results[0]
    except Exception:
        return {}


def _homepage_impact_factor(journal: str, issn: str = "") -> Dict:
    """Extract only explicitly labelled Impact Factor/JIF values from a journal page."""
    src = _openalex_source(journal, issn)
    homepage = _clean(src.get("homepage_url"))
    if not homepage:
        return {}
    try:
        r = requests.get(homepage, timeout=20, headers=HEADERS, allow_redirects=True)
        r.raise_for_status()
        text = _clean(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", r.text, flags=re.I|re.S))
        # Require the label and a nearby numeric value. Accept common journal wording,
        # but reject generic metrics such as CiteScore unless Impact Factor is explicit.
        patterns = [
            r"(?:journal\s+)?impact\s+factor[^0-9]{0,80}(\d+(?:\.\d+)?)",
            r"(?:JIF|JCR)\s*(?:2025|2024|2023)?[^0-9]{0,40}(\d+(?:\.\d+)?)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                value = float(m.group(1))
                if 0 <= value <= 100:
                    return {"jif":value,"jif_year":2025,"jcr_release":2026,"jcr_quartile":"","jcr_category_rank":"","metric_source":"Journal homepage (explicit Impact Factor label; not independently verified as Clarivate JCR)","metric_url":homepage,"journal":journal,"issn":issn}
    except Exception as exc:
        print(f"[jif-home] failed {journal}: {exc}")
    return {}


def lookup_journal_metric(journal: str, issn: str, metrics: Dict[str, Dict]) -> Dict:
    for key in [issn.replace("-","").strip(), _norm(journal)]:
        if key and key in metrics: return metrics[key]
    homepage = _homepage_impact_factor(journal, issn)
    if homepage:
        return homepage
    return {"journal":journal,"issn":issn,"jif":None,"jif_year":2025,"jcr_release":2026,"jcr_quartile":"未检索到","jcr_category_rank":"","metric_source":"未能从当前公开JCR-based目录或期刊首页可靠匹配；不填猜测值","metric_url":BASE+"/"}


def annotate_journal_metrics(records: Iterable[Dict]) -> List[Dict]:
    records=list(records); metrics=fetch_jcr_metrics(); missing=[]
    for record in records:
        metric=lookup_journal_metric(record.get("journal",""),record.get("issn",""),metrics)
        record["journal_metrics"]=metric
        if metric.get("jif") is None: missing.append(record.get("journal",""))
    if missing: print(f"[jcr] unmatched journals: {', '.join(sorted(set(x for x in missing if x)))}")
    return records
