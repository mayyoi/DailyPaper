"""Daily literature pipeline for diabetic retinopathy x lipid metabolism.

Flow:
    PubMed + Europe PMC -> deduplicate -> relevance ranking -> direction tags
    -> Markdown + JSON output.

This module is intentionally independent of email delivery and AI summaries.
It can be run locally first, then scheduled by GitHub Actions later.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List

from literature_sources import search_all
from relevance_ranker import rank_records


# These are deliberately parallel research-direction tags. They describe what a
# paper is about; they do NOT give extra relevance points to any one direction.
DIRECTION_RULES = {
    "Fatty acid / PUFA": (
        "fatty acid", "fatty-acid", "polyunsaturated fatty acid", "pufa",
        "omega-3", "omega-6", "arachidonic acid", "docosahexaenoic acid",
        "eicosapentaenoic acid", "beta-oxidation", "fatty acid oxidation",
    ),
    "Phospholipid": (
        "phospholipid", "phosphatidylcholine", "phosphatidylethanolamine",
        "phosphatidylserine", "phosphatidylinositol", "lysophosphatidylcholine",
    ),
    "Sphingolipid / ceramide": (
        "ceramide", "sphingolipid", "sphingosine", "sphingomyelin",
        "sphingosine-1-phosphate", "s1p",
    ),
    "Cholesterol / oxysterol": (
        "cholesterol", "oxysterol", "cholesteryl", "cholesterol ester",
    ),
    "Glycerolipid / TG / DAG": (
        "triglyceride", "triacylglycerol", "diacylglycerol", "dag", "monoacylglycerol",
    ),
    "Lipid mediator": (
        "prostaglandin", "leukotriene", "thromboxane", "resolvin", "protectin",
        "maresin", "lipoxin", "eicosanoid",
    ),
    "Lipoprotein": (
        "lipoprotein", "ldl", "hdl", "vldl", "lipoprotein(a)", "lpa",
    ),
    "Lipid peroxidation / ferroptosis": (
        "lipid peroxidation", "lipid peroxide", "oxidized lipid", "oxidative lipid",
        "ferroptosis", "4-hydroxynonenal", "4-hne", "malondialdehyde",
    ),
    "Lipid droplet": (
        "lipid droplet", "lipid droplets", "perilipin", "plin1", "plin2", "plin3",
        "plin4", "plin5", "cidec", "dgat1", "dgat2", "pnpla2", "atgl",
    ),
    "Lipid transcriptional regulation": (
        "liver x receptor", "lxr", "nr1h3", "nr1h2", "ppar", "ppara", "pparg",
        "srebp", "srebf1", "srebf2", "fasn", "acaca", "cpt1", "acsl", "elovl",
    ),
    "Mitochondrial lipid metabolism": (
        "mitochondrial fatty acid", "mitochondrial lipid", "lipid oxidation",
        "fatty acid oxidation", "mitochondrial beta-oxidation",
    ),
}


def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def _item_to_text(item: Any) -> str:
    """Convert strings or structured API items into searchable text."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        values = []
        for value in item.values():
            if isinstance(value, (str, int, float)):
                values.append(str(value))
        return " ".join(values)
    return str(item)


def _list_to_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (str, dict)):
        return _item_to_text(value)
    try:
        return " ".join(_item_to_text(item) for item in value)
    except TypeError:
        return _item_to_text(value)


def _record_text(record: Dict) -> str:
    fields = [
        record.get("title", ""),
        record.get("abstract", ""),
        _list_to_text(record.get("mesh_terms")),
        _list_to_text(record.get("publication_types")),
    ]
    return _normalise(" ".join(str(x) for x in fields))


def tag_directions(record: Dict) -> List[str]:
    """Assign all matching lipid-metabolism direction tags to one paper."""
    text = _record_text(record)
    tags = []
    for label, terms in DIRECTION_RULES.items():
        if any(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) for term in terms):
            tags.append(label)
    return tags or ["Other lipid-related"]


def enrich_direction_tags(records: Iterable[Dict]) -> List[Dict]:
    output = []
    for record in records:
        item = dict(record)
        item["direction_tags"] = tag_directions(item)
        output.append(item)
    return output


def direction_summary(records: Iterable[Dict]) -> List[Dict]:
    """Return direction counts sorted by number of papers, not by priority."""
    records = list(records)
    counts = Counter()
    for record in records:
        counts.update(record.get("direction_tags") or ["Other lipid-related"])
    total = len(records)
    rows = []
    for direction, count in counts.most_common():
        rows.append({
            "direction": direction,
            "papers": count,
            "share_of_ranked": round(count / total * 100, 1) if total else 0.0,
        })
    return rows


