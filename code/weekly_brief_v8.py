"""Final weekly runner: persistent history, focused retrieval, Bing-first metrics, multi-route translation."""
from __future__ import annotations
import argparse,os,json
from pathlib import Path
import requests
import literature_sources as sources
import weekly_brief_v2 as v2
from literature_sources import search_all,google_scholar_url
from daily_pipeline import enrich_direction_tags
from relevance_ranker import rank_records
from journal_metrics import annotate_journal_metrics
from metadata_enrichment import enrich_selected_records
from translation_service_v4 import zh as robust_zh
from web_metrics_fill_v3 import fill_missing
from weekly_brief_v4 import _load_history,_filter_history,_render_md,_render_html

def run_weekly_brief(days=21,per_query=80,minimum_score=55,hard_max=60,output_dir='Output/weekly',history_path='data/weekly_history.json'):
 original_pubmed=sources.search_pubmed
 def fixed_pubmed(query,retmax=100,days=None,email=None,api_key=None):
  if days is None or days<=0:return original_pubmed(query,retmax=retmax,days=days,email=email,api_key=api_key)
  term=f"({query}) AND (last {days} days[dp])";params={'db':'pubmed','term':term,'retmode':'json','retmax':retmax,'sort':'pub date'}
  if email:params['email']=email
  if api_key:params['api_key']=api_key
  data=sources._request_json(f"{sources.PUBMED_BASE}/esearch.fcgi",params);ids=data.get('esearchresult',{}).get('idlist',[])
  if not ids:return []
  fetch={'db':'pubmed','id':','.join(ids),'retmode':'xml'}
  if email:fetch['email']=email
  if api_key:fetch['api_key']=api_key
  response=requests.get(f"{sources.PUBMED_BASE}/efetch.fcgi",params=fetch,timeout=60);response.raise_for_status();return sources._parse_pubmed_xml(response.text,query)
 sources.search_pubmed=fixed_pubmed;v2.zh=robust_zh
 queries=['(("diabetic retinopathy" OR "diabetic macular edema" OR PDR OR NPDR) AND ("lipid metabolism" OR lipotoxicity OR "lipid droplet" OR perilipin OR PLIN2 OR PLIN5 OR ceramide OR sphingolipid OR triglyceride OR cholesterol OR ferroptosis OR "lipid peroxidation"))','(("diabetic retinopathy" OR retina OR retinal) AND (LXR OR NR1H3 OR NR1H2 OR PPAR OR SREBP OR ACSL4 OR GPX4 OR DGAT1 OR DGAT2 OR "fatty acid" OR "metabolic index" OR TyG OR metabolomics OR transcriptomic))']
 raw=search_all(queries=queries,per_query=min(per_query,60),days=days,email=os.getenv('NCBI_EMAIL','171142515@qq.com'));raw_count=len(raw)
 records=v2.strict_deduplicate(raw);candidates=len(records);history=_load_history(history_path);records,excluded=_filter_history(records,history);records=enrich_direction_tags(records);ranked=rank_records(records)
 metric_pool=[r for r in ranked if int(r.get('relevance_score',0))>=40][:15];metric_pool=annotate_journal_metrics(metric_pool);metric_pool=fill_missing(metric_pool)
 strong=[r for r in metric_pool if int(r.get('relevance_score',0))>=minimum_score];selected=strong if len(strong)>=5 else metric_pool
 if hard_max>0:selected=selected[:hard_max]
 for r in selected:
  m=r.get('journal_metrics') or {};complete=m.get('jif') is not None and bool(m.get('jcr_quartile')) and m.get('jcr_quartile')!='未检索到';r['jcr_metric_complete']=bool(complete);r['jcr_metric_note']='JIF + JCR Q已匹配' if complete else '⚠️ 指标不完整：不填猜测值';r['hard_included_due_to_missing_jcr']=not complete
 enrich_selected_records(selected)
 for r in selected:r['annotation']=v2.annotate(r)
 out=Path(output_dir);out.mkdir(parents=True,exist_ok=True);d=__import__('datetime').date.today().isoformat()
 (out/f'{d}.md').write_text(_render_md(selected,candidates,raw_count,days,len(excluded),len(history)),encoding='utf-8');(out/f'{d}.html').write_text(_render_html(selected,candidates,raw_count,days,len(excluded),len(history)),encoding='utf-8')
 payload={'date':d,'days':days,'raw_candidates':raw_count,'candidates':candidates,'history_excluded':len(excluded),'history_size':len(history),'included':len(selected),'dynamic_count':True,'jcr_complete':sum(1 for r in selected if r.get('jcr_metric_complete')),'jcr_missing_hard_included':sum(1 for r in selected if not r.get('jcr_metric_complete')),'papers':selected,'google_scholar_url':google_scholar_url('diabetic retinopathy lipid metabolism')}
 (out/f'{d}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(f'[done] new_candidates={len(records)} history_excluded={len(excluded)} metric_pool={len(metric_pool)} selected={len(selected)}')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--days',type=int,default=int(os.getenv('WEEKLY_DAYS','21')));p.add_argument('--per-query',type=int,default=int(os.getenv('WEEKLY_PER_QUERY','80')));p.add_argument('--minimum-score',type=int,default=int(os.getenv('WEEKLY_MINIMUM_SCORE','55')));p.add_argument('--hard-max',type=int,default=int(os.getenv('WEEKLY_HARD_MAX','60')));p.add_argument('--output-dir',default=os.getenv('WEEKLY_OUTPUT_DIR','Output/weekly'));p.add_argument('--history-path',default=os.getenv('WEEKLY_HISTORY_PATH','data/weekly_history.json'));a=p.parse_args();run_weekly_brief(a.days,a.per_query,a.minimum_score,a.hard_max,a.output_dir,a.history_path)
