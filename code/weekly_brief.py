"""Weekly DR x lipid-metabolism literature brief without an AI API key.

Public biomedical APIs are used for retrieval. Chinese title/abstract translation
uses a public translation endpoint with retries; innovation and research-focus notes
are generated from explicit evidence in the title/abstract. No OpenAI, Api2D, or
GitHub Copilot credential is required.
"""
from __future__ import annotations
import argparse, html, json, os, re, time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List
import requests
from daily_pipeline import enrich_direction_tags
import literature_sources as sources
from literature_sources import search_all, google_scholar_url
from relevance_ranker import rank_records

TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
GLOSSARY = {"diabetic retinopathy":"糖尿病视网膜病变","diabetic macular edema":"糖尿病黄斑水肿","lipid metabolism":"脂质代谢","lipid homeostasis":"脂质稳态","lipotoxicity":"脂毒性","fatty acid":"脂肪酸","polyunsaturated fatty acid":"多不饱和脂肪酸","phospholipid":"磷脂","sphingolipid":"鞘脂","ceramide":"神经酰胺","cholesterol":"胆固醇","oxysterol":"氧固醇","triglyceride":"甘油三酯","diacylglycerol":"二酰甘油","lipoprotein":"脂蛋白","lipid droplet":"脂滴","lipid peroxidation":"脂质过氧化","ferroptosis":"铁死亡","endothelial cell":"内皮细胞","müller cell":"Müller细胞","muller cell":"Müller细胞","retinal pigment epithelium":"视网膜色素上皮","retinal ganglion cell":"视网膜神经节细胞","blood-retinal barrier":"血视网膜屏障","retinal neurovascular unit":"视网膜神经血管单元","mitochondrial":"线粒体","inflammation":"炎症","oxidative stress":"氧化应激","diabetes mellitus":"糖尿病","metabolic stress":"代谢应激","vascular permeability":"血管通透性"}

def clean(s: Any) -> str: return re.sub(r"\s+", " ", str(s or "")).strip()

def authors_text(record: Dict) -> str:
    out=[]
    for a in record.get("authors") or []:
        if isinstance(a, dict): out.append(clean(a.get("name") or a.get("full_name") or a.get("author_name")))
        else: out.append(clean(a))
    return ", ".join(x for x in out if x) or "N/A"

def list_text(v: Any) -> str:
    if not v: return ""
    if isinstance(v, (str, dict)): return clean(v)
    return "; ".join(clean(x) for x in v if clean(x))

def translate_text(text: str, target: str="zh-CN", retries: int=3) -> str:
    text=clean(text)
    if not text: return ""
    chunks=[]; cur=""
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if len(cur)+len(sentence)+1>2200 and cur: chunks.append(cur); cur=sentence
        else: cur=(cur+" "+sentence).strip()
    if cur: chunks.append(cur)
    translated=[]
    for chunk in chunks:
        ok=False
        for attempt in range(retries):
            try:
                r=requests.get(TRANSLATE_URL,params={"client":"gtx","sl":"en","tl":target,"dt":"t","q":chunk},timeout=30,headers={"User-Agent":"Mozilla/5.0"})
                r.raise_for_status(); data=r.json(); value="".join(part[0] for part in (data[0] or []) if part and part[0])
                if value: translated.append(value); ok=True; break
            except Exception:
                if attempt<retries-1: time.sleep(1.5*(attempt+1))
        if not ok: translated.append(chunk)
        time.sleep(0.15)
    return " ".join(translated)

def fallback_translation(text: str) -> str:
    x=clean(text)
    for en,zhv in sorted(GLOSSARY.items(),key=lambda kv:-len(kv[0])): x=re.sub(re.escape(en),zhv,x,flags=re.I)
    return x

def zh(text: str) -> str:
    t=translate_text(text)
    return t if t and t!=text else fallback_translation(text)

def sentences(text: str) -> List[str]: return [clean(x) for x in re.split(r"(?<=[.!?])\s+",clean(text)) if len(clean(x))>=45]

def keywords(record: Dict) -> List[str]:
    text=(record.get("title","")+" "+record.get("abstract","")).lower()
    terms=["lipid metabolism","lipid homeostasis","lipotoxicity","fatty acid","PUFA","phospholipid","sphingolipid","ceramide","cholesterol","oxysterol","triglyceride","diacylglycerol","lipoprotein","lipid mediator","lipid peroxidation","ferroptosis","lipid droplet","perilipin","LXR","PPAR","SREBP","FASN","CPT1","ACSL","CD36","mitochondrial","endothelial","Müller","retinal pigment epithelium","retinal ganglion","blood-retinal barrier","inflammation","oxidative stress"]
    found=[]
    for t in terms:
        if t.lower() in text and t.lower() not in [x.lower() for x in found]: found.append(t)
    return found[:8] or ["diabetic retinopathy","lipid metabolism"]

