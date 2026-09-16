"""Append successfully emailed paper IDs/titles to persistent weekly history."""
from __future__ import annotations
import argparse,json,re
from datetime import date
from pathlib import Path

def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def doi(x): return re.sub(r"^https?://doi.org/","",clean(x).lower()).rstrip('.')
def norm_title(x): return re.sub(r"[^a-z0-9]+","",clean(x).lower())
def keys(r):
 out=[]
 if clean(r.get('pmid')): out.append('pmid:'+clean(r['pmid']).lower())
 if doi(r.get('doi')): out.append('doi:'+doi(r.get('doi')))
 if norm_title(r.get('title')): out.append('title:'+norm_title(r.get('title')))
 return out
p=argparse.ArgumentParser(); p.add_argument('--report',required=True); p.add_argument('--history',default='data/weekly_history.json'); a=p.parse_args()
report=json.loads(Path(a.report).read_text(encoding='utf-8')); hpath=Path(a.history); hpath.parent.mkdir(parents=True,exist_ok=True)
data=json.loads(hpath.read_text(encoding='utf-8')) if hpath.exists() else {'version':1,'papers':[]}
existing={k for r in data.get('papers',[]) for k in keys(r)}; added=0
for r in report.get('papers',[]):
 if any(k in existing for k in keys(r)): continue
 data.setdefault('papers',[]).append({'pmid':clean(r.get('pmid')),'doi':doi(r.get('doi')),'title':clean(r.get('title')),'first_seen':report.get('date') or date.today().isoformat()}); existing.update(keys(r)); added+=1
hpath.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); print(f'[history] added={added} total={len(data["papers"])} path={hpath}')
