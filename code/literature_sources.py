"""Biomedical literature retrieval for the DR x lipid metabolism project.

Primary biomedical sources:
- PubMed / NCBI E-utilities
- Europe PMC
- OpenAlex
- Crossref

Google Scholar is handled as a supplementary discovery link rather than by
scraping. Google Scholar does not provide a stable public search API, and
scraping it is brittle and can trigger anti-bot controls. The weekly brief
therefore includes a ready-to-click Scholar query for manual cross-checking.
No API keys are hardcoded in this module.
"""

from __future__ import annotations

import re
import time
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote_plus

import requests

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX_BASE = "https://api.openalex.org"
CROSSREF_BASE = "https://api.crossref.org"

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
    return re.sub(r"\s+", " ", str(text)).strip()


def _request_json(url: str, params: Dict, timeout: int = 30, headers: Optional[Dict] = None) -> Dict:
    response = requests.get(url, params=params, timeout=timeout, headers=headers or {})
    response.raise_for_status()
    return response.json()


def _normalise_record(record: Dict, source: str) -> Dict:
    authors = record.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    return {
        "source": source,
        "pmid": _clean(record.get("pmid")),
        "doi": _clean(record.get("doi")),
        "title": _clean(record.get("title")),
        "abstract": _clean(record.get("abstract")),
        "authors": authors,
        "journal": _clean(record.get("journal")),
        "publication_date": _clean(record.get("publication_date")),
        "publication_types": record.get("publication_types") or [],
        "mesh_terms": record.get("mesh_terms") or [],
        "url": _clean(record.get("url")),
        "query": _clean(record.get("query")),
    }


def search_pubmed(query: str, retmax: int = 100, days: Optional[int] = None, email: Optional[str] = None, api_key: Optional[str] = None) -> List[Dict]:
    term = query
    if days is not None and days > 0:
        term = f"({term}) AND (\"{days} days\"[PDat])"
    params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax, "sort": "pub date"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    data = _request_json(f"{PUBMED_BASE}/esearch.fcgi", params)
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    fetch_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
    if email:
        fetch_params["email"] = email
    if api_key:
        fetch_params["api_key"] = api_key
    response = requests.get(f"{PUBMED_BASE}/efetch.fcgi", params=fetch_params, timeout=60)
    response.raise_for_status()
    return _parse_pubmed_xml(response.text, query)


def _parse_pubmed_xml(xml_text: str, query: str) -> List[Dict]:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    records = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _clean(article.findtext(".//PMID"))
        title_node = article.find(".//ArticleTitle")
        title = _clean("".join(title_node.itertext())) if title_node is not None else ""
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
            "pmid": pmid, "doi": doi, "title": title, "abstract": " ".join(abstract_parts),
            "authors": authors, "journal": journal, "publication_date": publication_date,
            "publication_types": pub_types, "mesh_terms": mesh_terms,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "", "query": query,
        }, "PubMed"))
    return records


def search_europe_pmc(query: str, page_size: int = 100, days: Optional[int] = None) -> List[Dict]:
    epmc_query = query
    if days is not None and days > 0:
        epmc_query = f"({epmc_query}) AND FIRST_PDATE:[NOW-{days}D TO NOW]"
    params = {"query": epmc_query, "format": "json", "resultType": "core", "pageSize": page_size, "sort": "FIRST_PDATE_D desc"}
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
            "pmid": item.get("pmid"), "doi": item.get("doi"), "title": item.get("title"),
            "abstract": item.get("abstractText"), "authors": authors, "journal": item.get("journalTitle"),
            "publication_date": item.get("firstPublicationDate") or item.get("pubYear"),
            "publication_types": pub_types, "mesh_terms": item.get("meshHeadingList", {}).get("meshHeading", []) or [],
            "url": f"https://europepmc.org/article/MED/{item.get('pmid')}" if item.get("pmid") else "", "query": query,
        }, "Europe PMC"))
    return records


def _openalex_abstract(item: Dict) -> str:
    inverted = item.get("abstract_inverted_index") or {}
    if not inverted:
        return ""
    words = []
    for word, positions in inverted.items():
        for pos in positions:
            words.append((pos, word))
    words.sort(key=lambda x: x[0])
    return " ".join(word for _, word in words)