def evidence_level(record: Dict) -> str:
    t=(record.get("title","")+" "+record.get("abstract","")+" "+list_text(record.get("publication_types"))).lower()
    if any(x in t for x in ["meta-analysis","systematic review","review"]): return "review/meta-analysis"
    human=any(x in t for x in ["patient","patients","clinical","cohort","human","serum","plasma","aqueous humor","retrospective","prospective"]); animal=any(x in t for x in ["mouse","mice","rat","rats","db/db","stz","streptozotocin","murine","in vivo"]); cell=any(x in t for x in ["cell","cultured","in vitro","hrmec","müller","muller","rpe1","arpe-19"])
    if sum([human,animal,cell])>=2:return "multi-level"
    if human:return "clinical/human"
    if animal:return "animal"
    if cell:return "cell/in vitro"
    return "unclear"

def innovation_points(record: Dict) -> List[str]:
    sents=sentences(record.get("abstract", "")); cue=re.compile(r"\b(identify|identified|demonstrate|demonstrated|show|showed|revealed|found|associated|predict|predicted|novel|first|role|mechanism|mechanistic|mediates|regulates|improves|worsens|inhibits|promotes)\b",re.I); picks=[s for s in sents if cue.search(s)] or sents[:3]
    return ["摘要支持："+x for x in picks[:3]] or ["摘要信息不足，创新性需要结合全文验证。"]

def research_focus(record: Dict) -> List[str]:
    tags=record.get("direction_tags") or []; t=(record.get("title","")+" "+record.get("abstract","")).lower(); out=[]
    if any("sphingolipid" in x.lower() or "ceramide" in x.lower() for x in tags): out.append("关注神经酰胺/鞘脂是否连接内皮屏障、炎症与细胞死亡，可作为可验证的脂毒性机制轴。")
    if any("fatty acid" in x.lower() or "PUFA" in x for x in tags): out.append("关注脂肪酸谱、脂肪酸氧化与炎症脂质介质之间的耦联，区分底物效应与信号效应。")
    if any("lipid droplet" in x.lower() for x in tags): out.append("关注脂滴形成、脂滴-线粒体接触及脂肪酸动员，而不是只看脂滴数量。")
    if any("cholesterol" in x.lower() for x in tags): out.append("关注胆固醇/氧固醇稳态及LXR-PPAR-SREBP等转录调控是否影响视网膜血管和胶质细胞。")
    if any("ferroptosis" in x.lower() or "peroxidation" in x.lower() for x in tags): out.append("关注脂质过氧化-铁死亡与线粒体应激的因果关系，并考虑用脂质组学或特异性干预验证。")
    if "endothelial" in t or "blood-retinal barrier" in t: out.append("与RNVU/BRB高度相关：可进一步比较内皮细胞与Müller细胞的脂质代谢响应是否存在细胞类型特异性。")
    if "müller" in t or "muller" in t: out.append("Müller胶质值得重点观察脂质处理、乳酸/脂肪酸代谢与Kir4.1/炎症表型之间的联系。")
    if "retinal pigment" in t or "rpe" in t: out.append("RPE方向可关注脂质处理与线粒体功能、氧化应激及视网膜外屏障之间的耦联。")
    if "patient" in t or "cohort" in t or "clinical" in t: out.append("若有临床队列，优先评估指标与DR分级/OCTA表型的关联，并进一步寻找可进入前瞻性验证的指标。")
    return out[:4] or ["可将本文机制映射到DR-RNVU：脂质来源、代谢去路、细胞器处理和炎症/屏障表型四个层面逐一验证。"]

def limitations(record: Dict) -> List[str]:
    out=[]; t=(record.get("title","")+" "+record.get("abstract","")).lower()
    if not record.get("abstract"): out.append("当前来源没有摘要，机制判断必须回到全文。")
    if "association" in t or "associated" in t or "correlation" in t: out.append("摘要包含相关性证据时，不应直接等同于因果关系。")
    if evidence_level(record) in ("cell/in vitro","animal"): out.append("单一模型的外推性有限，需关注与人类DR表型的一致性。")
    return (out or ["创新点和机制判断主要依据摘要，正式立项前建议核对全文实验设计与主要终点。"])[:3]

def annotate(record: Dict) -> Dict:
    abstract=clean(record.get("abstract")) or "No abstract available."; ks=keywords(record)
    return {"title_zh":zh(record.get("title", "")),"abstract_zh":zh(abstract),"keywords_en":ks,"keywords_zh":[fallback_translation(k) for k in ks],"innovation_points":innovation_points(record),"research_focus":research_focus(record),"evidence_level":evidence_level(record),"limitations_or_cautions":limitations(record)}

