"""Production weekly DR x lipid literature brief with persistent cross-week deduplication."""
from __future__ import annotations
import argparse, html, json, os, re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import requests
import literature_sources as sources
import weekly_brief_v2 as v2
from daily_pipeline import enrich_direction_tags
from metadata_enrichment import enrich_selected_records
from literature_sources import google_scholar_url, search_all
from relevance_ranker import rank_records
from journal_metrics import annotate_journal_metrics
from translation_service_v2 import zh as robust_zh
from web_metrics_fill_v2 import fill_missing


def _clean(x): return re.sub(r"\s+", " ", str(x or "")).strip()
def _norm_title(title): return re.sub(r"[^a-z0-9]+", "", re.sub(r"https?://\S+", "", _clean(title).lower()))
def _doi(x): return re.sub(r"^https?://doi.org/", "", _clean(x).lower()).rstrip(".")

def _record_keys(r):
    keys=set()
    if _clean(r.get("pmid")): keys.add("pmid:"+_clean(r.get("pmid")).lower())
    if _doi(r.get("doi")): keys.add("doi:"+_doi(r.get("doi")))
    if _norm_title(r.get("title")): keys.add("title:"+_norm_title(r.get("title")))
    return keys

def _load_history(path):
    p=Path(path)
    if not p.exists(): return []
    try:
        data=json.loads(p.read_text(encoding="utf-8")); return data.get("papers",[]) if isinstance(data,dict) else []
    except Exception as exc:
        print(f"[history] cannot read {p}: {exc}"); return []

def _history_keys(history):
    out=set()
    for x in history:
        if isinstance(x,dict): out |= _record_keys(x)
    return out

def _filter_history(records,history):
    seen=_history_keys(history); kept=[]; excluded=[]
    for r in records:
        if _record_keys(r) & seen: excluded.append(r)
        else: kept.append(r)
    return kept,excluded

def _metric_text(record):
    m=record.get("journal_metrics") or {}; jif,q=m.get("jif"),m.get("jcr_quartile"); source=m.get("metric_source") or "JCR-based公开目录"
    if jif is not None and q and q!="未检索到": return f"JCR 2026（2025指标年）：JIF {jif:.3g}；{q}；类别排名 {m.get('jcr_category_rank') or '公开目录未提供'}；来源：{source}"
    if jif is not None: return f"JIF {jif:.3g}；⚠️ 当前未可靠匹配JCR Q；来源：{source}。不将期刊首页JIF冒充完整JCR指标。"
    return "⚠️ 未可靠匹配到当前JCR-based JIF + JCR Q；本周按相关性保留，不填猜测值。"

def _aff(values):
    vals=values if isinstance(values,list) else [values]
    return "；".join(_clean(x) for x in vals if _clean(x)) or "未从公开结构化元数据确认"

def _specific_highlights(records):
    if not records: return ["本周没有检索到此前未出现、且达到当前相关性阈值的精华文献；本次没有用旧文献补数，避免重复。","建议继续保持“宁缺毋滥”的策略：下一周优先等待新的机制研究、临床队列或新技术验证，而不是回填旧文献。"]
    low=[(r.get("title","")+" "+r.get("abstract","")).lower() for r in records]; titles=[r.get("title","") for r in records]; out=[]
    patterns=[
      (("microparticle","sustained-release","long-acting","intravitreal","drug delivery"),"给药/递送技术：","本周出现持续释药、长效玻璃体腔给药或微粒递送等路线，重点价值在于把干预从“短时药理刺激”推进到持续暴露与长期视网膜功能评价。"),
      (("artificial intelligence","deep learning","machine learning","multi-framework","external validation","real-world"),"AI/临床验证：","本周出现跨设备、跨人群或多框架的真实世界AI验证思路，技术重点从单一数据集性能比较转向domain shift、外部验证和评价框架标准化。"),
      (("transcriptomic","multi-cohort","multi-omics","single-cell","rna-seq"),"组学新方向：","出现多队列/转录组/单细胞层面的细胞区室解析，重点不只是找差异基因，而是把内皮、免疫、胶质和纤维化程序放回DR微环境并寻找可验证的脂质代谢节点。"),
      (("tyg","mets-ir","metabolic index","atherogenic index","composite metabolic"),"代谢表型新方向：","出现把TyG、AIP、METS-IR等复合代谢指标与PDR严重程度直接连接的临床研究，值得关注其与血脂、胰岛素抵抗及OCTA表型能否进一步形成可解释的代谢-视网膜轴。"),
      (("exosomal","mir-","ferroptosis","acsl4","nrf2","lipid peroxidation"),"脂质死亡机制：","脂质过氧化/铁死亡方向继续出现外泌体miRNA、ACSL4、NRF2等上游调控组合，研究开始从“铁死亡是否存在”转向细胞间通讯与可干预分子轴。"),
      (("lipid droplet","perilipin","microglia","lipid reprogramming"),"脂滴/免疫代谢：","出现脂滴积累与免疫细胞脂质代谢重编程的研究线索，提示脂滴可能不仅是储脂结构，还可能参与视网膜免疫细胞状态转换。")]
    for keys,label,text in patterns:
        hits=[titles[i] for i,x in enumerate(low) if any(k in x for k in keys)]
        if hits: out.append(label+text+" 代表文献："+"；".join(hits[:2])+"。")
    if not out: out.append("本周方向变化：新入选文献未形成单一技术热点，主要集中在DR脂质代谢与细胞损伤表型的交叉验证；建议优先按证据层级阅读，而不是按期刊名筛选。")
    if len(records)>=2: out.append("导师汇报式总结：本周更值得关注的是“技术/表型向机制闭环靠拢”——把临床代谢指标、组学定位或新型递送技术，与具体脂质节点及视网膜功能/屏障表型连接起来的文章优先级更高。")
    return out[:5]

