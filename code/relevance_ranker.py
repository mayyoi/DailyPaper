"""Rule-based relevance scoring for diabetic retinopathy x lipid metabolism.

The score is designed as a transparent first-pass ranker. It uses title,
abstract, MeSH terms and publication type, so the ranking can be inspected
before we add an AI reranker in a later module.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple


# Points are intentionally transparent and additive. Direct DR + lipid links
# dominate broad background terms.
RULES: List[Tuple[str, int, Tuple[str, ...]]] = [
    ("diabetic retinopathy", 30, ("diabetic retinopathy", "diabetic retinal disease")),
    ("diabetic macular edema", 25, ("diabetic macular edema", "diabetic macular oedema", "dme")),
    ("retina/retinal", 15, ("retina", "retinal")),
    ("lipid metabolism", 20, ("lipid metabolism", "lipid homeostasis", "lipid metabolic")),
    ("fatty acid metabolism", 15, ("fatty acid metabolism", "fatty acid oxidation", "fatty acid synthesis", "beta-oxidation", "fatty acid")),
    ("lipid droplet", 15, ("lipid droplet", "lipid droplets")),
    ("PLIN1-5", 20, ("plin1", "plin2", "plin3", "plin4", "plin5", "perilipin")),
    ("CIDEC/DGAT/PNPLA2", 15, ("cidec", "dgat1", "dgat2", "pnpla2", "atgl")),
    ("ceramide/sphingolipid", 15, ("ceramide", "sphingolipid", "sphingosine", "sphingomyelin")),
    ("DAG/cholesterol/TG", 12, ("diacylglycerol", "dag", "cholesterol", "triglyceride", "triacylglycerol")),
    ("LXR", 20, ("liver x receptor", "lxr", "nr1h3", "nr1h2")),
    ("PPAR/SREBP", 15, ("ppar", "ppara", "pparg", "srebp", "srebf1", "srebf2")),
    ("lipid enzymes", 12, ("fasn", "acaca", "acc", "cpt1", "cpt1a", "cpt1b", "acsl", "elovl")),
    ("retinal endothelial", 15, ("retinal endothelial", "retinal microvascular endothelial", "endothelial cell", "microvascular endothelial")),
    ("Muller glia", 15, ("müller cell", "muller cell", "müller glia", "muller glia")),
    ("RPE", 10, ("retinal pigment epithelium", "retinal pigment epithelial", "rpe")),
    ("RGC", 10, ("retinal ganglion cell", "retinal ganglion cells", "rgc")),
    ("BRB/RNVU", 15, ("blood-retinal barrier", "blood retinal barrier", "brb", "retinal neurovascular unit", "rnvu")),
    ("ferroptosis/lipid", 10, ("ferroptosis", "lipid peroxidation", "oxidized lipid")),
    ("mitochondrial lipid", 10, ("mitochondrial lipid", "lipid oxidation", "mitochondrial fatty acid")),
    ("human/clinical", 10, ("patient", "patients", "clinical", "cohort", "human", "serum", "plasma", "aqueous humor")),
    ("animal/cell model", 5, ("mouse", "mice", "rat", "rats", "streptozotocin", "db/db", "cell culture", "in vitro")),
]

REVIEW_BONUS = 5


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


def score_record(record: Dict) -> Dict:
    """Return the record with transparent relevance score and matched rules."""
    text = _field_text(record)
    score = 0
    matched = []

    for label, points, terms in RULES:
        if any(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) for term in terms):
            score += points
            matched.append({"label": label, "points": points})

    pub_types = " ".join(record.get("publication_types") or []).lower()
    if "review" in pub_types or "meta-analysis" in pub_types or "systematic review" in pub_types:
        score += REVIEW_BONUS
        matched.append({"label": "review/meta-analysis", "points": REVIEW_BONUS})

    # Strong safeguard against retina papers that only mention lipids in passing.
    direct_disease = bool(re.search(r"diabetic retinopathy|diabetic retinal disease|diabetic macular edema", text))
    lipid_core = bool(re.search(r"lipid metabolism|lipid homeostasis|fatty acid|lipotoxicity|lipid droplet|ceramide|sphingolipid|cholesterol|triglyceride|diacylglycerol|perilipin|plin[1-5]|lxr|nr1h[23]|ppar|srebp", text))
    if not (direct_disease and lipid_core):
        score = min(score, 59)

    tier = "must-read" if score >= 80 else "high" if score >= 60 else "expansion" if score >= 40 else "exclude"
    result = dict(record)
    result.update({
        "relevance_score": score,
        "relevance_tier": tier,
        "matched_rules": matched,
    })
    return result


def rank_records(records: Iterable[Dict], top_n: int = 30, minimum_score: int = 40) -> List[Dict]:
    """Score, filter and rank records, preferring higher score and newer papers."""
    scored = [score_record(record) for record in records]
    filtered = [x for x in scored if x["relevance_score"] >= minimum_score]

    def sort_key(record: Dict):
        # ISO-like dates sort correctly; missing dates are pushed to the end.
        date = record.get("publication_date", "") or ""
        return (record["relevance_score"], date)

    filtered.sort(key=sort_key, reverse=True)
    return filtered[:top_n]


def explain(record: Dict) -> str:
    """Create a short human-readable explanation for the ranking."""
    matched = record.get("matched_rules", [])
    labels = ", ".join(x["label"] for x in matched[:8])
    return f"Score {record.get('relevance_score', 0)} ({record.get('relevance_tier', 'unknown')}): {labels}"


if __name__ == "__main__":
    demo = {
        "title": "Lipid droplet metabolism and diabetic retinopathy",
        "abstract": "Diabetic retinopathy endothelial cells show altered PLIN2 and fatty acid metabolism.",
        "mesh_terms": [],
        "publication_types": ["Journal Article"],
    }
    scored = score_record(demo)
    print(explain(scored))
