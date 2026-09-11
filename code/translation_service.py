"""Robust, keyless English->Simplified Chinese translation for the weekly brief.

Uses Google's public translation endpoints as best-effort services, with response
validation. Biomedical glossary terms are protected after translation. If all
network translation attempts fail, the caller receives a clearly marked fallback
rather than silently presenting English as Chinese.
"""
from __future__ import annotations

import html
import re
import time
from typing import Optional
from urllib.parse import quote_plus

import requests

GLOSSARY = {
    "diabetic retinopathy": "糖尿病视网膜病变", "diabetic macular edema": "糖尿病黄斑水肿",
    "lipid metabolism": "脂质代谢", "lipid homeostasis": "脂质稳态", "lipotoxicity": "脂毒性",
    "fatty acid": "脂肪酸", "polyunsaturated fatty acid": "多不饱和脂肪酸", "phospholipid": "磷脂",
    "sphingolipid": "鞘脂", "ceramide": "神经酰胺", "cholesterol": "胆固醇", "oxysterol": "氧固醇",
    "triglyceride": "甘油三酯", "diacylglycerol": "二酰甘油", "lipoprotein": "脂蛋白",
    "lipid droplet": "脂滴", "lipid peroxidation": "脂质过氧化", "ferroptosis": "铁死亡",
    "endothelial cell": "内皮细胞", "endothelial cells": "内皮细胞", "müller cell": "Müller细胞", "muller cell": "Müller细胞",
    "retinal pigment epithelium": "视网膜色素上皮", "retinal ganglion cell": "视网膜神经节细胞",
    "blood-retinal barrier": "血视网膜屏障", "retinal neurovascular unit": "视网膜神经血管单元",
    "mitochondrial": "线粒体", "inflammation": "炎症", "oxidative stress": "氧化应激",
    "diabetes mellitus": "糖尿病", "metabolic stress": "代谢应激", "vascular permeability": "血管通透性",
    "neovascularization": "新生血管形成", "retinal vascular": "视网膜血管", "pericyte": "周细胞",
    "permeability": "通透性", "apoptosis": "细胞凋亡", "autophagy": "自噬", "macrophage": "巨噬细胞",
    "insulin resistance": "胰岛素抵抗", "free fatty acid": "游离脂肪酸", "fatty acid oxidation": "脂肪酸氧化",
}


def _clean(x: str) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _has_chinese(x: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", x or ""))


def _quality_ok(source: str, translated: str) -> bool:
    translated = _clean(translated)
    if not translated or translated == _clean(source):
        return False
    zh = len(re.findall(r"[\u4e00-\u9fff]", translated))
    latin = len(re.findall(r"[A-Za-z]", translated))
    # A successful biomedical translation should contain Chinese and should not be
    # overwhelmingly untranslated English.
    return zh >= max(2, min(20, len(translated) // 40)) and zh >= max(3, latin // 3)


def _google_json(chunk: str, target: str) -> Optional[str]:
    url = "https://translate.googleapis.com/translate_a/single"
    r = requests.get(url, params={"client":"gtx","sl":"auto","tl":target,"dt":"t","q":chunk}, timeout=25,
                     headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    return "".join(part[0] for part in (data[0] or []) if part and part[0])


def _google_mobile(chunk: str, target: str) -> Optional[str]:
    url = "https://translate.google.com/m"
    r = requests.get(url, params={"sl":"en","tl":target,"q":chunk}, timeout=25,
                     headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    m = re.search(r'<div[^>]+class=["\']result-container["\'][^>]*>(.*?)</div>', r.text, flags=re.I|re.S)
    if not m:
        return None
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))


def _chunks(text: str, limit: int = 1800):
    sentences = re.split(r"(?<=[.!?;])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > limit:
            chunks.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur: chunks.append(cur)
    return chunks


def _protect_terms(text: str):
    placeholders = {}
    out = text
    for i, (en, zh) in enumerate(sorted(GLOSSARY.items(), key=lambda kv: -len(kv[0]))):
        token = f"ZXBIO{i}Q"
        if re.search(re.escape(en), out, flags=re.I):
            out = re.sub(re.escape(en), token, out, flags=re.I)
            placeholders[token] = zh
    return out, placeholders


def translate_text(text: str, target: str = "zh-CN", retries: int = 2) -> str:
    text = _clean(text)
    if not text:
        return ""
    protected, replacements = _protect_terms(text)
    translated_chunks = []
    for chunk in _chunks(protected):
        result = None
        for attempt in range(retries):
            for method in (_google_json, _google_mobile):
                try:
                    candidate = method(chunk, target)
                    if candidate and _quality_ok(chunk, candidate):
                        result = candidate
                        break
                except Exception:
                    pass
            if result:
                break
            if attempt + 1 < retries:
                time.sleep(0.8 * (attempt + 1))
        if result is None:
            # Keep this sentinel; the final layer will make the failure explicit.
            translated_chunks.append("")
        else:
            translated_chunks.append(result)
    if not translated_chunks or any(not x for x in translated_chunks):
        return ""
    out = _clean(" ".join(translated_chunks))
    for token, zh in replacements.items():
        out = out.replace(token, zh)
    return out if _has_chinese(out) else ""


def fallback_translation(text: str) -> str:
    """Glossary-only fallback; explicitly not presented as a full translation."""
    x = _clean(text)
    for en, zh in sorted(GLOSSARY.items(), key=lambda kv: -len(kv[0])):
        x = re.sub(re.escape(en), zh, x, flags=re.I)
    return x


def zh(text: str) -> str:
    original = _clean(text)
    if not original:
        return ""
    translated = translate_text(original)
    if translated:
        return translated
    # Do not silently label English as Chinese. This branch is mainly for rare
    # API outages and makes the status visible in the generated report.
    glossary = fallback_translation(original)
    if glossary != original and _has_chinese(glossary):
        return "【自动翻译服务暂不可用；以下为术语增强原文】" + glossary
    return "【自动翻译服务暂不可用，请查看英文原文】" + original