def direction_summary(records: Iterable[Dict]) -> List[Dict]:
    c=Counter(); records=list(records)
    for r in records:c.update(r.get("direction_tags") or ["Other lipid-related"])
    n=len(records); return [{"direction":k,"papers":v,"share":round(v/n*100,1) if n else 0} for k,v in c.most_common()]

def render_md(records: List[Dict], candidates: int, days: int) -> str:
    today=date.today().isoformat(); start=(date.today()-timedelta(days=days-1)).isoformat(); summ=direction_summary(records); L=[f"# DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}","",f"**检索范围:** {start} 至 {today}（{days}天）  ",f"**去重后候选:** {candidates}  ",f"**纳入文献:** {len(records)}  ","**说明:** 不以脂滴为唯一方向；脂肪酸/PUFA、磷脂、鞘脂/神经酰胺、胆固醇/氧固醇、甘油脂、脂蛋白、脂质介质、脂质过氧化/铁死亡、脂滴、LXR/PPAR/SREBP及线粒体脂质代谢均纳入雷达。",""]
    L += ["## 1. Research landscape / 研究方向分布","","|方向|篇数|占比|","|---|---:|---:|"]+[f"|{x['direction']}|{x['papers']}|{x['share']}%|" for x in summ]+["","## 2. Weekly papers / 本周重点文献",""]
    for i,r in enumerate(records,1):
        a=r.get("annotation",{}); L += [f"### {i}. {r.get('title','Untitled')}","",f"**中文题目:** {a.get('title_zh','N/A')}","",f"**作者:** {authors_text(r)}",f"**期刊:** {r.get('journal') or 'N/A'}  ",f"**发表日期:** {r.get('publication_date') or 'N/A'}  ",f"**文章类型:** {list_text(r.get('publication_types')) or 'N/A'}  ",f"**证据层级:** {a.get('evidence_level','N/A')}  ",f"**相关性评分:** {r.get('relevance_score',0)} ({r.get('relevance_tier','')})  ",f"**脂质方向:** {', '.join(r.get('direction_tags') or [])}","","**Abstract / 英文摘要**","",r.get('abstract') or "N/A","","**中文摘要（自动翻译）**","",a.get('abstract_zh','N/A'),"","**Keywords / 关键词**","",f"English: {', '.join(a.get('keywords_en') or [])}",f"中文: {', '.join(a.get('keywords_zh') or [])}","","**Innovation points / 本文创新点（摘要支持）**",""]+[f"- {x}" for x in a.get('innovation_points',[])]+["","**Why it matters / 对你的研究的启发**",""]+[f"- {x}" for x in a.get('research_focus',[])]+["","**Limitations / 注意事项**",""]+[f"- {x}" for x in a.get('limitations_or_cautions',[])]+["",f"**PMID:** {r.get('pmid') or 'N/A'}  ",f"**DOI:** {r.get('doi') or 'N/A'}  ",f"**Link:** {r.get('url') or 'N/A'}","","---",""]
    L += ["## 3. Google Scholar supplement / Google Scholar补充检索","","系统不自动抓取Google Scholar；以下链接用于人工交叉核查。",google_scholar_url('diabetic retinopathy lipid metabolism'),""]
    return "\n".join(L).rstrip()+"\n"

