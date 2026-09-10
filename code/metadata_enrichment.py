"""Enrich selected papers with author affiliations and corresponding-author data.

Uses PubMed XML first, then Europe PMC by DOI for papers that came from Crossref/OpenAlex.
When PMC full text is available, it is used to locate an explicitly marked corresponding
author. The code never guesses a corresponding author from author order.
"""
from __future__ import annotations
import re, time, xml.etree.ElementTree as ET
from typing import Dict, List
import requests

PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()

def _name_from_author(node: ET.Element) -> str:
    collective = clean(node.findtext("CollectiveName"))
    if collective: return collective
    fore, last, initials = clean(node.findtext("ForeName")), clean(node.findtext("LastName")), clean(node.findtext("Initials"))
    return " ".join(x for x in [fore, last] if x) or f"{last} {initials}".strip()

def _affiliations(node: ET.Element) -> List[str]:
    values=[]
    for aff in node.findall("./AffiliationInfo/Affiliation"):
        value=clean("".join(aff.itertext()))
        if value and value not in values: values.append(value)
    return values

def _parse_pubmed_article(article: ET.Element) -> Dict:
    pmid=clean(article.findtext(".//PMID")); pmcid=""
    for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if aid.attrib.get("IdType")=="pmc": pmcid=clean(aid.text); break
    authors=[]
    for author in article.findall(".//AuthorList/Author"):
        name=_name_from_author(author)
        if name: authors.append({"name":name,"affiliations":_affiliations(author)})
    return {"pmid":pmid,"pmcid":pmcid,"authors":authors,"journal":clean(article.findtext(".//Journal/Title"))}

def fetch_pubmed_metadata(pmids: List[str]) -> Dict[str, Dict]:
    ids=[clean(x) for x in pmids if clean(x)]
    if not ids: return {}
    response=requests.get(PUBMED_EFETCH,params={"db":"pubmed","id":",".join(ids),"retmode":"xml"},timeout=60); response.raise_for_status()
    root=ET.fromstring(response.text)
    return {item["pmid"]:item for article in root.findall(".//PubmedArticle") if (item:=_parse_pubmed_article(article)).get("pmid")}

def fetch_europepmc_by_doi(doi: str) -> Dict:
    doi=clean(doi)
    if not doi: return {}
    try:
        r=requests.get(EUROPEPMC_SEARCH,params={"query":f'DOI:"{doi}"',"format":"json","resultType":"core","pageSize":1},timeout=30)
        r.raise_for_status(); results=r.json().get("resultList",{}).get("result",[])
        if not results: return {}
        item=results[0]; authors=[]
        for a in (item.get("authorList",{}).get("author",[]) or []):
            name=clean(a.get("fullName")) or " ".join(x for x in [clean(a.get("firstName")),clean(a.get("lastName"))] if x)
            affs=[]
            for detail in (a.get("authorAffiliationDetailsList",{}).get("authorAffiliation",[]) or []):
                value=clean(detail.get("affiliation")) if isinstance(detail,dict) else clean(detail)
                if value and value not in affs: affs.append(value)
            if name: authors.append({"name":name,"affiliations":affs})
        return {"pmid":clean(item.get("pmid")),"pmcid":clean(item.get("pmcid")),"authors":authors,"journal":clean(item.get("journalTitle"))}
    except Exception:
        return {}

def _strip_ns(tag: str) -> str: return tag.split("}")[-1]

def _fulltext_author_name(contrib: ET.Element) -> str:
    surnames=[n for n in contrib.iter() if _strip_ns(n.tag)=="surname"]; givens=[n for n in contrib.iter() if _strip_ns(n.tag)=="given-names"]
    surname=clean("".join(surnames[0].itertext())) if surnames else ""; given=clean("".join(givens[0].itertext())) if givens else ""
    return " ".join(x for x in [given,surname] if x)

def fetch_corresponding_from_pmc(pmcid: str) -> Dict:
    pmcid=clean(pmcid)
    if not pmcid: return {}
    try:
        response=requests.get(PMC_FULLTEXT.format(pmcid=pmcid),timeout=30,headers={"User-Agent":"mayyoi/DailyPaper DR lipid weekly brief"}); response.raise_for_status(); root=ET.fromstring(response.text)
    except Exception: return {}
    for contrib in root.iter():
        if _strip_ns(contrib.tag)!="contrib" or str(contrib.attrib.get("corresp","")).lower() not in {"yes","true","1"}: continue
        name=_fulltext_author_name(contrib); email=""; affs=[]
        for node in contrib.iter():
            tag=_strip_ns(node.tag)
            if tag=="email" and not email: email=clean("".join(node.itertext()))
            if tag in {"aff","address"}:
                value=clean("".join(node.itertext()))
                if value and value not in affs: affs.append(value)
        if name or email or affs: return {"name":name,"email":email,"affiliations":affs,"source":"PMC full text"}
    for node in root.iter():
        if _strip_ns(node.tag)!="corresp": continue
        text=clean("".join(node.itertext()))
        if text:
            m=re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",text,re.I)
            return {"name":"","email":m.group(0) if m else "","affiliations":[text],"source":"PMC full text (corresp block)"}
    return {}

def _apply_meta(record: Dict, meta: Dict) -> None:
    if not meta: return
    if meta.get("pmid") and not record.get("pmid"): record["pmid"]=meta["pmid"]
    if meta.get("pmcid"): record["pmcid"]=meta["pmcid"]
    if meta.get("authors"): record["authors"]=meta["authors"]
    if meta.get("journal") and not record.get("journal"): record["journal"]=meta["journal"]

def enrich_selected_records(records: List[Dict], pause_seconds: float=0.08) -> List[Dict]:
    pmids=[r.get("pmid","") for r in records if r.get("pmid")]; metadata={}
    if pmids:
        try: metadata=fetch_pubmed_metadata(pmids)
        except Exception as exc: print(f"[metadata] PubMed affiliation enrichment failed: {exc}")
    for record in records: _apply_meta(record,metadata.get(record.get("pmid",""),{}))
    for record in records:
        if not record.get("first_author_affiliations") and record.get("doi"):
            _apply_meta(record,fetch_europepmc_by_doi(record["doi"])); time.sleep(pause_seconds)
    for record in records:
        if record.get("authors"):
            first=record["authors"][0]; record["first_author"]=first.get("name","") if isinstance(first,dict) else str(first); record["first_author_affiliations"]=first.get("affiliations",[]) if isinstance(first,dict) else []
        else: record["first_author"]=""; record["first_author_affiliations"]=[]
        if record.get("pmcid"):
            corr=fetch_corresponding_from_pmc(record["pmcid"])
            if corr:
                record["corresponding_author"]=corr.get("name",""); record["corresponding_author_email"]=corr.get("email",""); record["corresponding_author_affiliations"]=corr.get("affiliations",[]); record["corresponding_author_source"]=corr.get("source","")
        record.setdefault("corresponding_author",""); record.setdefault("corresponding_author_email",""); record.setdefault("corresponding_author_affiliations",[]); record.setdefault("corresponding_author_source","")
    return records