def _paper_line(index: int, record: Dict) -> str:
    title = record.get("title", "Untitled")
    journal = record.get("journal", "")
    pub_date = record.get("publication_date", "")
    score = record.get("relevance_score", 0)
    tier = record.get("relevance_tier", "")
    directions = ", ".join(record.get("direction_tags") or [])
    url = record.get("url", "")
    matched = ", ".join(x.get("label", "") for x in record.get("matched_rules", [])[:6])
    doi = record.get("doi", "")
    identifiers = []
    if record.get("pmid"):
        identifiers.append(f"PMID: {record['pmid']}")
    if doi:
        identifiers.append(f"DOI: {doi}")
    id_text = " | ".join(identifiers)
    link = f"[{title}]({url})" if url else title
    return (
        f"{index}. **{link}**\n"
        f"   - Journal: {journal or 'N/A'} | Date: {pub_date or 'N/A'} | Score: **{score}** ({tier})\n"
        f"   - Directions: {directions}\n"
        f"   - Why ranked: {matched or 'N/A'}\n"
        f"   - {id_text or 'No identifier'}"
    )


def render_markdown(records: List[Dict], candidates: int, days: int, top_n: int) -> str:
    today = date.today().isoformat()
    rows = direction_summary(records)
    must_read = [r for r in records if r.get("relevance_tier") == "must-read"]
    high = [r for r in records if r.get("relevance_tier") == "high"]
    expansion = [r for r in records if r.get("relevance_tier") == "expansion"]

    lines = [
        f"# DR × Lipid Metabolism Daily Literature — {today}",
        "",
        "> This is a discovery pipeline, not a claim that any one lipid pathway is the best research direction. Direction tags are descriptive and are not used to up-weight lipid droplets.",
        "",
        "## Run summary",
        f"- Search window: last **{days} days**",
        f"- Unique records after source deduplication: **{candidates}**",
        f"- Ranked records in this report: **{len(records)}**",
        f"- Requested top N: **{top_n}**",
        "",
        "## Lipid-metabolism direction distribution",
        "",
        "| Direction | Papers | Share of ranked papers |",
        "|---|---:|---:|",
    ]
    for row in rows:
        lines.append(f"| {row['direction']} | {row['papers']} | {row['share_of_ranked']}% |")

    sections = [
        ("Must-read", must_read),
        ("High relevance", high),
        ("Expansion / discovery", expansion),
    ]
    for heading, group in sections:
        lines.extend(["", f"## {heading}", ""])
        if not group:
            lines.append("No papers in this tier today.")
        else:
            for i, record in enumerate(group, 1):
                lines.append(_paper_line(i, record))
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(
    days: int = 30,
    per_query: int = 100,
    top_n: int = 30,
    minimum_score: int = 40,
    output_dir: str = "Output",
) -> Dict:
    """Run retrieval, deduplication, ranking, tagging and file output."""
    records = search_all(per_query=per_query, days=days)
    candidates = len(records)
    ranked = rank_records(records, top_n=top_n, minimum_score=minimum_score)
    ranked = enrich_direction_tags(ranked)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = out / f"{today}.md"
    json_path = out / f"{today}.json"

    md_path.write_text(render_markdown(ranked, candidates, days, top_n), encoding="utf-8")
    payload = {
        "date": today,
        "days": days,
        "candidate_count": candidates,
        "ranked_count": len(ranked),
        "direction_summary": direction_summary(ranked),
        "records": ranked,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[pipeline] unique candidates: {candidates}")
    print(f"[pipeline] ranked records: {len(ranked)}")
    print(f"[pipeline] markdown: {md_path}")
    print(f"[pipeline] json: {json_path}")
    print("[pipeline] directions:")
    for row in payload["direction_summary"]:
        print(f"  - {row['direction']}: {row['papers']}")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="DR x lipid metabolism literature pipeline")
    parser.add_argument("--days", type=int, default=30, help="Rolling publication window")
    parser.add_argument("--per-query", type=int, default=100, help="Maximum records per source/query")
    parser.add_argument("--top-n", type=int, default=30, help="Number of ranked papers to keep")
    parser.add_argument("--minimum-score", type=int, default=40, help="Minimum relevance score")
    parser.add_argument("--output-dir", default="Output", help="Output directory")
    args = parser.parse_args()
    run_pipeline(
        days=args.days,
        per_query=args.per_query,
        top_n=args.top_n,
        minimum_score=args.minimum_score,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