def render_html(records: List[Dict], candidates: int, days: int) -> str:
    today=date.today().isoformat(); summ=direction_summary(records); P=[f"<html><body><h1>DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}</h1>",f"<p>Coverage: last {days} days; candidates: {candidates}; included: {len(records)}.</p>","<h2>Research landscape / 研究方向分布</h2><table border='1' cellpadding='6'><tr><th>Direction</th><th>Papers</th><th>Share</th></tr>"]
    for x in summ:P.append(f"<tr><td>{html.escape(x['direction'])}</td><td>{x['papers']}</td><td>{x['share']}%</td></tr>")
    P.append("</table><h2>Weekly papers / 本周重点文献</h2>")
    for i,r in enumerate(records,1):
        a=r.get('annotation',{}); url=html.escape(r.get('url') or '#'); P += [f"<h3>{i}. <a href='{url}'>{html.escape(r.get('title','Untitled'))}</a></h3>",f"<p><b>中文题目:</b> {html.escape(a.get('title_zh','N/A'))}</p>",f"<p><b>作者:</b> {html.escape(authors_text(r))}<br><b>期刊:</b> {html.escape(r.get('journal') or 'N/A')}<br><b>日期:</b> {html.escape(r.get('publication_date') or 'N/A')}<br><b>证据层级:</b> {html.escape(a.get('evidence_level','N/A'))}<br><b>评分:</b> {r.get('relevance_score',0)}<br><b>方向:</b> {html.escape(', '.join(r.get('direction_tags') or []))}</p>","<p><b>Abstract / 英文摘要</b></p>",f"<p>{html.escape(r.get('abstract') or 'N/A').replace(chr(10),'<br>')}</p>","<p><b>中文摘要（自动翻译）</b></p>",f"<p>{html.escape(a.get('abstract_zh','N/A')).replace(chr(10),'<br>')}</p>",f"<p><b>Keywords:</b> {html.escape(', '.join(a.get('keywords_en') or []))}<br><b>关键词:</b> {html.escape(', '.join(a.get('keywords_zh') or []))}</p>","<p><b>Innovation points / 本文创新点</b></p>","<ul>"+"".join(f"<li>{html.escape(x)}</li>" for x in a.get('innovation_points',[]))+"</ul>","<p><b>Why it matters / 对你的研究的启发</b></p>","<ul>"+"".join(f"<li>{html.escape(x)}</li>" for x in a.get('research_focus',[]))+"</ul>","<p><b>Limitations / 注意事项</b></p>","<ul>"+"".join(f"<li>{html.escape(x)}</li>" for x in a.get('limitations_or_cautions',[]))+"</ul>",f"<p>PMID: {html.escape(r.get('pmid') or 'N/A')} | DOI: {html.escape(r.get('doi') or 'N/A')}<br><a href='{url}'>Open article / 打开文章</a></p><hr>"]
    P += [f"<h2>Google Scholar supplement / Google Scholar补充检索</h2><p>系统不自动抓取Google Scholar；用于人工交叉核查。</p><p><a href='{html.escape(google_scholar_url('diabetic retinopathy lipid metabolism'))}'>Google Scholar search</a></p>","</body></html>"]
    return "".join(P)

def run_weekly_brief(days=14, per_query=100, top_n=20, minimum_score=40, output_dir='Output/weekly'):
    original_pubmed=sources.search_pubmed
    def fixed_pubmed(query, retmax=100, days=None, email=None, api_key=None):
        if days is None or days<=0: return original_pubmed(query,retmax=retmax,days=days,email=email,api_key=api_key)
        import requests as _requests
        term=f"({query}) AND (last {days} days[dp])"; params={"db":"pubmed","term":term,"retmode":"json","retmax":retmax,"sort":"pub date"}
        if email: params["email"]=email
        if api_key: params["api_key"]=api_key
        data=sources._request_json(f"{sources.PUBMED_BASE}/esearch.fcgi",params); ids=data.get("esearchresult",{}).get("idlist",[])
        if not ids:return []
        fetch={"db":"pubmed","id":",".join(ids),"retmode":"xml"}
        if email: fetch["email"]=email
        if api_key: fetch["api_key"]=api_key
        response=_requests.get(f"{sources.PUBMED_BASE}/efetch.fcgi",params=fetch,timeout=60); response.raise_for_status(); return sources._parse_pubmed_xml(response.text,query)
    sources.search_pubmed=fixed_pubmed
    print(f"[retrieve] days={days} per_query={per_query}")
    records=search_all(per_query=per_query,days=days,email=os.getenv('NCBI_EMAIL','171142515@qq.com')); candidates=len(records)
    for r in records: enrich_direction_tags(r)
    ranked=rank_records(records); eligible=[r for r in ranked if int(r.get('relevance_score',0))>=minimum_score]
    if len(eligible)<top_n: eligible=ranked[:top_n]
    selected=eligible[:top_n]
    if len(selected)<top_n: raise RuntimeError(f"Only {len(selected)} relevant papers found; need {top_n}.")
    print(f"[rank] candidates={candidates} ranked={len(ranked)} selected={len(selected)}")
    for i,r in enumerate(selected,1): print(f"[annotate] {i}/{len(selected)}"); r['annotation']=annotate(r)
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); d=date.today().isoformat(); md=render_md(selected,candidates,days); h=render_html(selected,candidates,days)
    (out/f"{d}.md").write_text(md,encoding='utf-8'); (out/f"{d}.html").write_text(h,encoding='utf-8'); payload={'date':d,'days':days,'candidates':candidates,'included':len(selected),'papers':selected,'google_scholar_url':google_scholar_url('diabetic retinopathy lipid metabolism')}; (out/f"{d}.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print(f"[done] {out}/{d}.html")

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--days',type=int,default=14); p.add_argument('--per-query',type=int,default=100); p.add_argument('--top-n',type=int,default=20); p.add_argument('--minimum-score',type=int,default=40); p.add_argument('--output-dir',default='Output/weekly'); a=p.parse_args(); run_weekly_brief(a.days,a.per_query,a.top_n,a.minimum_score,a.output_dir)
