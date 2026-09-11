"""Fill missing journal Impact Factor / JCR quartile using web search fallbacks.

Order: Bing HTML/RSS -> Google web search. Values are explicitly labelled as
third-party web evidence and are never presented as official Clarivate data.
"""
from __future__ import annotations
import html
import re
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, List
from urllib.parse import quote_plus
import requests

BING = "https://www.bing.com/search"
GOOGLE = "https://www.google.com/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _extract_jif(text: str):
    pats = [
        r"(?:journal\s+impact\s+factor|impact\s+factor|JIF)\s*(?:for\s+2025|2025)?\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
        r"(?:2025|2024)\s+impact\s+factor\s*[:=\-]?\s*(\d+(?:\.\d+)?)",
    ]
    for p in pats:
        m = re.search(p, text, re.I)
        if m and 0 <= float(m.group(1)) <= 100:
            return float(m.group(1))
    return None


def _extract_q(text: str) -> str:
    pats = [
        r"(?:JCR\s+)?quartile\s*[:=\-]?\s*(Q[1-4])",
        r"(?:JCR|WOS|Web\s+of\s+Science)[^Q]{0,80}(Q[1-4])",
        r"\b(Q[1-4])\s+(?:quartile|in\s+quartile)",
    ]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).upper()
    return ""


def _bing(query: str) -> List[Dict[str, str]]:
    out=[]
    for params in ({"q":query,"count":10,"setlang":"en-US"},{"q":query,"format":"rss"}):
        try:
            r=requests.get(BING,params=params,headers=HEADERS,timeout=12)
            r.raise_for_status(); raw=r.text
            if params.get("format")=="rss" or raw.lstrip().startswith("<?xml"):
                try:
                    root=ET.fromstring(raw)
                    for item in root.findall(".//item"):
                        out.append({"title":_clean(item.findtext("title")),"snippet":_clean(item.findtext("description")),"url":_clean(item.findtext("link"))})
                except ET.ParseError: pass
            for block in re.findall(r'<li[^>]+class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>.*?</li>',raw,re.I|re.S):
                tm=re.search(r'<h2.*?>\s*<a[^>]*>(.*?)</a>',block,re.I|re.S)
                sm=re.search(r'<p[^>]*>(.*?)</p>',block,re.I|re.S)
                um=re.search(r'<h2.*?>\s*<a[^>]+href=["\']([^"\']+)',block,re.I|re.S)
                if tm or sm:
                    out.append({"title":_clean(re.sub(r'<[^>]+>',' ',tm.group(1))) if tm else "","snippet":_clean(re.sub(r'<[^>]+>',' ',sm.group(1))) if sm else "","url":html.unescape(um.group(1)) if um else ""})
            if out: break
        except Exception: pass
    return out


def _google(query: str) -> List[Dict[str,str]]:
    try:
        r=requests.get(GOOGLE,params={"q":query,"num":10,"hl":"en"},headers=HEADERS,timeout=12); r.raise_for_status()
        raw=r.text; out=[]
        for block in re.findall(r'<div[^>]+class=["\'][^"\']*MjjYud[^"\']*["\'][^>]*>.*?</div>\s*</div>',raw,re.I|re.S):
            text=_clean(re.sub(r'<[^>]+>',' ',block)); um=re.search(r'<a[^>]+href=["\'](https?://[^"\']+)',block,re.I)
            if text: out.append({"title":text[:250],"snippet":text,"url":um.group(1) if um else ""})
        if out: return out
        # Generic fallback: extract visible text around explicit metric terms.
        text=_clean(re.sub(r'<script.*?</script>|<style.*?</style>|<[^>]+>',' ',raw,re.I|re.S))
        return [{"title":"Google result","snippet":text,"url":GOOGLE+"?q="+quote_plus(query)}] if text else []
    except Exception: return []


def _search(journal: str, issn: str = "") -> Dict:
    queries=[f'"{journal}" "impact factor" "JCR quartile"', f'"{journal}" "impact factor" Q1 Q2 Q3 Q4']
    if issn: queries.insert(0,f'"{journal}" "{issn}" "impact factor" "JCR"')
    for engine, fn in (("Bing",_bing),("Google",_google)):
        candidates=[]
        for q in queries:
            candidates.extend(fn(q))
        target=re.sub(r'[^a-z0-9]+','',journal.lower())
        best=None
        for item in candidates:
            combined=_clean(item.get('title','')+' '+item.get('snippet',''))
            norm=re.sub(r'[^a-z0-9]+','',combined.lower())
            if target and target not in norm and not (issn and issn in combined): continue
            jif=_extract_jif(combined); quart=_extract_q(combined)
            if jif is None and not quart: continue
            score=(4 if target and target in norm else 2)+(2 if jif is not None else 0)+(2 if quart else 0)
            if best is None or score>best[0]: best=(score,item,jif,quart)
        if best:
            _,item,jif,quart=best
            return {"journal":journal,"issn":issn,"jif":jif,"jif_year":2025 if jif is not None else None,"jcr_release":2026 if quart else None,"jcr_quartile":quart,"jcr_category_rank":"","metric_source":f"{engine} web search (third-party evidence; not independently verified as Clarivate JCR)","metric_url":item.get('url') or (BING if engine=='Bing' else GOOGLE),"metric_search_query":queries[0]}
    return {}


def fill_missing(records: Iterable[Dict]) -> List[Dict]:
    records=list(records)
    for r in records:
        m=r.get('journal_metrics') or {}
        complete=m.get('jif') is not None and m.get('jcr_quartile') and m.get('jcr_quartile')!='未检索到'
        if complete: continue
        found=_search(r.get('journal',''),r.get('issn',''))
        if not found: continue
        # Merge so a partial homepage metric can be completed by web search.
        merged=dict(m); merged.update({k:v for k,v in found.items() if v not in (None,'')})
        if m.get('jif') is not None and found.get('jif') is None: merged['jif']=m['jif']; merged['jif_year']=m.get('jif_year')
        if m.get('jcr_quartile') and m.get('jcr_quartile')!='未检索到' and not found.get('jcr_quartile'): merged['jcr_quartile']=m['jcr_quartile']
        r['journal_metrics']=merged
    return records