def _render_md(records,candidates,raw_candidates,days,excluded_count,history_size):
    today=date.today().isoformat(); start=(date.today()-timedelta(days=days-1)).isoformat(); highlights=_specific_highlights(records); summ=v2.direction_summary(records)
    L=[f"# DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}","","## 0. 本周亮点（导师汇报版）",""]+[f"- {x}" for x in highlights]
    L += ["",f"**检索范围:** {start} 至 {today}（{days}天）  ",f"**原始抓取记录:** {raw_candidates}  ",f"**跨源严格去重后候选:** {candidates}  ",f"**因历史重复剔除:** {excluded_count}  ",f"**本周纳入:** {len(records)}（动态数量，不以旧文献补数）  ",f"**历史去重库:** {history_size} 篇  ","**JCR说明:** 当前优先匹配2026 JCR release对应的2025指标年；Bing为首要网页检索渠道，Google仅作二级补充。无法可靠匹配时明确留空，不猜测。", ""]
    L += ["## 1. Research landscape / 研究方向分布","","|方向|篇数|占比|","|---|---:|---:|"]+[f"|{x['direction']}|{x['papers']}|{x['share']}%|" for x in summ]+["","## 2. Weekly papers / 本周重点文献",""]
    for i,r in enumerate(records,1):
        a=r.get("annotation",{})
        L += [f"### {i}. {r.get('title','Untitled')}","",f"**中文题目:** {a.get('title_zh','N/A')}",f"**第一作者:** {r.get('first_author') or 'N/A'}",f"**第一作者单位:** {_aff(r.get('first_author_affiliations'))}",f"**通讯作者:** {r.get('corresponding_author') or '公开结构化元数据未确认'}",f"**通讯作者单位:** {_aff(r.get('corresponding_author_affiliations'))}",f"**期刊（全称）:** {r.get('journal') or 'N/A'}",f"**JCR/影响因子:** {_metric_text(r)}",f"**发表日期:** {r.get('publication_date') or 'N/A'}",f"**文章类型:** {a.get('article_type') or 'N/A'}",f"**证据层级:** {a.get('evidence_level','N/A')}",f"**相关性评分:** {r.get('relevance_score',0)} ({r.get('relevance_tier','')})",f"**脂质方向:** {v2._direction_cn(r.get('direction_tags') or [])}","","**Innovation points / 中文创新点",""]
        L += [f"- {x}" for x in a.get('innovation_points',[])]+["","**Why it matters / 对你的研究的启发",""]+[f"- {x}" for x in a.get('research_focus',[])]+["","**Limitations / 注意事项",""]+[f"- {x}" for x in a.get('limitations_or_cautions',[])]+["","**Abstract / 英文摘要","",r.get('abstract') or 'N/A',"","**中文摘要（自动翻译）**","",a.get('abstract_zh','N/A'),"","**Keywords / 关键词**","",f"English: {', '.join(a.get('keywords_en') or [])}",f"中文: {', '.join(a.get('keywords_zh') or [])}","",f"**PMID:** {r.get('pmid') or 'N/A'}",f"**DOI:** {r.get('doi') or 'N/A'}",f"**PMCID:** {r.get('pmcid') or 'N/A'}",f"**Link:** {r.get('url') or 'N/A'}","","---",""]
    L += ["## 3. Google Scholar supplement / Google Scholar补充检索","","系统不自动抓取Google Scholar；以下链接用于人工交叉核查。",google_scholar_url("diabetic retinopathy lipid metabolism")]
    return "\n".join(L).rstrip()+"\n"

