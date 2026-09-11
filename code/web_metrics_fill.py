"""Reliable journal-metric fallback for the weekly DR×lipid brief.

Bing is tried first with browser-like Chinese queries. If server-side Bing HTML is
blocked, Google is used as a secondary search engine, but only for a strict allowlist
of journal-metric domains. Search-result evidence must contain the journal name/ISSN
and an explicit Impact Factor/JCR quartile cue. No generic whole-page guess is used.
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
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _norm(s: str) -> str:
    s = _clean(s).lower().replace("&", " and ")
    s = re.sub(r"\bthe\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _journal_match(journal: str, item: Dict[str, str], issn: str = "") -> bool:
    target = _norm(journal)
    title = _norm(item.get("title", "")); snippet = _norm(item.get("snippet", ""))
    if target and (target in title or target in snippet): return True
    raw = _clean(item.get("title", "") + " " + item.get("snippet", ""))
    if issn and issn.replace("-", "") in raw.replace("-", ""): return True
    tokens = [t for t in re.findall(r"[a-z0-9]+", _clean(journal).lower()) if t != "the"]
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
    for p in patterns:
        m = re.search(p, text, re.I)
        if m and 0 <= float(m.group(1)) <= 100: return float(m.group(1))
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
        if m: return m.group(1).upper()
    return ""


def _parse_bing_html(raw: str) -> List[Dict[str, str]]:
    out=[]
    for block in re.findall(r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>.*?</li>', raw, re.I|re.S):
        tm=re.search(r'<h2.*?>\s*<a[^>]*>(.*?)</a>',block,re.I|re.S); sm=re.search(r'<p[^>]*>(.*?)</p>',block,re.I|re.S); um=re.search(r'<h2.*?>\s*<a[^>]+href=["\']([^"\']+)',block,re.I|re.S)
        if tm or sm: out.append({"title":_clean(re.sub(r"<[^>]+>"," ",tm.group(1))) if tm else "","snippet":_clean(re.sub(r"<[^>]+>"," ",sm.group(1))) if sm else "","url":html.unescape(um.group(1)) if um else ""})
    return out


def _parse_bing_rss(raw: str) -> List[Dict[str, str]]:
    try: root=ET.fromstring(raw)
    except ET.ParseError: return []
    return [{"title":_clean(i.findtext("title")),"snippet":_clean(i.findtext("description")),"url":_clean(i.findtext("link"))} for i in root.findall(".//item")]


def _bing(query: str) -> List[Dict[str, str]]:
    for host in BING_HOSTS:
        for params in ({"q":query,"count":10,"setlang":"zh-CN","cc":"CN"},{"q":query,"count":10,"setlang":"en-US","cc":"US"},{"q":query,"format":"rss"}):
            try:
                r=requests.get(host,params=params,headers=HEADERS,timeout=10); r.raise_for_status()
                parsed=_parse_bing_rss(r.text) if params.get("format")=="rss" else _parse_bing_html(r.text)
                if parsed: return parsed
            except Exception as exc: print(f"[bing] failed host={host} query={query!r}: {exc}")
    return []


def _strip_html(s: str) -> str:
    return _clean(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", s, re.I|re.S))


def _parse_google_html(raw: str) -> List[Dict[str, str]]:
    """Parse Google result cards; also support obfuscated/new Google markup by using URL-local windows."""
    out=[]
    blocks=re.findall(r'<div[^>]+class=["\'][^"\']*(?:MjjYud|Gx5Zad)[^"\']*["\'][^>]*>.*?</div>\s*</div>',raw,re.I|re.S)
    for block in blocks:
        text=_strip_html(block); links=re.findall(r'https?://[^\s"<>]+',block)
        if text: out.append({"title":text[:400],"snippet":text,"url":links[0] if links else ""})
    # Robust fallback: locate allowed-domain URLs and inspect only nearby text.
    for m in re.finditer(r'https?://(?:www\.)?(?:iikx\.com|ablesci\.com|journalsimpactfactors\.com)/[^\s"<>\\]+', raw, re.I):
        url=html.unescape(m.group(0)).rstrip('.,)')
        host=urlparse(url).netloc.lower()
        if host not in GOOGLE_METRIC_DOMAINS: continue
        window=_strip_html(raw[max(0,m.start()-5000):min(len(raw),m.end()+5000)])
        out.append({"title":window[:500],"snippet":window,"url":url})
    return out


def _google(query: str) -> List[Dict[str, str]]:
    try:
        r=requests.get(GOOGLE,params={"q":query,"num":10,"hl":"zh-CN","gl":"cn","gbv":"1"},headers=HEADERS,timeout=10); r.raise_for_status()
        return _parse_google_html(r.text)
    except Exception as exc:
        print(f"[google] failed query={query!r}: {exc}")
        return []


def _score_candidate(journal: str, issn: str, item: Dict[str, str], require_metric_domain=False):
    combined=_clean(item.get("title","")+" "+item.get("snippet",""))
    if not _journal_match(journal,item,issn): return None
    low=combined.lower()
    if not any(x in low for x in ("impact factor","jif","影响因子","jcr","分区","quartile")): return None
    jif=_extract_jif(combined); q=_extract_q(combined)
    if jif is None and not q: return None
    host=urlparse(item.get("url","")).netloc.lower()
    if require_metric_domain and host not in GOOGLE_METRIC_DOMAINS: return None
    title_hit=bool(_norm(journal) and _norm(journal) in _norm(item.get("title","")))
    issn_hit=bool(issn and issn.replace("-","") in combined.replace("-",""))
    score=(8 if title_hit else 0)+(6 if issn_hit else 0)+(3 if jif is not None else 0)+(3 if q else 0)
    return score,item,jif,q


def _pick(engine: str, journal: str, issn: str, queries: List[str], fn, require_metric_domain=False) -> Dict:
    candidates=[]
    for q in queries: candidates.extend(fn(q))
    best=None
    for item in candidates:
        s=_score_candidate(journal,issn,item,require_metric_domain)
        if s and (best is None or s[0]>best[0]): best=s
    if not best: return {}
    _,item,jif,q=best
    return {"journal":journal,"issn":issn,"jif":jif,"jif_year":2025 if jif is not None else None,"jcr_release":2026 if q else None,"jcr_quartile":q,"jcr_category_rank":"","metric_source":f"{engine} web search (third-party evidence; not independently verified as Clarivate JCR)","metric_url":item.get("url",""),"metric_search_query":queries[0],"metric_evidence":_clean(item.get("title","")+" | "+item.get("snippet",""))}


def _search(journal: str, issn: str = "") -> Dict:
    bing_queries=[f'"{journal}"影响因子',f'"{journal}" JCR 分区 影响因子',f'"{journal}" "impact factor" "JCR quartile"']
    if issn: bing_queries.insert(0,f'"{journal}" "{issn}" 影响因子')
    found=_pick("Bing",journal,issn,bing_queries,_bing)
    if found: return found
    google_queries=[f'site:iikx.com/sci "{journal}" "2026" "影响因子"',f'site:ablesci.com/journal "{journal}" "2026" "影响因子"',f'site:journalsimpactfactors.com "{journal}" "Impact Factor" "Q1"']
    return _pick("Google",journal,issn,google_queries,_google,True)


def fill_missing(records: Iterable[Dict]) -> List[Dict]:
    records=list(records)
    for r in records:
        m=r.get("journal_metrics") or {}
        if m.get("jif") is not None and m.get("jcr_quartile") and m.get("jcr_quartile")!="未检索到": continue
        found=_search(r.get("journal",""),r.get("issn",""))
        if not found: continue
        merged=dict(m)
        for key in ("jif","jif_year","jcr_release","jcr_quartile","jcr_category_rank","metric_source","metric_url","metric_search_query","metric_evidence"):
            value=found.get(key)
            if value in (None,""): continue
            if key=="jif" and m.get("jif") is not None: continue
            if key=="jcr_quartile" and m.get("jcr_quartile") and m.get("jcr_quartile")!="未检索到": continue
            merged[key]=value
        r["journal_metrics"]=merged
    return records
