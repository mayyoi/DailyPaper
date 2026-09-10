"""Production weekly DR x lipid-metabolism literature brief.

Key changes in v2:
- dynamic paper count rather than a fixed 20;
- second-pass duplicate checking across PMID/DOI/title plus near-identical titles;
- first/corresponding author affiliations from PubMed/PMC when publicly available;
- current JCR-based 2025 JIF + quartile from the 2026 public metrics directory;
- explicit article-type classification;
- rule-based Chinese synthesis of innovation points rather than copying abstract sentences;
- a front-of-email "本周看点" synthesis.

No OpenAI, Api2D or Copilot credential is required.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests

import literature_sources as sources
from daily_pipeline import enrich_direction_tags
from journal_metrics import annotate_journal_metrics
from metadata_enrichment import enrich_selected_records
from relevance_ranker import rank_records
from literature_sources import google_scholar_url, search_all

TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
GLOSSARY = {
    "diabetic retinopathy": "糖尿病视网膜病变", "diabetic macular edema": "糖尿病黄斑水肿",
    "lipid metabolism": "脂质代谢", "lipid homeostasis": "脂质稳态", "lipotoxicity": "脂毒性",
    "fatty acid": "脂肪酸", "polyunsaturated fatty acid": "多不饱和脂肪酸", "phospholipid": "磷脂",
    "sphingolipid": "鞘脂", "ceramide": "神经酰胺", "cholesterol": "胆固醇", "oxysterol": "氧固醇",
    "triglyceride": "甘油三酯", "diacylglycerol": "二酰甘油", "lipoprotein": "脂蛋白",
    "lipid droplet": "脂滴", "lipid peroxidation": "脂质过氧化", "ferroptosis": "铁死亡",
    "endothelial cell": "内皮细胞", "müller cell": "Müller细胞", "muller cell": "Müller细胞",
    "retinal pigment epithelium": "视网膜色素上皮", "retinal ganglion cell": "视网膜神经节细胞",
    "blood-retinal barrier": "血视网膜屏障", "retinal neurovascular unit": "视网膜神经血管单元",
    "mitochondrial": "线粒体", "inflammation": "炎症", "oxidative stress": "氧化应激",
    "diabetes mellitus": "糖尿病", "metabolic stress": "代谢应激", "vascular permeability": "血管通透性",
}


def clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def list_text(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, (str, dict)):
        return clean(v)
    return "; ".join(clean(x) for x in v if clean(x))


def authors_text(record: Dict) -> str:
    out = []
    for a in record.get("authors") or []:
        if isinstance(a, dict):
            out.append(clean(a.get("name") or a.get("full_name") or a.get("author_name")))
        else:
            out.append(clean(a))
    return ", ".join(x for x in out if x) or "N/A"


def normalize_title(title: str) -> str:
    x = clean(title).lower()
    x = re.sub(r"https?://\S+", "", x)
    return re.sub(r"[^a-z0-9]+", "", x)


def strict_deduplicate(records: Iterable[Dict]) -> List[Dict]:
    """Second-pass deduplication; never allows duplicate PMID/DOI/title into output."""
    records = list(records)
    out: List[Dict] = []
    seen_pmid, seen_doi, seen_title = set(), set(), set()
    for r in records:
        pmid = clean(r.get("pmid")).lower()
        doi = re.sub(r"^https?://doi.org/", "", clean(r.get("doi")).lower()).rstrip(".")
        title = normalize_title(r.get("title", ""))
        if (pmid and pmid in seen_pmid) or (doi and doi in seen_doi) or (title and title in seen_title):
            continue
        # Catch source/version duplicates whose titles differ only by punctuation or tiny wording.
        duplicate = False
        if title:
            for existing in out[-400:]:
                et = normalize_title(existing.get("title", ""))
                if et and len(title) >= 35 and len(et) >= 35 and difflib.SequenceMatcher(None, title, et).ratio() >= 0.985:
                    duplicate = True
                    break
        if duplicate:
            continue
        if pmid: seen_pmid.add(pmid)
        if doi: seen_doi.add(doi)
        if title: seen_title.add(title)
        out.append(r)
    return out


def translate_text(text: str, target: str = "zh-CN", retries: int = 3) -> str:
    text = clean(text)
    if not text:
        return ""
    chunks, cur = [], ""
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if len(cur) + len(sentence) + 1 > 2200 and cur:
            chunks.append(cur)
            cur = sentence
        else:
            cur = (cur + " " + sentence).strip()
    if cur:
        chunks.append(cur)
    translated = []
    for chunk in chunks:
        ok = False
        for attempt in range(retries):
            try:
                r = requests.get(TRANSLATE_URL, params={"client": "gtx", "sl": "en", "tl": target, "dt": "t", "q": chunk}, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                data = r.json()
                value = "".join(part[0] for part in (data[0] or []) if part and part[0])
                if value:
                    translated.append(value)
                    ok = True
                    break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(1.2 * (attempt + 1))
        if not ok:
            translated.append(chunk)
        time.sleep(0.1)
    return " ".join(translated)


def fallback_translation(text: str) -> str:
    x = clean(text)
    for en, zh in sorted(GLOSSARY.items(), key=lambda kv: -len(kv[0])):
        x = re.sub(re.escape(en), zh, x, flags=re.I)
    return x


def zh(text: str) -> str:
    t = translate_text(text)
    return t if t and t != text else fallback_translation(text)


def keywords(record: Dict) -> List[str]:
    text = (record.get("title", "") + " " + record.get("abstract", "")).lower()
    terms = ["lipid metabolism", "lipid homeostasis", "lipotoxicity", "fatty acid", "PUFA", "phospholipid", "sphingolipid", "ceramide", "cholesterol", "oxysterol", "triglyceride", "diacylglycerol", "lipoprotein", "lipid mediator", "lipid peroxidation", "ferroptosis", "lipid droplet", "perilipin", "LXR", "PPAR", "SREBP", "FASN", "CPT1", "ACSL", "CD36", "mitochondrial", "endothelial", "Müller", "retinal pigment epithelium", "retinal ganglion", "blood-retinal barrier", "inflammation", "oxidative stress"]
    found = []
    for term in terms:
        if term.lower() in text and term.lower() not in [x.lower() for x in found]:
            found.append(term)
    return found[:8] or ["diabetic retinopathy", "lipid metabolism"]


def article_type(record: Dict) -> str:
    t = (record.get("title", "") + " " + record.get("abstract", "") + " " + list_text(record.get("publication_types"))).lower()
    pts = " ".join(str(x).lower() for x in (record.get("publication_types") or []))
    if "meta-analysis" in pts or "network meta-analysis" in pts:
        return "Meta分析"
    if "systematic review" in pts or "scoping review" in pts:
        return "系统综述"
    if "review" in pts or "review" in t and "review" not in pts:
        return "综述"
    if "mendelian randomization" in t or re.search(r"\bMR analysis\b|\bMendelian randomi", t):
        return "孟德尔随机化/遗传流行病学分析"
    if any(x in t for x in ["single-cell", "single cell", "rna-seq", "transcriptom", "bioinformatic", "machine learning", "deep learning", "proteomic", "metabolomic", "multi-omics"]):
        return "生信/组学分析"
    if any(x in pts for x in ["randomized controlled trial", "clinical trial", "controlled clinical trial", "pragmatic clinical trial"]):
        return "临床试验"
    if any(x in pts for x in ["observational study", "cohort", "case-control", "multicenter study"]):
        return "临床观察性研究"
    if any(x in t for x in ["patient", "patients", "cohort", "retrospective", "prospective", "clinical study", "human plasma", "serum", "aqueous humor"]):
        return "临床/人群研究"
    if any(x in t for x in ["mouse", "mice", "rat", "rats", "db/db", "streptozotocin", "stz", "murine", "animal model", "in vivo"]):
        return "基础实验（动物）"
    if any(x in t for x in ["cell", "cultured", "in vitro", "hrmec", "müller", "muller", "rpe", "organoid"]):
        return "基础实验（细胞/体外）"
    return "原始研究（类型未完全标注）"


def evidence_level(record: Dict) -> str:
    kind = article_type(record)
    if "Meta" in kind or "综述" in kind:
        return "二级证据"
    if "临床" in kind or "孟德尔" in kind:
        return "人群/临床证据"
    if "动物" in kind:
        return "动物证据"
    if "细胞" in kind:
        return "细胞证据"
    if "组学" in kind:
        return "计算/组学证据"
    return "原始研究"


def _direction_cn(tags: List[str]) -> str:
    mapping = {
        "Fatty acid / PUFA": "脂肪酸/PUFA", "Phospholipid": "磷脂", "Sphingolipid / ceramide": "鞘脂/神经酰胺",
        "Cholesterol / oxysterol": "胆固醇/氧固醇", "Glycerolipid / TG / DAG": "甘油脂/TG/DAG",
        "Lipid mediator": "脂质介质", "Lipoprotein": "脂蛋白", "Lipid peroxidation / ferroptosis": "脂质过氧化/铁死亡",
        "Lipid droplet": "脂滴", "Lipid transcriptional regulation": "脂质转录调控", "Mitochondrial lipid metabolism": "线粒体脂质代谢",
    }
    return ", ".join(mapping.get(x, x) for x in tags)


def innovation_points(record: Dict) -> List[str]:
    """Generate synthesis bullets from study design + causal/intervention cues; do not copy abstract sentences."""
    t = clean(record.get("title", "")) + " " + clean(record.get("abstract", ""))
    low = t.lower()
    tags = record.get("direction_tags") or []
    direction = _direction_cn(tags) or "脂质代谢"
    genes = re.findall(r"\b(?:PLIN[1-5]|LXR\w*|NR1H[23]|PPAR\w*|SREBP\w*|FASN|ACSL\w*|CPT1\w*|CD36|DGAT[12]|PNPLA2|CIDEC|GPX4|ACSL4|SCD1|HMOX1|NLRP3|VEGF|HIF-1α|HIF-1A)\b", t, flags=re.I)
    genes = list(dict.fromkeys(genes))[:4]
    interventions = [x for x in ["knockdown", "knockout", "overexpression", "silencing", "inhibitor", "agonist", "antagonist", "depletion", "treatment", "gene deletion"] if x in low]
    outcomes = [x for x in ["vascular leakage", "permeability", "barrier", "neovascularization", "inflammation", "oxidative stress", "ferroptosis", "apoptosis", "retinal function", "visual function", "disease severity"] if x in low]
    human = any(x in low for x in ["patient", "patients", "cohort", "human", "serum", "plasma", "aqueous humor", "clinical"])
    bullets = [f"研究问题创新：将{direction}从单纯代谢异常提升到糖尿病视网膜病变病理链条中的候选环节，重点观察其与{', '.join(outcomes[:2]) or '视网膜损伤表型'}的连接。"]
    if interventions:
        target = "、".join(genes) if genes else "候选代谢节点"
        bullets.append(f"机制证据创新：研究包含“{interventions[0]}”等干预线索，并把{target}与下游表型联系起来，因此比单纯相关性研究更接近“脂质代谢改变→细胞反应→DR表型”的因果链。")
    else:
        bullets.append(f"机制定位：摘要提示{direction}与疾病表型存在明确联系，但缺少可识别的直接干预证据，因此其主要价值在于提出机制假说，而不是完成因果闭环。")
    if human:
        bullets.append("转化创新：存在人群/临床材料或临床表型，使脂质代谢信号有机会与DR分级、影像或生物标志物建立对应关系，后续可进一步做纵向验证。")
    elif any(x in low for x in ["single-cell", "single cell", "transcriptom", "rna-seq", "proteom", "metabolom"]):
        bullets.append("技术创新：利用组学/单细胞层面的分辨率定位脂质代谢异常的细胞来源或通路网络，为后续细胞特异性干预提供靶点筛选依据。")
    else:
        bullets.append("研究价值：若干预与表型方向一致，可进一步在内皮细胞、Müller细胞、RPE或RGC之间比较细胞类型特异性，从而把单一模型结果放回RNVU整体框架。")
    return bullets[:3]


def research_focus(record: Dict) -> List[str]:
    tags = record.get("direction_tags") or []
    t = (record.get("title", "") + " " + record.get("abstract", "")).lower()
    out = []
    if any("ceramide" in x.lower() or "sphingolipid" in x.lower() for x in tags):
        out.append("重点看神经酰胺/鞘脂是否同时连接内皮屏障、炎症和细胞死亡，适合进一步验证脂毒性机制轴。")
    if any("fatty acid" in x.lower() or "puFA" in x.lower() for x in tags):
        out.append("重点看脂肪酸底物、脂肪酸氧化与炎症脂质介质之间的耦联，区分底物堆积和信号效应。")
    if any("lipid droplet" in x.lower() for x in tags):
        out.append("脂滴方向不宜只测数量，应进一步看脂滴-线粒体接触、脂肪酸动员以及PLIN家族差异。")
    if any("cholesterol" in x.lower() for x in tags):
        out.append("胆固醇/氧固醇方向可与LXR-PPAR-SREBP调控轴衔接，并比较血管与胶质细胞的反应。")
    if any("ferroptosis" in x.lower() or "peroxidation" in x.lower() for x in tags):
        out.append("脂质过氧化-铁死亡方向应尽量加入脂质组学、GPX4/ACSL4等证据和特异性救援实验。")
    if "endothelial" in t or "blood-retinal barrier" in t:
        out.append("与RNVU/BRB高度相关，可进一步比较内皮与Müller细胞的脂质代谢响应是否具有细胞类型特异性。")
    if "müller" in t or "muller" in t:
        out.append("Müller细胞值得重点观察脂质处理、线粒体功能与炎症/离子稳态表型之间的联系。")
    if "retinal pigment" in t or re.search(r"\brpe\b", t):
        out.append("RPE方向可关注脂质处理与线粒体、氧化应激及视网膜外屏障之间的耦联。")
    if "patient" in t or "cohort" in t or "clinical" in t:
        out.append("若有临床队列，优先把脂质指标与DR分级/OCTA表型做关联，再寻找可进入前瞻性验证的指标。")
    return out[:4] or ["可将本文映射到DR-RNVU：脂质来源、代谢去路、细胞器处理、炎症/屏障表型四层逐一验证。"]


def limitations(record: Dict) -> List[str]:
    t = (record.get("title", "") + " " + record.get("abstract", "")).lower()
    out = []
    if not record.get("abstract"):
        out.append("当前来源没有摘要，机制判断必须回到全文。")
    if any(x in t for x in ["association", "associated", "correlation"]):
        out.append("摘要包含相关性证据时，不能直接等同于因果关系。")
    if article_type(record) in {"基础实验（细胞/体外）", "基础实验（动物）"}:
        out.append("单一模型外推性有限，需要与人类DR表型或其他模型交叉验证。")
    return (out or ["创新点主要基于公开摘要和结构化元数据，正式引用前建议核对全文实验设计与主要终点。"])[:3]


def annotate(record: Dict) -> Dict:
    abstract = clean(record.get("abstract")) or "No abstract available."
    ks = keywords(record)
    return {
        "title_zh": zh(record.get("title", "")),
        "abstract_zh": zh(abstract),
        "keywords_en": ks,
        "keywords_zh": [fallback_translation(k) for k in ks],
        "innovation_points": innovation_points(record),
        "research_focus": research_focus(record),
        "evidence_level": evidence_level(record),
        "article_type": article_type(record),
        "limitations_or_cautions": limitations(record),
    }


def direction_summary(records: Iterable[Dict]) -> List[Dict]:
    c = Counter()
    records = list(records)
    for r in records:
        c.update(r.get("direction_tags") or ["Other lipid-related"])
    n = len(records)
    return [{"direction": k, "papers": v, "share": round(v / n * 100, 1) if n else 0} for k, v in c.most_common()]


def weekly_highlights(records: List[Dict]) -> List[str]:
    if not records:
        return ["本周没有达到纳入阈值的高质量文献；建议下周扩大检索窗口或人工补充Google Scholar。"]
    top = records[:5]
    dirs = Counter(tag for r in records for tag in (r.get("direction_tags") or []))
    strongest = dirs.most_common(3)
    highlights = []
    if strongest:
        d = "、".join(_direction_cn([x[0]]) for x in strongest)
        highlights.append(f"本周主线：{d}最集中，提示DR脂质研究正在从“总脂质变化”向具体脂质类别和代谢节点下沉。")
    causal = sum(1 for r in records if any(x in (r.get("abstract", "").lower()) for x in ["knockdown", "knockout", "overexpression", "inhibitor", "agonist", "antagonist", "silencing"]))
    clinical = sum(1 for r in records if "临床" in r.get("annotation", {}).get("article_type", "") or "人群" in r.get("annotation", {}).get("article_type", ""))
    if causal:
        highlights.append(f"机制亮点：入选文献中约{causal}篇包含基因/药理/治疗干预线索，优先阅读这些论文的因果链和救援实验。")
    if clinical:
        highlights.append(f"转化亮点：有{clinical}篇属于临床/人群研究，适合重点寻找可与DR分级、OCTA或体液脂质指标对接的候选标志物。")
    top_names = "；".join(clean(r.get("title", "")) for r in top[:3])
    highlights.append(f"优先精读：{top_names}。这些文章在DR直接相关性、脂质机制和证据层级上综合排名靠前。")
    return highlights[:4]


def _aff_text(values: Any) -> str:
    vals = values if isinstance(values, list) else [values]
    return "；".join(clean(x) for x in vals if clean(x)) or "未从公开结构化元数据确认"


def journal_metric_text(record: Dict) -> str:
    m = record.get("journal_metrics") or {}
    if m.get("jif") is None:
        return "未检索到可靠JCR-based记录（不填猜测值）"
    return f"JCR 2026（2025指标年）：JIF {m['jif']:.3g}；{m.get('jcr_quartile','N/A')}；类别排名 {m.get('jcr_category_rank') or '公开目录未提供'}"


def render_md(records: List[Dict], candidates: int, days: int, raw_candidates: int) -> str:
    today = date.today().isoformat(); start = (date.today() - timedelta(days=days - 1)).isoformat()
    summ = direction_summary(records); highlights = weekly_highlights(records)
    L = [f"# DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}", "", "## 本周看点", ""]
    L += [f"- {x}" for x in highlights]
    L += ["", f"**检索范围:** {start} 至 {today}（{days}天）  ", f"**原始抓取记录:** {raw_candidates}  ", f"**跨源严格去重后候选:** {candidates}  ", f"**本周纳入:** {len(records)}（按质量阈值动态变化，不固定20篇）  ", "**JCR说明:** 当前使用2026 JCR release对应的2025指标年；JIF/Q来自公开JCR-based目录，正式职称/基金/投稿评价建议再以机构订阅的Clarivate JCR核验。", ""]
    L += ["## 1. Research landscape / 研究方向分布", "", "|方向|篇数|占比|", "|---|---:|---:|"] + [f"|{x['direction']}|{x['papers']}|{x['share']}%|" for x in summ] + ["", "## 2. Weekly papers / 本周重点文献", ""]
    for i, r in enumerate(records, 1):
        a = r.get("annotation", {}); m = r.get("journal_metrics", {})
        L += [f"### {i}. {r.get('title','Untitled')}", "", f"**中文题目:** {a.get('title_zh','N/A')}", "", f"**第一作者:** {r.get('first_author') or 'N/A'}", f"**第一作者单位:** {_aff_text(r.get('first_author_affiliations'))}", f"**通讯作者:** {r.get('corresponding_author') or '公开结构化元数据未确认'}", f"**通讯作者单位:** {_aff_text(r.get('corresponding_author_affiliations'))}", f"**期刊（全称）:** {r.get('journal') or 'N/A'}", f"**JCR/影响因子:** {journal_metric_text(r)}", f"**发表日期:** {r.get('publication_date') or 'N/A'}  ", f"**文章类型:** {a.get('article_type') or 'N/A'}", f"**证据层级:** {a.get('evidence_level','N/A')}  ", f"**相关性评分:** {r.get('relevance_score',0)} ({r.get('relevance_tier','')})  ", f"**脂质方向:** {_direction_cn(r.get('direction_tags') or [])}", "", "**Innovation points / 中文创新点**", ""] + [f"- {x}" for x in a.get('innovation_points', [])] + ["", "**Why it matters / 对你的研究的启发**", ""] + [f"- {x}" for x in a.get('research_focus', [])] + ["", "**Limitations / 注意事项**", ""] + [f"- {x}" for x in a.get('limitations_or_cautions', [])] + ["", "**Abstract / 英文摘要**", "", r.get('abstract') or "N/A", "", "**中文摘要（自动翻译）**", "", a.get('abstract_zh','N/A'), "", "**Keywords / 关键词**", "", f"English: {', '.join(a.get('keywords_en') or [])}", f"中文: {', '.join(a.get('keywords_zh') or [])}", "", f"**PMID:** {r.get('pmid') or 'N/A'}  ", f"**DOI:** {r.get('doi') or 'N/A'}  ", f"**PMCID:** {r.get('pmcid') or 'N/A'}  ", f"**Link:** {r.get('url') or 'N/A'}", "", "---", ""]
    L += ["## 3. Google Scholar supplement / Google Scholar补充检索", "", "系统不自动抓取Google Scholar；以下链接用于人工交叉核查。", google_scholar_url("diabetic retinopathy lipid metabolism"), ""]
    return "\n".join(L).rstrip() + "\n"


def render_html(records: List[Dict], candidates: int, days: int, raw_candidates: int) -> str:
    today = date.today().isoformat(); summ = direction_summary(records); highlights = weekly_highlights(records)
    P = [f"<html><body><h1>DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}</h1>", "<h2>本周看点</h2><ul>"]
    P += [f"<li>{html.escape(x)}</li>" for x in highlights]
    P += ["</ul>", f"<p>Coverage: last {days} days; raw records: {raw_candidates}; strictly deduplicated candidates: {candidates}; included: {len(records)} (dynamic).</p>", "<p><b>JCR说明：</b>当前使用2026 JCR release对应的2025指标年；JIF/Q来自公开JCR-based目录，正式评价建议以机构订阅的Clarivate JCR核验。</p>", "<h2>Research landscape / 研究方向分布</h2><table border='1' cellpadding='6'><tr><th>Direction</th><th>Papers</th><th>Share</th></tr>"]
    for x in summ:
        P.append(f"<tr><td>{html.escape(x['direction'])}</td><td>{x['papers']}</td><td>{x['share']}%</td></tr>")
    P.append("</table><h2>Weekly papers / 本周重点文献</h2>")
    for i, r in enumerate(records, 1):
        a = r.get("annotation", {}); url = html.escape(r.get("url") or "#")
        P += [f"<h3>{i}. <a href='{url}'>{html.escape(r.get('title','Untitled'))}</a></h3>", f"<p><b>中文题目:</b> {html.escape(a.get('title_zh','N/A'))}</p>", f"<p><b>第一作者:</b> {html.escape(r.get('first_author') or 'N/A')}<br><b>第一作者单位:</b> {html.escape(_aff_text(r.get('first_author_affiliations')))}<br><b>通讯作者:</b> {html.escape(r.get('corresponding_author') or '公开结构化元数据未确认')}<br><b>通讯作者单位:</b> {html.escape(_aff_text(r.get('corresponding_author_affiliations')))}<br><b>期刊（全称）:</b> {html.escape(r.get('journal') or 'N/A')}<br><b>JCR/影响因子:</b> {html.escape(journal_metric_text(r))}<br><b>日期:</b> {html.escape(r.get('publication_date') or 'N/A')}<br><b>文章类型:</b> {html.escape(a.get('article_type','N/A'))}<br><b>证据层级:</b> {html.escape(a.get('evidence_level','N/A'))}<br><b>评分:</b> {r.get('relevance_score',0)}<br><b>方向:</b> {html.escape(_direction_cn(r.get('direction_tags') or []))}</p>", "<p><b>Innovation points / 中文创新点</b></p>", "<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in a.get('innovation_points', [])) + "</ul>", "<p><b>Why it matters / 对你的研究的启发</b></p>", "<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in a.get('research_focus', [])) + "</ul>", "<p><b>Limitations / 注意事项</b></p>", "<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in a.get('limitations_or_cautions', [])) + "</ul>", f"<p><b>Abstract / 英文摘要</b></p><p>{html.escape(r.get('abstract') or 'N/A').replace(chr(10), '<br>')}</p>", f"<p><b>中文摘要（自动翻译）</b></p><p>{html.escape(a.get('abstract_zh','N/A')).replace(chr(10), '<br>')}</p>", f"<p><b>Keywords:</b> {html.escape(', '.join(a.get('keywords_en') or []))}<br><b>关键词:</b> {html.escape(', '.join(a.get('keywords_zh') or []))}</p>", f"<p>PMID: {html.escape(r.get('pmid') or 'N/A')} | DOI: {html.escape(r.get('doi') or 'N/A')} | PMCID: {html.escape(r.get('pmcid') or 'N/A')}<br><a href='{url}'>Open article / 打开文章</a></p><hr>"]
    P += [f"<h2>Google Scholar supplement / Google Scholar补充检索</h2><p>用于人工交叉核查：</p><p><a href='{html.escape(google_scholar_url('diabetic retinopathy lipid metabolism'))}'>Google Scholar search</a></p>", "</body></html>"]
    return "".join(P)


def run_weekly_brief(days: int = 21, per_query: int = 80, minimum_score: int = 55, hard_max: int = 60, output_dir: str = "Output/weekly") -> None:
    # Correct PubMed relative-date syntax at runtime while keeping the original module reusable.
    original_pubmed = sources.search_pubmed
    def fixed_pubmed(query, retmax=100, days=None, email=None, api_key=None):
        if days is None or days <= 0:
            return original_pubmed(query, retmax=retmax, days=days, email=email, api_key=api_key)
        term = f"({query}) AND (last {days} days[dp])"
        params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax, "sort": "pub date"}
        if email: params["email"] = email
        if api_key: params["api_key"] = api_key
        data = sources._request_json(f"{sources.PUBMED_BASE}/esearch.fcgi", params)
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids: return []
        fetch = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        if email: fetch["email"] = email
        if api_key: fetch["api_key"] = api_key
        response = requests.get(f"{sources.PUBMED_BASE}/efetch.fcgi", params=fetch, timeout=60)
        response.raise_for_status()
        return sources._parse_pubmed_xml(response.text, query)
    sources.search_pubmed = fixed_pubmed

    print(f"[retrieve] days={days} per_query={per_query}")
    raw = search_all(per_query=per_query, days=days, email=os.getenv("NCBI_EMAIL", "171142515@qq.com"))
    raw_count = len(raw)
    records = strict_deduplicate(raw)
    candidates = len(records)
    records = enrich_direction_tags(records)
    ranked = rank_records(records)
    # Prefer strong papers; only relax to 40 when there are fewer than five strong papers.
    strong = [r for r in ranked if int(r.get("relevance_score", 0)) >= minimum_score]
    if len(strong) < 5:
        strong = [r for r in ranked if int(r.get("relevance_score", 0)) >= 40]
    strong = strong[:hard_max] if hard_max > 0 else strong

    # Journal metrics are a hard completeness gate: no paper reaches the email without JIF + JCR quartile.
    strong = annotate_journal_metrics(strong)
    selected = [r for r in strong if (r.get("journal_metrics") or {}).get("jif") is not None and (r.get("journal_metrics") or {}).get("jcr_quartile")]
    if not selected:
        raise RuntimeError("No selected paper has a complete current JCR-based JIF + quartile record; refusing to send an incomplete weekly email.")

    print(f"[rank] raw={raw_count} candidates={candidates} ranked={len(ranked)} strong={len(strong)} selected={len(selected)}")
    enrich_selected_records(selected)
    for i, r in enumerate(selected, 1):
        print(f"[annotate] {i}/{len(selected)} {r.get('title','')[:90]}")
        r["annotation"] = annotate(r)

    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True); d = date.today().isoformat()
    md = render_md(selected, candidates, days, raw_count); h = render_html(selected, candidates, days, raw_count)
    (out / f"{d}.md").write_text(md, encoding="utf-8")
    (out / f"{d}.html").write_text(h, encoding="utf-8")
    payload = {"date": d, "days": days, "raw_candidates": raw_count, "candidates": candidates, "included": len(selected), "dynamic_count": True, "papers": selected, "google_scholar_url": google_scholar_url("diabetic retinopathy lipid metabolism")}
    (out / f"{d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {out}/{d}.html papers={len(selected)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=int(os.getenv("WEEKLY_DAYS", "21")))
    p.add_argument("--per-query", type=int, default=int(os.getenv("WEEKLY_PER_QUERY", "80")))
    p.add_argument("--minimum-score", type=int, default=int(os.getenv("WEEKLY_MINIMUM_SCORE", "55")))
    p.add_argument("--hard-max", type=int, default=int(os.getenv("WEEKLY_HARD_MAX", "60")))
    p.add_argument("--output-dir", default=os.getenv("WEEKLY_OUTPUT_DIR", "Output/weekly"))
    args = p.parse_args()
    run_weekly_brief(args.days, args.per_query, args.minimum_score, args.hard_max, args.output_dir)
