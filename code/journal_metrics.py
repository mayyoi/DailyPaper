"""Retrieve current JCR-based journal metrics from a public 2026 directory.

The current JCR release is the 2026 release (2025 metric year). Clarivate's official
Journals API requires a paid license/API key, so this module uses the public
journalsimpactfactors.com JCR-based directory as a machine-readable fallback and
stores the metric year/source explicitly. The report tells the reader to verify
formal evaluations against the institutional Clarivate JCR record.
"""
from __future__ import annotations

import html as html_lib
import re
from typing import Dict, Iterable, List
from urllib.parse import quote_plus

import requests

BASE = "https://journalsimpactfactors.com"
# Relevant subject pages cover most journals likely to appear in a DR x lipid radar.
CATEGORY_PAGES = [
    ("ophthalmology-vision-science", "Medicine"),
    ("endocrinology-metabolism", "Medicine"),
    ("molecular-cell-biology", "Life Sciences"),
]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).lower())


def _cells(row_html: str) -> List[str]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
    out = []
    for cell in cells:
        text = re.sub(r"<br\s*/?>", " ", cell, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        out.append(_clean(text))
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
        # Expected columns: journal, JIF, 5-year JIF, JCI, quartile, publisher.
        jif = cells[1] if len(cells) > 1 else ""
        quartile = cells[4] if len(cells) > 4 else ""
        if not re.fullmatch(r"(?:\d+(?:\.\d+)?|N/A)", jif or ""):
            continue
        if jif == "N/A" or not quartile or quartile == "N/A":
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
            "metric_url": "https://journalsimpactfactors.com/",
        }
        # Store both title and ISSN keys. ISSN is the preferred key when available.
        metrics[_norm(journal_name)] = record
        for issn in issns:
            metrics[issn.replace("-", "")] = record
    return metrics


def fetch_jcr_metrics(timeout: int = 30, max_pages: int = 4) -> Dict[str, Dict]:
    metrics: Dict[str, Dict] = {}
    headers = {"User-Agent": "mayyoi/DailyPaper DR lipid weekly literature radar"}
    for slug, subject in CATEGORY_PAGES:
        for page in range(1, max_pages + 1):
            url = f"{BASE}/subsubject.php"
            params = {"slug": slug, "subject": subject, "page": page}
            try:
                response = requests.get(url, params=params, timeout=timeout, headers=headers)
                response.raise_for_status()
                parsed = _parse_page(response.text)
                if not parsed:
                    break
                metrics.update(parsed)
            except Exception as exc:
                print(f"[jcr] failed {slug} page={page}: {exc}")
                break
    print(f"[jcr] loaded {len(metrics)} journal metric keys")
    return metrics


def lookup_journal_metric(journal: str, issn: str, metrics: Dict[str, Dict]) -> Dict:
    for key in [issn.replace("-", "").strip(), _norm(journal)]:
        if key and key in metrics:
            return metrics[key]
    return {
        "journal": journal,
        "issn": issn,
        "jif": None,
        "jif_year": 2025,
        "jcr_release": 2026,
        "jcr_quartile": "未检索到",
        "jcr_category_rank": "",
        "metric_source": "未能从当前公开JCR-based目录可靠匹配；不填猜测值",
        "metric_url": "https://journalsimpactfactors.com/",
    }


def annotate_journal_metrics(records: Iterable[Dict]) -> List[Dict]:
    records = list(records)
    metrics = fetch_jcr_metrics()
    missing = []
    for record in records:
        metric = lookup_journal_metric(record.get("journal", ""), record.get("issn", ""), metrics)
        record["journal_metrics"] = metric
        if metric.get("jif") is None:
            missing.append(record.get("journal", ""))
    if missing:
        print(f"[jcr] unmatched journals: {', '.join(sorted(set(x for x in missing if x)))}")
    return records
