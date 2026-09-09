"""Biomedical literature retrieval for the DR x lipid metabolism project.

This module deliberately contains no API keys. PubMed E-utilities and Europe
PMC are public APIs; optional NCBI credentials can be added later through
GitHub Actions secrets without changing this code.
"""

from __future__ import annotations

import re
import time
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlencode

import requests

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

DEFAULT_QUERIES = [
    '("diabetic retinopathy" OR "diabetic retinal disease" OR "diabetic macular edema" OR NPDR OR PDR)',
    '(("diabetic retinopathy" OR "diabetic retinal disease") AND ("lipid metabolism" OR "lipid homeostasis" OR "fatty acid metabolism" OR lipotoxicity))',
    '(("diabetic retinopathy" OR "diabetic retinal disease") AND (ceramide OR sphingolipid OR diacylglycerol OR cholesterol OR triglyceride OR phospholipid))',
    '(("diabetic retinopathy" OR retina OR retinal) AND ("lipid droplet" OR perilipin OR PLIN1 OR PLIN2 OR PLIN3 OR PLIN4 OR PLIN5 OR CIDEC OR DGAT1 OR DGAT2 OR PNPLA2))',
    '(("diabetic retinopathy" OR retina OR retinal) AND (LXR OR NR1H3 OR NR1H2 OR PPAR OR SREBP OR FASN OR CPT1 OR ACSL))',
    '(("diabetic retinopathy" OR retina OR retinal) AND (endothelial OR "Muller cell" OR "Müller cell" OR RPE OR "retinal ganglion" OR "blood-retinal barrier" OR RNVU))',
]


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _request_json(url: str, params: Dict, timeout: int = 30) -> Dict:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _normalise_record(record: Dict, source: str) -> Dict:
    """Convert PubMed/Europe PMC metadata to one common schema."""
    doi = _clean(record.get("doi"))
    pmid = _clean(record.get("pmid"))
    title = _clean(record.get("title"))
    abstract = _clean(record.get("abstract"))
    authors = record.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]

    return {
        "source": source,
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "journal": _clean(record.get("journal")),
        "publication_date": _clean(record.get("publication_date")),
        "publication_types": record.get("publication_types") or [],
        "mesh_terms": record.get("mesh_terms") or [],
        "url": _clean(record.get("url")),
        "query": _clean(record.get("query")),
    }


def search_pubmed(
    query: str,
    retmax: int = 100,
    days: Optional[int] = None,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
) -> List[Dict]:
    """Search PubMed with ESearch + EFetch and return normalized records."""
    term = query
    if days is not None and days > 0:
        term = f"({term}) AND (\"{days} days\"[PDat])"

    search_params = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": retmax,
        "sort": "pub date",
    }
    if email:
        search_params["email"] = email
    if api_key:
        search_params["api_key"] = api_key

    data = _request_json(f"{PUBMED_BASE}/esearch.fcgi", search_params)
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
    }
    if email:
        fetch_params["email"] = email
    if api_key:
        fetch_params["api_key"] = api_key

    response = requests.get(f"{PUBMED_BASE}/efetch.fcgi", params=fetch_params, timeout=60)
    response.raise_for_status()
    return _parse_pubmed_xml(response.text, query)


