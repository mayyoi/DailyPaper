"""Transparent relevance scoring for diabetic retinopathy x lipid metabolism.

Important design principle:
    This module does NOT assume that lipid droplets are the user's research
    direction. Lipid-metabolism subfields are treated as approximately equal
    evidence categories so that later trend analysis can discover promising
    directions from the literature rather than from hard-coded preferences.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple


# Core disease/context rules. These intentionally carry more weight than any
# individual lipid-metabolism subfield.
CORE_RULES: List[Tuple[str, int, Tuple[str, ...]]] = [
    ("diabetic retinopathy", 35, (
        "diabetic retinopathy", "diabetic retinal disease", "diabetic retinopathy-associated",
    )),
    ("diabetic macular edema", 28, (
        "diabetic macular edema", "diabetic macular oedema", "dme",
    )),
    ("retina/retinal", 10, ("retina", "retinal")),
    ("lipid metabolism", 15, (
        "lipid metabolism", "lipid metabolic", "lipid homeostasis", "lipid remodeling",
        "lipid remodelling", "lipid dysregulation", "lipid metabolic pathway",
    )),
    ("lipotoxicity", 10, ("lipotoxicity", "lipotoxic")),
]

# These are deliberately near-equal. The goal is discovery, not to privilege
# lipid droplets, PLINs, ceramides, LXR, or any other preselected hypothesis.
LIPID_DIRECTION_RULES: List[Tuple[str, int, Tuple[str, ...]]] = [
    ("fatty acid", 8, (
        "fatty acid", "fatty acid metabolism", "fatty acid oxidation",
        "fatty acid synthesis", "beta-oxidation", "β-oxidation", "lipid oxidation",
    )),
    ("PUFA/eicosanoid", 8, (
        "polyunsaturated fatty acid", "pufa", "arachidonic acid", "docosahexaenoic acid",
        "dha", "eicosanoid", "prostaglandin", "leukotriene", "resolvin",
    )),
    ("phospholipid", 8, (
        "phospholipid", "phospholipids", "phosphatidylcholine", "phosphatidylethanolamine",
        "phosphatidylserine", "phosphatidylinositol", "lysophosphatidylcholine",
    )),
    ("sphingolipid/ceramide", 8, (
        "sphingolipid", "ceramide", "sphingosine", "sphingomyelin", "sphingosine-1-phosphate",
    )),
    ("cholesterol/oxysterol", 8, (
        "cholesterol", "cholesteryl", "oxysterol", "oxysterols", "cholesterol metabolism",
    )),
    ("glycerolipid", 8, (
        "triglyceride", "triacylglycerol", "diacylglycerol", "dag", "monoacylglycerol", "mag",
    )),
    ("lipoprotein", 8, (
        "lipoprotein", "ldl", "hdl", "vldl", "lipoprotein metabolism", "lipoprotein(a)",
    )),
    ("lipid mediator", 8, (
        "lipid mediator", "lipid mediators", "specialized pro-resolving mediator",
        "oxylipin", "oxylipins",
    )),
    ("lipid peroxidation/ferroptosis", 8, (
        "lipid peroxidation", "lipid peroxide", "oxidized lipid", "oxidised lipid",
        "ferroptosis", "lipid oxidative damage",
    )),
    ("lipid droplet", 8, (
        "lipid droplet", "lipid droplets", "perilipin", "plin1", "plin2", "plin3",
        "plin4", "plin5", "cidec", "dgat1", "dgat2", "pnpla2", "atgl",
    )),
    ("lipid transcriptional regulation", 8, (
        "liver x receptor", "lxr", "nr1h3", "nr1h2", "ppar", "ppara", "pparg",
        "srebp", "srebf1", "srebf2",
    )),
    ("lipid enzyme/transport", 8, (
        "fasn", "acaca", "acetyl-coa carboxylase", "cpt1", "cpt1a", "cpt1b",
        "acsl", "elovl", "fabp", "fatp", "cd36",
    )),
]

BIOLOGICAL_CONTEXT_RULES: List[Tuple[str, int, Tuple[str, ...]]] = [
    ("retinal endothelial", 12, (
        "retinal endothelial", "retinal microvascular endothelial", "endothelial cell",
        "microvascular endothelial",
    )),
    ("Muller glia", 12, (
        "müller cell", "muller cell", "müller glia", "muller glia",
    )),
    ("RPE", 10, (
        "retinal pigment epithelium", "retinal pigment epithelial", "rpe",
    )),
    ("RGC", 10, (
        "retinal ganglion cell", "retinal ganglion cells", "rgc",
    )),
    ("BRB/RNVU", 12, (
        "blood-retinal barrier", "blood retinal barrier", "brb",
        "retinal neurovascular unit", "rnvu",
    )),
    ("mitochondrial metabolism", 5, (
        "mitochondrial", "mitochondrial metabolism", "mitochondrial dysfunction",
    )),
    ("human/clinical", 8, (
        "patient", "patients", "clinical", "cohort", "human", "serum", "plasma", "aqueous humor",
    )),
    ("animal/cell model", 3, (
        "mouse", "mice", "rat", "rats", "streptozotocin", "db/db", "cell culture", "in vitro",
    )),
]

REVIEW_BONUS = 3


def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def _field_text(record: Dict) -> str:
    parts = [
        record.get("title", ""),
        record.get("abstract", ""),
        " ".join(record.get("mesh_terms") or []),
        " ".join(record.get("publication_types") or []),
    ]
    return _normalise(" ".join(str(x) for x in parts))


def _matches(text: str, terms: Tuple[str, ...]) -> bool:
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)
        for term in terms
    )


def score_record(record: Dict) -> Dict:
    """Score one record and attach both relevance evidence and direction tags."""
    text = _field_text(record)
    score = 0
    matched = []
    directions = []

    for label, points, terms in CORE_RULES:
        if _matches(text, terms):
            score += points
            matched.append({"label": label, "points": points})

    for label, points, terms in LIPID_DIRECTION_RULES:
        if _matches(text, terms):
            score += points
            directions.append(label)
            matched.append({"label": label, "points": points})

    for label, points, terms in BIOLOGICAL_CONTEXT_RULES:
        if _matches(text, terms):
            score += points
            matched.append({"label": label, "points": points})

    pub_types = " ".join(record.get("publication_types") or []).lower()
    if "review" in pub_types or "meta-analysis" in pub_types or "systematic review" in pub_types:
        score += REVIEW_BONUS
        matched.append({"label": "review/meta-analysis", "points": REVIEW_BONUS})

    # Strong safeguard: a paper must directly connect DR/DME with a lipid core
    # concept to enter the main discovery pool.
    direct_disease = _matches(text, (
        "diabetic retinopathy", "diabetic retinal disease", "diabetic macular edema",
        "diabetic macular oedema", "dme",
    ))
    lipid_core = _matches(text, (
        "lipid metabolism", "lipid metabolic", "lipid homeostasis", "lipid remodeling",
        "lipid remodelling", "lipotoxicity", "fatty acid", "pufa", "phospholipid",
        "sphingolipid", "ceramide", "cholesterol", "triglyceride", "diacylglycerol",
        "lipoprotein", "lipid mediator", "lipid peroxidation", "oxidized lipid",
        "ferroptosis", "lipid droplet", "perilipin", "plin1", "plin2", "plin3",
        "plin4", "plin5", "lxr", "nr1h3", "nr1h2", "ppar", "srebp",
    ))
    if not (direct_disease and lipid_core):
        score = min(score, 59)

    tier = "must-read" if score >= 70 else "high" if score >= 55 else "expansion" if score >= 40 else "exclude"
    result = dict(record)
    result.update({
        "relevance_score": score,
        "relevance_tier": tier,
        "matched_rules": matched,
        "lipid_directions": directions,
    })
    return result


def rank_records(records: Iterable[Dict], top_n: int = 30, minimum_score: int = 40) -> List[Dict]:
    """Score, filter and rank records, preferring relevance then newer papers."""
    scored = [score_record(record) for record in records]
    filtered = [x for x in scored if x["relevance_score"] >= minimum_score]

    def sort_key(record: Dict):
        date = record.get("publication_date", "") or ""
        return (record["relevance_score"], date)

    filtered.sort(key=sort_key, reverse=True)
    return filtered[:top_n]


def summarize_directions(records: Iterable[Dict]) -> Dict[str, int]:
    """Count how often each lipid-metabolism direction appears in a ranked set."""
    counts: Dict[str, int] = {}
    for record in records:
        for direction in record.get("lipid_directions", []):
            counts[direction] = counts.get(direction, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def explain(record: Dict) -> str:
    """Create a short human-readable explanation for the ranking."""
    matched = record.get("matched_rules", [])
    labels = ", ".join(x["label"] for x in matched[:8])
    directions = ", ".join(record.get("lipid_directions", [])) or "none"
    return (
        f"Score {record.get('relevance_score', 0)} "
        f"({record.get('relevance_tier', 'unknown')}); "
        f"directions: {directions}; evidence: {labels}"
    )


if __name__ == "__main__":
    demo = {
        "title": "Ceramide metabolism and diabetic retinopathy",
        "abstract": "Diabetic retinopathy is associated with altered sphingolipid metabolism in retinal endothelial cells.",
        "mesh_terms": [],
        "publication_types": ["Journal Article"],
    }
    scored = score_record(demo)
    print(explain(scored))