def _render_html(records,candidates,raw_candidates,days,excluded_count,history_size):
    today=date.today().isoformat(); highlights=_specific_highlights(records); summ=v2.direction_summary(records)
    P=["<!doctype html><html lang='zh-CN'><head><meta charset='UTF-8'><meta http-equiv='Content-Type' content='text/html; charset=UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'><title>DR × Lipid Metabolism Weekly Brief</title></head><body>",f"<h1>DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}</h1>","<h2>0. 本周亮点（导师汇报版）</h2><ul>"]+[f"<li>{html.escape(x)}</li>" for x in highlights]+["</ul>",f"<p>Coverage: last {days} days; raw records: {raw_candidates}; strict candidates: {candidates}; historical duplicates excluded: {excluded_count}; included: {len(records)}; history size: {history_size}.</p>","<p><b>JCR/检索说明：</b>Bing为首要网页检索渠道，Google仅作二级补充；无法可靠匹配的JIF/Q不填猜测值。</p>","<h2>1. Research landscape / 研究方向分布</h2><table border='1' cellpadding='6'><tr><th>Direction</th><th>Papers</th><th>Share</th></tr>"]
    for x in summ:P.append(f"<tr><td>{html.escape(x['direction'])}</td><td>{x['papers']}</td><td>{x['share']}%</td></tr>")
    P.append("</table><h2>2. Weekly papers / 本周重点文献</h2>")
    for i,r in enumerate(records,1):
        a=r.get('annotation',{}); url=html.escape(r.get('url') or '#')
        P += [f"<h3>{i}. <a href='{url}'>{html.escape(r.get('title','Untitled'))}</a></h3>",f"<p><b>中文题目:</b> {html.escape(a.get('title_zh','N/A'))}</p>",f"<p><b>第一作者:</b> {html.escape(r.get('first_author') or 'N/A')}<br><b>第一作者单位:</b> {html.escape(_aff(r.get('first_author_affiliations')))}<br><b>通讯作者:</b> {html.escape(r.get('corresponding_author') or '公开结构化元数据未确认')}<br><b>通讯作者单位:</b> {html.escape(_aff(r.get('corresponding_author_affiliations')))}<br><b>期刊（全称）:</b> {html.escape(r.get('journal') or 'N/A')}<br><b>JCR/影响因子:</b> {html.escape(_metric_text(r))}<br><b>日期:</b> {html.escape(r.get('publication_date') or 'N/A')}<br><b>文章类型:</b> {html.escape(a.get('article_type','N/A'))}<br><b>证据层级:</b> {html.escape(a.get('evidence_level','N/A'))}<br><b>评分:</b> {r.get('relevance_score',0)}<br><b>方向:</b> {html.escape(v2._direction_cn(r.get('direction_tags') or []))}</p>","<p><b>Innovation points / 中文创新点</b></p><ul>"+"".join(f"<li>{html.escape(x)}</li>" for x in a.get('innovation_points',[]))+"</ul>","<p><b>Why it matters / 对你的研究的启发</b></p><ul>"+"".join(f"<li>{html.escape(x)}</li>" for x in a.get('research_focus',[]))+"</ul>","<p><b>Limitations / 注意事项</b></p><ul>"+"".join(f"<li>{html.escape(x)}</li>" for x in a.get('limitations_or_cautions',[]))+"</ul>",f"<p><b>Abstract / 英文摘要</b></p><p>{html.escape(r.get('abstract') or 'N/A').replace(chr(10),'<br>')}</p>",f"<p><b>中文摘要（自动翻译）</b></p><p>{html.escape(a.get('abstract_zh','N/A')).replace(chr(10),'<br>')}</p>",f"<p><b>Keywords:</b> {html.escape(', '.join(a.get('keywords_en') or []))}<br><b>关键词:</b> {html.escape(', '.join(a.get('keywords_zh') or []))}</p>",f"<p>PMID: {html.escape(r.get('pmid') or 'N/A')} | DOI: {html.escape(r.get('doi') or 'N/A')} | PMCID: {html.escape(r.get('pmcid') or 'N/A')}<br><a href='{url}'>Open article / 打开文章</a></p><hr>"]
    P += [f"<h2>3. Google Scholar supplement / Google Scholar补充检索</h2><p><a href='{html.escape(google_scholar_url('diabetic retinopathy lipid metabolism'))}'>Google Scholar search</a></p>","</body></html>"]
    return "".join(P)

