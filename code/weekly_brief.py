"""Weekly bilingual literature brief for diabetic retinopathy x lipid metabolism.

Retrieval is deliberately broad across lipid-metabolism directions. AI is used only
for bilingual interpretation and research-focused annotations; it does not decide
which lipid subfield is scientifically preferable.
"""

from __future__ import annotations

import argparse
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

from daily_pipeline import enrich_direction_tags
from literature_sources import search_all
from relevance_ranker import rank_records


AI_SYSTEM_PROMPT = """You are a biomedical literature analyst helping a PhD researcher build a broad diabetic retinopathy (DR) x lipid metabolism research radar. Do not assume lipid droplets are the preferred direction. The researcher wants to discover promising subfields across fatty acids/PUFAs, phospholipids, sphingolipids/ceramides, cholesterol/oxysterols, glycerolipids, lipoproteins, lipid mediators, lipid peroxidation/ferroptosis, lipid droplets, lipid transcriptional regulation, lipid enzymes/transport, and mitochondrial lipid metabolism.

Return strict JSON only. Base every statement on the supplied title, abstract, metadata and index terms. Never invent experimental results, cohorts, mechanisms, sample sizes, affiliations, or conclusions that are not supported. If the abstract is insufficient to establish an innovation point, explicitly say so and phrase the point as "abstract-supported" or "requires full-text verification".

The researcher is especially interested in retinal neurovascular unit biology, retinal endothelial cells, Müller glia, RPE/RGC biology, blood-retinal barrier, lipid homeostasis, metabolic stress, mitochondrial biology, inflammation/oxidative stress, and translational relevance. These are for interpretation only and must not bias the ranking score.

JSON schema:
{
  "title_zh": "Chinese translation of title",
  "abstract_zh": "faithful Chinese translation of the supplied abstract",
  "keywords_en": ["5-8 concise keywords"],
  "keywords_zh": ["corresponding Chinese keywords"],
  "innovation_points": ["up to 3 concise abstract-supported innovation points"],
  "research_focus": ["up to 4 concrete reasons/questions relevant to the research radar"],
  "evidence_level": "clinical/human | animal | cell/in vitro | multi-level | review/meta-analysis | unclear",
  "limitations_or_cautions": ["up to 3 important cautions or gaps visible from metadata/abstract"]
}
"""


def _item_to_text(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        vals = []
        for v in item.values():
            if isinstance(v, (str, int, float)):
                vals.append(str(v))
        return " ".join(vals)
    return str(item)


def _list_to_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (str, dict)):
        return _item_to_text(value)
    try:
        return "; ".join(_item_to_text(x) for x in value)
    except TypeError:
        return _item_to_text(value)


def _authors_text(record: Dict) -> str:
    authors = record.get("authors") or []
    names = []
    for author in authors:
        if isinstance(author, dict):
            name = author.get("name") or author.get("full_name") or author.get("author_name")
            if name:
                names.append(str(name))
        elif author:
            names.append(str(author))
    return ", ".join(names) if names else "N/A"


def _api_settings() -> Dict[str, str]:
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    api2d_key = os.getenv("API2D_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
    if openai_key:
        return {"key": openai_key, "url": "https://api.openai.com/v1/chat/completions", "model": model}
    if api2d_key:
        return {"key": api2d_key, "url": "https://openai.api2d.net/v1/chat/completions", "model": os.getenv("API2D_MODEL", "gpt-3.5-turbo")}
    raise RuntimeError("No AI API key configured. Add OPENAI_API_KEY or API2D_API_KEY as a GitHub Actions secret.")


def _call_ai(record: Dict, settings: Dict[str, str], retries: int = 3) -> Dict:
    abstract = record.get("abstract", "") or ""
    if not abstract.strip():
        abstract = "No abstract available."
    payload_record = {
        "title": record.get("title", ""),
        "authors": _authors_text(record),
        "journal": record.get("journal", ""),
        "publication_date": record.get("publication_date", ""),
        "publication_types": _list_to_text(record.get("publication_types")),
        "mesh_terms": _list_to_text(record.get("mesh_terms")),
        "abstract": abstract,
        "directions": record.get("direction_tags") or record.get("lipid_directions") or [],
        "relevance_score": record.get("relevance_score", 0),
    }
    messages = [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload_record, ensure_ascii=False)},
    ]
    body = {"model": settings["model"], "messages": messages, "temperature": 0.1}
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.post(
                settings["url"],
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['key']}"},
                json=body,
                timeout=90,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            return json.loads(content)
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"AI enrichment failed after {retries} attempts: {last_error}")