def search_openalex(query: str, per_page: int = 100, days: Optional[int] = None) -> List[Dict]:
    params = {"search": query, "per-page": per_page, "sort": "publication_date:desc"}
    if days is not None and days > 0:
        from datetime import date, timedelta
        start = date.today() - timedelta(days=days)
        params["filter"] = f"from_publication_date:{start.isoformat()},to_publication_date:{date.today().isoformat()}"
    data = _request_json(f"{OPENALEX_BASE}/works", params, timeout=60, headers={"User-Agent": "mayyoi/DailyPaper DR lipid literature radar"})
    records = []
    for item in data.get("results", []):
        authors = []
        for authorship in item.get("authorships", []) or []:
            name = _clean((authorship.get("author") or {}).get("display_name"))
            if name:
                authors.append(name)
        primary = item.get("primary_location") or {}
        source = primary.get("source") or {}
        doi = _clean(item.get("doi"))
        records.append(_normalise_record({
            "doi": doi.replace("https://doi.org/", ""),
            "title": item.get("display_name") or item.get("title"),
            "abstract": _openalex_abstract(item),
            "authors": authors,
            "journal": source.get("display_name"),
            "publication_date": item.get("publication_date"),
            "publication_types": [item.get("type")] if item.get("type") else [],
            "mesh_terms": [],
            "url": item.get("doi") or item.get("id") or "",
            "query": query,
        }, "OpenAlex"))
    return records


def _strip_jats(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean(text)


def search_crossref(query: str, rows: int = 100, days: Optional[int] = None) -> List[Dict]:
    params = {"query.bibliographic": query, "rows": rows, "sort": "published", "order": "desc"}
    if days is not None and days > 0:
        from datetime import date, timedelta
        start = date.today() - timedelta(days=days)
        params["filter"] = f"from-pub-date:{start.isoformat()},until-pub-date:{date.today().isoformat()}"
    data = _request_json(f"{CROSSREF_BASE}/works", params, timeout=60, headers={"User-Agent": "mayyoi/DailyPaper/1.0 (mailto:171142515@qq.com)"})
    records = []
    for item in data.get("message", {}).get("items", []):
        title = (item.get("title") or [""])[0]
        authors = []
        for author in item.get("author", []) or []:
            name = " ".join(x for x in [_clean(author.get("given")), _clean(author.get("family"))] if x)
            if name:
                authors.append(name)
        published = item.get("published-print") or item.get("published-online") or item.get("issued") or {}
        parts = published.get("date-parts") or [[]]
        publication_date = "-".join(str(x) for x in parts[0] if x) if parts and parts[0] else ""
        doi = _clean(item.get("DOI"))
        records.append(_normalise_record({
            "doi": doi, "title": title, "abstract": _strip_jats(item.get("abstract", "")),
            "authors": authors, "journal": (item.get("container-title") or [""])[0],
            "publication_date": publication_date,
            "publication_types": [item.get("type")] if item.get("type") else [],
            "mesh_terms": [], "url": f"https://doi.org/{doi}" if doi else item.get("URL", ""), "query": query,
        }, "Crossref"))
    return records


def google_scholar_url(query: Optional[str] = None) -> str:
    """Return a ready-to-use Google Scholar discovery URL; no scraping."""
    query = query or 'diabetic retinopathy lipid metabolism'
    return "https://scholar.google.com/scholar?q=" + quote_plus(query)


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


def search_all(queries: Optional[List[str]] = None, per_query: int = 100, days: Optional[int] = 30, email: Optional[str] = None, api_key: Optional[str] = None, pause_seconds: float = 0.25) -> List[Dict]:
    """Search four machine-readable sources and deduplicate; Scholar is a manual supplement."""
    queries = queries or DEFAULT_QUERIES
    all_records = []
    for query in queries:
        for label, func in [
            ("PubMed", lambda: search_pubmed(query, retmax=per_query, days=days, email=email, api_key=api_key)),
            ("Europe PMC", lambda: search_europe_pmc(query, page_size=per_query, days=days)),
            ("OpenAlex", lambda: search_openalex(query, per_page=per_query, days=days)),
            ("Crossref", lambda: search_crossref(query, rows=per_query, days=days)),
        ]:
            try:
                all_records.extend(func())
            except requests.RequestException as exc:
                print(f"[{label}] query failed: {exc}")
            except Exception as exc:
                print(f"[{label}] unexpected error: {exc}")
            time.sleep(pause_seconds)
    return deduplicate(all_records)


if __name__ == "__main__":
    records = search_all(per_query=10, days=30)
    print(f"Retrieved {len(records)} unique records")
    print("Google Scholar supplement:", google_scholar_url())
    for record in records[:10]:
        print(record["source"], record["publication_date"], record["title"], record["pmid"])