def run_weekly_brief(days=21,per_query=80,minimum_score=55,hard_max=60,output_dir='Output/weekly',history_path='data/weekly_history.json'):
    original_pubmed=sources.search_pubmed
    def fixed_pubmed(query,retmax=100,days=None,email=None,api_key=None):
        if days is None or days<=0:return original_pubmed(query,retmax=retmax,days=days,email=email,api_key=api_key)
        term=f"({query}) AND (last {days} days[dp])"; params={'db':'pubmed','term':term,'retmode':'json','retmax':retmax,'sort':'pub date'}
        if email:params['email']=email
        if api_key:params['api_key']=api_key
        data=sources._request_json(f"{sources.PUBMED_BASE}/esearch.fcgi",params); ids=data.get('esearchresult',{}).get('idlist',[])
        if not ids:return []
        fetch={'db':'pubmed','id':','.join(ids),'retmode':'xml'}
        if email:fetch['email']=email
        if api_key:fetch['api_key']=api_key
        response=requests.get(f"{sources.PUBMED_BASE}/efetch.fcgi",params=fetch,timeout=60); response.raise_for_status(); return sources._parse_pubmed_xml(response.text,query)
    sources.search_pubmed=fixed_pubmed; v2.zh=robust_zh
    print(f"[retrieve] days={days} per_query={per_query}")
    raw=search_all(per_query=per_query,days=days,email=os.getenv('NCBI_EMAIL','171142515@qq.com')); raw_count=len(raw)
    records=v2.strict_deduplicate(raw); candidates=len(records); history=_load_history(history_path); records,excluded=_filter_history(records,history)
    print(f"[history] stored={len(history)} excluded={len(excluded)} remaining={len(records)}")
    records=enrich_direction_tags(records); ranked=rank_records(records); quality_pool=[r for r in ranked if int(r.get('relevance_score',0))>=40]
    if hard_max>0:quality_pool=quality_pool[:hard_max]
    quality_pool=annotate_journal_metrics(quality_pool); quality_pool=fill_missing(quality_pool)
    strong=[r for r in quality_pool if int(r.get('relevance_score',0))>=minimum_score]; selected=strong if len(strong)>=5 else quality_pool
    if hard_max>0:selected=selected[:hard_max]
    for r in selected:
        m=r.get('journal_metrics') or {}; complete=m.get('jif') is not None and bool(m.get('jcr_quartile')) and m.get('jcr_quartile')!='未检索到'; r['jcr_metric_complete']=bool(complete); r['jcr_metric_note']='JIF + JCR Q已匹配' if complete else '⚠️ 指标不完整：不填猜测值'; r['hard_included_due_to_missing_jcr']=not complete
    print(f"[rank] raw={raw_count} candidates={candidates} history_excluded={len(excluded)} ranked={len(ranked)} quality_pool={len(quality_pool)} strong={len(strong)} selected={len(selected)}")
    enrich_selected_records(selected)
    for i,r in enumerate(selected,1):r['annotation']=v2.annotate(r); print(f"[annotate] {i}/{len(selected)} {r.get('title','')[:90]}")
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); d=date.today().isoformat(); md=_render_md(selected,candidates,raw_count,days,len(excluded),len(history)); h=_render_html(selected,candidates,raw_count,days,len(excluded),len(history))
    payload={'date':d,'days':days,'raw_candidates':raw_count,'candidates':candidates,'history_excluded':len(excluded),'history_size':len(history),'included':len(selected),'dynamic_count':True,'jcr_complete':sum(1 for r in selected if r.get('jcr_metric_complete')),'jcr_missing_hard_included':sum(1 for r in selected if not r.get('jcr_metric_complete')),'papers':selected,'google_scholar_url':google_scholar_url('diabetic retinopathy lipid metabolism')}
    (out/f'{d}.md').write_text(md,encoding='utf-8'); (out/f'{d}.html').write_text(h,encoding='utf-8'); (out/f'{d}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print(f"[done] {out}/{d}.html papers={len(selected)}")

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--days',type=int,default=int(os.getenv('WEEKLY_DAYS','21'))); p.add_argument('--per-query',type=int,default=int(os.getenv('WEEKLY_PER_QUERY','80'))); p.add_argument('--minimum-score',type=int,default=int(os.getenv('WEEKLY_MINIMUM_SCORE','55'))); p.add_argument('--hard-max',type=int,default=int(os.getenv('WEEKLY_HARD_MAX','60'))); p.add_argument('--output-dir',default=os.getenv('WEEKLY_OUTPUT_DIR','Output/weekly')); p.add_argument('--history-path',default=os.getenv('WEEKLY_HISTORY_PATH','data/weekly_history.json')); a=p.parse_args(); run_weekly_brief(a.days,a.per_query,a.minimum_score,a.hard_max,a.output_dir,a.history_path)