def enrich_with_ai(records: List[Dict]) -> List[Dict]:
    settings = _api_settings()
    output = []
    for idx, record in enumerate(records, 1):
        print(f"[ai] enriching {idx}/{len(records)}: {record.get('title', '')[:100]}")
        enriched = dict(record)
        try:
            annotation = _call_ai(record, settings)
            enriched["ai_annotation"] = annotation
        except Exception as exc:
            print(f"[ai] ERROR: {exc}")
            raise
        output.append(enriched)
    return output


def _direction_summary(records: Iterable[Dict]) -> List[Dict]:
    counts = Counter()
    records = list(records)
    for record in records:
        counts.update(record.get("direction_tags") or ["Other lipid-related"])
    total = len(records)
    return [
        {"direction": k, "papers": v, "share": round(v / total * 100, 1) if total else 0.0}
        for k, v in counts.most_common()
    ]


def _md_list(items: Any) -> str:
    if not items:
        return "- N/A"
    return "\n".join(f"- {str(x)}" for x in items)


def render_markdown(records: List[Dict], candidates: int, days: int, top_n: int) -> str:
    today = date.today().isoformat()
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    summary = _direction_summary(records)
    lines = [
        f"# DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}",
        "",
        f"**Coverage / 检索范围:** {start} to {today} ({days} days)  ",
        f"**Unique candidates / 去重后候选:** {candidates}  ",
        f"**Papers included / 纳入文献:** {len(records)}  ",
        f"**Ranking / 排序:** transparent DR × lipid-metabolism relevance score; lipid sub-directions are descriptive, not preferential.",
        "",
        "## 1. Research landscape / 研究方向分布",
        "",
        "| Direction / 方向 | Papers / 篇数 | Share / 占比 |",
        "|---|---:|---:|",
    ]
    for row in summary:
        lines.append(f"| {row['direction']} | {row['papers']} | {row['share']}% |")

    lines += ["", "## 2. Weekly papers / 本周重点文献", ""]
    for i, record in enumerate(records, 1):
        ai = record.get("ai_annotation") or {}
        lines += [
            f"### {i}. {record.get('title', 'Untitled')}",
            "",
            f"**中文题目 / Chinese title:** {ai.get('title_zh', 'N/A')}",
            "",
            f"**Authors / 作者:** {_authors_text(record)}",
            "",
            f"**Journal / 期刊:** {record.get('journal') or 'N/A'}  ",
            f"**Publication date / 发表日期:** {record.get('publication_date') or 'N/A'}  ",
            f"**Article type / 文章类型:** {_list_to_text(record.get('publication_types')) or 'N/A'}  ",
            f"**Evidence level / 证据层级:** {ai.get('evidence_level', 'N/A')}  ",
            f"**Relevance score / 相关性评分:** {record.get('relevance_score', 0)} ({record.get('relevance_tier', '')})  ",
            f"**Directions / 脂质方向:** {', '.join(record.get('direction_tags') or [])}",
            "",
            "**Abstract / 英文摘要**",
            "",
            record.get('abstract') or "N/A",
            "",
            "**摘要 / 中文摘要**",
            "",
            ai.get('abstract_zh', 'N/A'),
            "",
            "**Keywords / 关键词**",
            "",
            f"English: {', '.join(ai.get('keywords_en') or []) or 'N/A'}  ",
            f"中文: {', '.join(ai.get('keywords_zh') or []) or 'N/A'}",
            "",
            "**Innovation points / 本文创新点（基于摘要）**",
            "",
            _md_list(ai.get('innovation_points')),
            "",
            "**Why it matters for your research / 对你的研究有什么值得关注的地方**",
            "",
            _md_list(ai.get('research_focus')),
            "",
            "**Limitations / 注意事项**",
            "",
            _md_list(ai.get('limitations_or_cautions')),
            "",
            f"**PMID:** {record.get('pmid') or 'N/A'}  ",
            f"**DOI:** {record.get('doi') or 'N/A'}  ",
            f"**Link / 链接:** {record.get('url') or 'N/A'}",
            "",
            "---",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def _html_list(items: Any) -> str:
    if not items:
        return "<p>N/A</p>"
    return "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in items) + "</ul>"


def render_html(records: List[Dict], candidates: int, days: int) -> str:
    today = date.today().isoformat()
    summary = _direction_summary(records)
    parts = [
        f"<html><body><h1>DR × Lipid Metabolism Weekly Literature Brief / 糖尿病视网膜病变 × 脂质代谢周报 — {today}</h1>",
        f"<p>Coverage / 检索范围: last {days} days; unique candidates / 候选 {candidates}; included / 纳入 {len(records)}.</p>",
        "<h2>Research landscape / 研究方向分布</h2><table border='1' cellpadding='6'><tr><th>Direction / 方向</th><th>Papers / 篇数</th><th>Share / 占比</th></tr>",
    ]
    for row in summary:
        parts.append(f"<tr><td>{html.escape(row['direction'])}</td><td>{row['papers']}</td><td>{row['share']}%</td></tr>")
    parts.append("</table><h2>Weekly papers / 本周重点文献</h2>")
    for i, record in enumerate(records, 1):
        ai = record.get('ai_annotation') or {}
        title = html.escape(record.get('title', 'Untitled'))
        url = html.escape(record.get('url') or '')
        parts += [
            f"<h3>{i}. <a href='{url}'>{title}</a></h3>",
            f"<p><b>中文题目:</b> {html.escape(ai.get('title_zh', 'N/A'))}</p>",
            f"<p><b>Authors / 作者:</b> {html.escape(_authors_text(record))}</p>",
            f"<p><b>Journal / 期刊:</b> {html.escape(record.get('journal') or 'N/A')} | <b>Date / 日期:</b> {html.escape(record.get('publication_date') or 'N/A')} | <b>Score / 评分:</b> {record.get('relevance_score', 0)}</p>",
            f"<p><b>Directions / 方向:</b> {html.escape(', '.join(record.get('direction_tags') or []))}</p>",
            "<p><b>Abstract / 英文摘要</b></p>",
            f"<p>{html.escape(record.get('abstract') or 'N/A').replace(chr(10), '<br>')}</p>",
            "<p><b>摘要 / 中文摘要</b></p>",
            f"<p>{html.escape(ai.get('abstract_zh', 'N/A')).replace(chr(10), '<br>')}</p>",
            f"<p><b>Keywords / 关键词:</b> {html.escape(', '.join(ai.get('keywords_en') or []))}<br>{html.escape(', '.join(ai.get('keywords_zh') or []))}</p>",
            "<p><b>Innovation points / 本文创新点</b></p>", _html_list(ai.get('innovation_points')),
            "<p><b>Research focus / 值得你重点关注</b></p>", _html_list(ai.get('research_focus')),
            "<p><b>Limitations / 注意事项</b></p>", _html_list(ai.get('limitations_or_cautions')),
            f"<p><b>PMID:</b> {html.escape(record.get('pmid') or 'N/A')} | <b>DOI:</b> {html.escape(record.get('doi') or 'N/A')}<br><a href='{url}'>Open article / 打开文章</a></p><hr>",
        ]
    parts.append("</body></html>")
    return "".join(parts)


def run_weekly_brief(days: int = 14, per_query: int = 100, top_n: int = 20, minimum_score: int = 40, output_dir: str = "Output/weekly") -> Dict:
    records = search_all(per_query=per_query, days=days)
    candidates = len(records)
    ranked = rank_records(records, top_n=top_n, minimum_score=minimum_score)
    ranked = enrich_direction_tags(ranked)
    if not ranked:
        raise RuntimeError("No records passed the relevance threshold; refusing to send an empty weekly brief.")
    ranked = enrich_with_ai(ranked)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = out / f"{today}.md"
    html_path = out / f"{today}.html"
    json_path = out / f"{today}.json"

    md_path.write_text(render_markdown(ranked, candidates, days, top_n), encoding="utf-8")
    html_path.write_text(render_html(ranked, candidates, days), encoding="utf-8")
    payload = {
        "date": today,
        "coverage_days": days,
        "candidate_count": candidates,
        "included_count": len(ranked),
        "direction_summary": _direction_summary(ranked),
        "records": ranked,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[weekly] candidates={candidates}; included={len(ranked)}")
    print(f"[weekly] markdown={md_path}")
    print(f"[weekly] html={html_path}")
    print(f"[weekly] json={json_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="DR x lipid metabolism weekly bilingual literature brief")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--per-query", type=int, default=100)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--minimum-score", type=int, default=40)
    parser.add_argument("--output-dir", default="Output/weekly")
    args = parser.parse_args()
    run_weekly_brief(args.days, args.per_query, args.top_n, args.minimum_score, args.output_dir)


if __name__ == "__main__":
    main()
