"""Final runner for the dynamic DR x lipid weekly brief.

JCR metadata is preferred but no longer a hard exclusion gate. Papers with strong
DR x lipid relevance can be included when current JIF/JCR quartile cannot be
reliably matched, and the report explicitly flags the missing metric instead of
guessing a value.
"""
from __future__ import annotations
import argparse, json, os
from datetime import date
from pathlib import Path
import requests
import literature_sources as sources
import weekly_brief_v2 as v2
from daily_pipeline import enrich_direction_tags
from journal_metrics import annotate_journal_metrics
from metadata_enrichment import enrich_selected_records
from literature_sources import google_scholar_url, search_all
from relevance_ranker import rank_records


def _metric_text_with_warning(record):
    m = record.get("journal_metrics") or {}
    if m.get("jif") is None:
        return "⚠️ 未可靠匹配到当前JCR-based JIF + JCR Q；本周因DR×脂质相关性较高而硬纳入；不填猜测值"
    return f"JCR 2026（2025指标年）：JIF {m['jif']:.3g}；{m.get('jcr_quartile','N/A')}；类别排名 {m.get('jcr_category_rank') or '公开目录未提供'}"


def run_weekly_brief(days=21, per_query=80, minimum_score=55, hard_max=60, output_dir="Output/weekly"):
    # Patch PubMed relative-date syntax for the source helper.
    original_pubmed = sources.search_pubmed
    def fixed_pubmed(query, retmax=100, days=None, email=None, api_key=None):
        if days is None or days <= 0:
            return original_pubmed(query, retmax=retmax, days=days, email=email, api_key=api_key)
        term = f"({query}) AND (last {days} days[dp])"
        params = {"db":"pubmed","term":term,"retmode":"json","retmax":retmax,"sort":"pub date"}
        if email: params["email"] = email
        if api_key: params["api_key"] = api_key
        data = sources._request_json(f"{sources.PUBMED_BASE}/esearch.fcgi", params)
        ids = data.get("esearchresult",{}).get("idlist",[])
        if not ids: return []
        fetch = {"db":"pubmed","id":",".join(ids),"retmode":"xml"}
        if email: fetch["email"] = email
        if api_key: fetch["api_key"] = api_key
        response = requests.get(f"{sources.PUBMED_BASE}/efetch.fcgi",params=fetch,timeout=60); response.raise_for_status()
        return sources._parse_pubmed_xml(response.text,query)
    sources.search_pubmed = fixed_pubmed

    # Make the warning visible in both the HTML and Markdown renderers without changing v2's public API.
    v2.journal_metric_text = _metric_text_with_warning

    print(f"[retrieve] days={days} per_query={per_query}")
    raw = search_all(per_query=per_query, days=days, email=os.getenv("NCBI_EMAIL","171142515@qq.com"))
    raw_count = len(raw)
    records = v2.strict_deduplicate(raw)
    candidates = len(records)
    records = enrich_direction_tags(records)
    ranked = rank_records(records)

    # JCR completeness is a preference, not an exclusion gate. Strong papers can be
    # hard-included when the public JCR-based directory cannot reliably match them.
    quality_pool = [r for r in ranked if int(r.get("relevance_score",0)) >= 40]
    if hard_max > 0:
        quality_pool = quality_pool[:hard_max]
    quality_pool = annotate_journal_metrics(quality_pool)
    strong = [r for r in quality_pool if int(r.get("relevance_score",0)) >= minimum_score]
    selected = strong if len(strong) >= 5 else quality_pool
    if hard_max > 0:
        selected = selected[:hard_max]
    if not selected:
        raise RuntimeError("No paper meets the minimum DR×lipid relevance threshold; refusing to send an empty brief.")

    complete_n = 0
    missing_n = 0
    for r in selected:
        m = r.get("journal_metrics") or {}
        complete = m.get("jif") is not None and bool(m.get("jcr_quartile"))
        r["jcr_metric_complete"] = bool(complete)
        r["jcr_metric_note"] = "JIF + JCR Q已匹配" if complete else "⚠️ 未可靠匹配到当前JCR-based JIF + JCR Q；本周按高相关性硬纳入，不填猜测值"
        r["hard_included_due_to_missing_jcr"] = not complete
        if complete: complete_n += 1
        else: missing_n += 1

    print(f"[rank] raw={raw_count} candidates={candidates} ranked={len(ranked)} quality_pool={len(quality_pool)} strong={len(strong)} selected={len(selected)} jcr_complete={complete_n} jcr_missing={missing_n}")
    enrich_selected_records(selected)
    for i, r in enumerate(selected,1):
        print(f"[annotate] {i}/{len(selected)} {r.get('title','')[:90]}")
        r["annotation"] = v2.annotate(r)

    out = Path(output_dir); out.mkdir(parents=True,exist_ok=True); d=date.today().isoformat()
    md=v2.render_md(selected,candidates,days,raw_count); h=v2.render_html(selected,candidates,days,raw_count)
    (out/f"{d}.md").write_text(md,encoding="utf-8")
    (out/f"{d}.html").write_text(h,encoding="utf-8")
    payload={"date":d,"days":days,"raw_candidates":raw_count,"candidates":candidates,"included":len(selected),"dynamic_count":True,"jcr_complete":complete_n,"jcr_missing_hard_included":missing_n,"papers":selected,"google_scholar_url":google_scholar_url("diabetic retinopathy lipid metabolism")}
    (out/f"{d}.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"[done] {out}/{d}.html papers={len(selected)}")


if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--days",type=int,default=int(os.getenv("WEEKLY_DAYS","21"))); p.add_argument("--per-query",type=int,default=int(os.getenv("WEEKLY_PER_QUERY","80"))); p.add_argument("--minimum-score",type=int,default=int(os.getenv("WEEKLY_MINIMUM_SCORE","55"))); p.add_argument("--hard-max",type=int,default=int(os.getenv("WEEKLY_HARD_MAX","60"))); p.add_argument("--output-dir",default=os.getenv("WEEKLY_OUTPUT_DIR","Output/weekly")); a=p.parse_args(); run_weekly_brief(a.days,a.per_query,a.minimum_score,a.hard_max,a.output_dir)
