"""Fast journal metric fallback: Bing first; Google only if Bing fails."""
from __future__ import annotations
import html,re,xml.etree.ElementTree as ET,time,json
from pathlib import Path
from urllib.parse import quote_plus
import requests
import web_metrics_fill as base
PROXY='https://r.jina.ai/http://www.bing.com/search?format=rss&q='
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36","Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8"}
def clean(x):return re.sub(r"\s+"," ",html.unescape(str(x or ""))).strip()
def rss(raw):
 try:
  root=ET.fromstring(raw);return [{"title":clean(i.findtext('title')),"snippet":clean(i.findtext('description')),"url":clean(i.findtext('link'))} for i in root.findall('.//item')]
 except Exception:return []
def bing(query):
 try:
  r=requests.get(PROXY+quote_plus(query),headers=HEADERS,timeout=12);r.raise_for_status();return rss(r.text) or base._parse_bing_html(r.text)
 except Exception as e:print(f'[bing-proxy] {e}');return []
def search_bing(journal,issn):
 qs=[f'"{journal}" "{issn}" 影响因子' if issn else f'"{journal}"影响因子',f'"{journal}" JCR 分区 影响因子'];best=None
 for q in qs:
  for item in bing(q):
   s=base._score_candidate(journal,issn,item,False)
   if s and (best is None or s[0]>best[0]):best=s
 if not best:return {}
 _,item,jif,q=best
 return {'journal':journal,'issn':issn,'jif':jif,'jif_year':2025 if jif is not None else None,'jcr_release':2026 if q else None,'jcr_quartile':q,'jcr_category_rank':'','metric_source':'Bing web search via text proxy (third-party evidence; not independently verified as Clarivate JCR)','metric_url':item.get('url',''),'metric_search_query':qs[0],'metric_evidence':clean(item.get('title','')+' | '+item.get('snippet',''))}
CACHE=Path('data/journal_metric_cache.json')
_last=0.0
def throttle():
 global _last
 delay=2-(time.monotonic()-_last)
 if delay>0:time.sleep(delay)
 _last=time.monotonic()
def fill_missing(records):
 records=list(records)
 try:cache=json.loads(CACHE.read_text(encoding='utf-8'))
 except (OSError,ValueError):cache={}
 changed=False
 for r in records:
  m=r.get('journal_metrics') or {}
  if m.get('jif') is not None and m.get('jcr_quartile') and m.get('jcr_quartile')!='未检索到':continue
  journal=r.get('journal','');issn=r.get('issn','')
  key=(str(journal).strip().casefold()+'|'+str(issn).strip().casefold())
  entry=cache.get(key,{})
  ttl=(30 if entry.get('value') else 7)*86400
  if entry and time.time()-entry.get('time',0)<ttl:found=entry.get('value',{})
  else:
   throttle();found=search_bing(journal,issn)
   if not found and not getattr(fill_missing,'google_disabled',False):
    try:
     throttle();found=base._search(journal,issn)
    except Exception as e:
     print(f'[google-fallback] {e}')
     if '429' in str(e):fill_missing.google_disabled=True
     found={}
   cache[key]={'time':time.time(),'value':found};changed=True
  if not found:continue
  merged=dict(m)
  for k,v in found.items():
   if k in ('journal','issn') or v in (None,''):continue
   if k=='jif' and m.get('jif') is not None:continue
   if k=='jcr_quartile' and m.get('jcr_quartile') and m.get('jcr_quartile')!='未检索到':continue
   merged[k]=v
  r['journal_metrics']=merged
 if changed:
  CACHE.parent.mkdir(parents=True,exist_ok=True)
  CACHE.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
 return records