def _parse_pubmed_xml(xml_text: str, query: str) -> List[Dict]:
    """Parse the subset of PubMed XML needed by the ranking pipeline."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    records = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _clean(article.findtext(".//PMID"))
        title = _clean("".join(article.find(".//ArticleTitle").itertext())) if article.find(".//ArticleTitle") is not None else ""
        abstract_parts = []
        for node in article.findall(".//Abstract/AbstractText"):
            label = node.attrib.get("Label", "")
            text = _clean("".join(node.itertext()))
            abstract_parts.append(f"{label}: {text}" if label else text)

        authors = []
        for author in article.findall(".//AuthorList/Author"):
            collective = _clean(author.findtext("CollectiveName"))
            if collective:
                authors.append(collective)
                continue
            last = _clean(author.findtext("LastName"))
            initials = _clean(author.findtext("Initials"))
            name = f"{last} {initials}".strip()
            if name:
                authors.append(name)

        doi = ""
        for aid in article.findall(".//ArticleId"):
            if aid.attrib.get("IdType") == "doi":
                doi = _clean(aid.text)
                break

        journal = _clean(article.findtext(".//Journal/Title"))
        year = _clean(article.findtext(".//PubDate/Year"))
        month = _clean(article.findtext(".//PubDate/Month"))
        publication_date = " ".join(x for x in [year, month] if x)
        pub_types = [_clean(x.text) for x in article.findall(".//PublicationType") if _clean(x.text)]
        mesh_terms = [_clean(x.findtext("DescriptorName")) for x in article.findall(".//MeshHeading") if _clean(x.findtext("DescriptorName"))]

        records.append(_normalise_record({
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "abstract": " ".join(abstract_parts),
            "authors": authors,
            "journal": journal,
            "publication_date": publication_date,
            "publication_types": pub_types,
            "mesh_terms": mesh_terms,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            "query": query,
        }, "PubMed"))
    return records


def search_europe_pmc(
    query: str,
    page_size: int = 100,
    days: Optional[int] = None,
) -> List[Dict]:
    """Search Europe PMC and return normalized records."""
    epmc_query = query
    if days is not None and days > 0:
        epmc_query = f"({epmc_query}) AND FIRST_PDATE:[NOW-{days}D TO NOW]"

    params = {
        "query": epmc_query,
        "format": "json",
        "resultType": "core",
        "pageSize": page_size,
        "sort": "FIRST_PDATE_D desc",
    }
    data = _request_json(f"{EUROPEPMC_BASE}/search", params, timeout=60)
    results = data.get("resultList", {}).get("result", [])

    records = []
    for item in results:
        author_list = item.get("authorList", {}).get("author", []) or []
        authors = []
        for author in author_list:
            name = _clean(author.get("fullName")) or _clean(author.get("lastName"))
            if name:
                authors.append(name)

        pub_types = item.get("pubTypeList", {}).get("pubType", []) or []
        if isinstance(pub_types, str):
            pub_types = [pub_types]

        records.append(_normalise_record({
            "pmid": item.get("pmid"),
            "doi": item.get("doi"),
            "title": item.get("title"),
            "abstract": item.get("abstractText"),
            "authors": authors,
            "journal": item.get("journalTitle"),
            "publication_date": item.get("firstPublicationDate") or item.get("pubYear"),
            "publication_types": pub_types,
            "mesh_terms": item.get("meshHeadingList", {}).get("meshHeading", []) or [],
            "url": f"https://europepmc.org/article/MED/{item.get('pmid')}" if item.get("pmid") else "",
            "query": query,
        }, "Europe PMC"))
    return records


def deduplicate(records: Iterable[Dict]) -> List[Dict]:
    """Deduplicate primarily by PMID, then DOI, then normalized title."""
    seen = set()
    output = []
    for record in records:
        title_key = re.sub(r"[^a-z0-9]+", "", record.get("title", "").lower())
        keys = [
            f"pmid:{record.get('pmid')}" if record.get("pmid") else "",
            f"doi:{record.get('doi').lower()}" if record.get("doi") else "",
            f"title:{title_key}" if title_key else "",
        ]
        key = next((x for x in keys if x), None)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        output.append(record)
    return output


def search_all(
    queries: Optional[List[str]] = None,
    per_query: int = 100,
    days: Optional[int] = 30,
    email: Optional[str] = None,
    api_key: Optional[str] = None,
    pause_seconds: float = 0.25,
) -> List[Dict]:
    """Search both sources for all configured queries and deduplicate."""
    queries = queries or DEFAULT_QUERIES
    all_records = []
    for query in queries:
        try:
            all_records.extend(search_pubmed(query, retmax=per_query, days=days, email=email, api_key=api_key))
        except requests.RequestException as exc:
            print(f"[PubMed] query failed: {exc}")
        time.sleep(pause_seconds)
        try:
            all_records.extend(search_europe_pmc(query, page_size=per_query, days=days))
        except requests.RequestException as exc:
            print(f"[Europe PMC] query failed: {exc}")
        time.sleep(pause_seconds)
    return deduplicate(all_records)


if __name__ == "__main__":
    records = search_all(per_query=10, days=30)
    print(f"Retrieved {len(records)} unique records")
    for record in records[:10]:
        print(record["publication_date"], record["title"], record["pmid"])
