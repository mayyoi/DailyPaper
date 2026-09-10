"""Enrich selected papers with author affiliations and corresponding-author data.

Uses PubMed XML for author affiliations and, when available, PMC full-text XML for
an explicitly marked corresponding author. The code never guesses a corresponding
author from author order.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List

import requests

PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _name_from_author(node: ET.Element) -> str:
    collective = clean(node.findtext("CollectiveName"))
    if collective:
        return collective
    fore = clean(node.findtext("ForeName"))
    last = clean(node.findtext("LastName"))
    initials = clean(node.findtext("Initials"))
    return " ".join(x for x in [fore, last] if x) or (f"{last} {initials}".strip())


def _affiliations(node: ET.Element) -> List[str]:
    values = []
    for aff in node.findall("./AffiliationInfo/Affiliation"):
        value = clean("".join(aff.itertext()))
        if value and value not in values:
            values.append(value)
    return values


def _parse_pubmed_article(article: ET.Element) -> Dict:
    pmid = clean(article.findtext(".//PMID"))
    pmcid = ""
    for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if aid.attrib.get("IdType") == "pmc":
            pmcid = clean(aid.text)
            break
    authors = []
    for author in article.findall(".//AuthorList/Author"):
        name = _name_from_author(author)
        if name:
            authors.append({"name": name, "affiliations": _affiliations(author)})
    journal = clean(article.findtext(".//Journal/Title"))
    return {"pmid": pmid, "pmcid": pmcid, "authors": authors, "journal": journal}


def fetch_pubmed_metadata(pmids: List[str]) -> Dict[str, Dict]:
    ids = [clean(x) for x in pmids if clean(x)]
    if not ids:
        return {}
    response = requests.get(PUBMED_EFETCH, params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}, timeout=60)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    return {item["pmid"]: item for article in root.findall(".//PubmedArticle") if (item := _parse_pubmed_article(article)).get("pmid")}


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1]


def _fulltext_author_name(contrib: ET.Element) -> str:
    surname = clean("".join(contrib.findall(".//surname")[0].itertext())) if contrib.findall(".//surname") else ""
    given = clean("".join(contrib.findall(".//given-names")[0].itertext())) if contrib.findall(".//given-names") else ""
    return " ".join(x for x in [given, surname] if x)


def fetch_corresponding_from_pmc(pmcid: str) -> Dict:
    pmcid = clean(pmcid)
    if not pmcid:
        return {}
    url = PMC_FULLTEXT.format(pmcid=pmcid)
    try:
        response = requests.get(url, timeout=45, headers={"User-Agent": "mayyoi/DailyPaper DR lipid weekly brief"})
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception:
        return {}

    # Preferred: JATS contrib marked as corresponding.
    for contrib in root.iter():
        if _strip_ns(contrib.tag) != "contrib":
            continue
        if str(contrib.attrib.get("corresp", "")).lower() not in {"yes", "true", "1"}:
            continue
        name = _fulltext_author_name(contrib)
        email = ""
        for node in contrib.iter():
            if _strip_ns(node.tag) == "email":
                email = clean("".join(node.itertext()))
                break
        affs = []
        for node in contrib.iter():
            if _strip_ns(node.tag) in {"aff", "address"}:
                value = clean("".join(node.itertext()))
                if value and value not in affs:
                    affs.append(value)
        if name or email or affs:
            return {"name": name, "email": email, "affiliations": affs, "source": "PMC full text"}

    # Fallback: explicit <corresp> blocks often contain the corresponding author's name.
    for node in root.iter():
        if _strip_ns(node.tag) != "corresp":
            continue
        text = clean("".join(node.itertext()))
        if not text:
            continue
        email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
        email = email_match.group(0) if email_match else ""
        return {"name": "", "email": email, "affiliations": [text], "source": "PMC full text (corresp block)"}
    return {}


def enrich_selected_records(records: List[Dict], pause_seconds: float = 0.15) -> List[Dict]:
    pmids = [r.get("pmid", "") for r in records if r.get("pmid")]
    metadata = {}
    if pmids:
        try:
            metadata = fetch_pubmed_metadata(pmids)
        except Exception as exc:
            print(f"[metadata] PubMed affiliation enrichment failed: {exc}")

    for record in records:
        meta = metadata.get(record.get("pmid", ""), {})
        if meta.get("authors"):
            record["authors"] = meta["authors"]
        if meta.get("pmcid"):
            record["pmcid"] = meta["pmcid"]
        if meta.get("journal") and not record.get("journal"):
            record["journal"] = meta["journal"]
        if record.get("authors"):
            first = record["authors"][0]
            record["first_author"] = first.get("name", "") if isinstance(first, dict) else str(first)
            record["first_author_affiliations"] = first.get("affiliations", []) if isinstance(first, dict) else []
        if record.get("pmcid"):
            corr = fetch_corresponding_from_pmc(record["pmcid"])
            if corr:
                record["corresponding_author"] = corr.get("name", "")
                record["corresponding_author_email"] = corr.get("email", "")
                record["corresponding_author_affiliations"] = corr.get("affiliations", [])
                record["corresponding_author_source"] = corr.get("source", "")
        time.sleep(pause_seconds)

        if not record.get("first_author") and record.get("authors"):
            first = record["authors"][0]
            record["first_author"] = first.get("name", "") if isinstance(first, dict) else str(first)
        record.setdefault("first_author_affiliations", [])
        record.setdefault("corresponding_author", "")
        record.setdefault("corresponding_author_email", "")
        record.setdefault("corresponding_author_affiliations", [])
        record.setdefault("corresponding_author_source", "")
    return records
