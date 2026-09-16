"""Robust English->Simplified Chinese translation with mojibake rejection."""
from __future__ import annotations
import html, re, time
import requests
GLOSSARY={"diabetic retinopathy":"糖尿病视网膜病变","diabetic macular edema":"糖尿病黄斑水肿","lipid metabolism":"脂质代谢","lipid homeostasis":"脂质稳态","lipotoxicity":"脂毒性","fatty acid":"脂肪酸","polyunsaturated fatty acid":"多不饱和脂肪酸","phospholipid":"磷脂","sphingolipid":"鞘脂","ceramide":"神经酰胺","cholesterol":"胆固醇","oxysterol":"氧固醇","triglyceride":"甘油三酯","diacylglycerol":"二酰甘油","lipoprotein":"脂蛋白","lipid droplet":"脂滴","lipid peroxidation":"脂质过氧化","ferroptosis":"铁死亡","endothelial cell":"内皮细胞","endothelial cells":"内皮细胞","müller cell":"Müller细胞","muller cell":"Müller细胞","retinal pigment epithelium":"视网膜色素上皮","retinal ganglion cell":"视网膜神经节细胞","blood-retinal barrier":"血视网膜屏障","retinal neurovascular unit":"视网膜神经血管单元","mitochondrial":"线粒体","inflammation":"炎症","oxidative stress":"氧化应激","diabetes mellitus":"糖尿病","metabolic stress":"代谢应激","vascular permeability":"血管通透性","neovascularization":"新生血管形成","pericyte":"周细胞","permeability":"通透性","apoptosis":"细胞凋亡","autophagy":"自噬","macrophage":"巨噬细胞","insulin resistance":"胰岛素抵抗","free fatty acid":"游离脂肪酸","fatty acid oxidation":"脂肪酸氧化","sustained release":"持续释药","intravitreal":"玻璃体腔内","lipid reprogramming":"脂质代谢重编程"}
def _clean(x): return re.sub(r"\s+"," ",html.unescape(str(x or ""))).strip()
def _has_zh(x): return bool(re.search(r"[\u4e00-\u9fff]",x or ""))
def _mojibake(x): return "�" in x or any(t in x for t in ("Ã","Â","â€","ðŸ","æµ","ç”","ï¿½"))
def _quality(source,candidate):
 c=_clean(candidate)
 if not c or c==_clean(source) or _mojibake(c) or not _has_zh(c): return False
 zh=len(re.findall(r"[\u4e00-\u9fff]",c)); latin=len(re.findall(r"[A-Za-z]",c)); return zh>=max(3,min(20,len(c)//40)) and zh>=max(3,latin//3)
def _google_json(chunk,target):
 r=requests.get("https://translate.googleapis.com/translate_a/single",params={"client":"gtx","sl":"en","tl":target,"dt":"t","q":chunk},timeout=30,headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status(); data=r.json(); return "".join(p[0] for p in (data[0] or []) if p and p[0])
def _google_mobile(chunk,target):
 r=requests.get("https://translate.google.com/m",params={"sl":"en","tl":target,"q":chunk},timeout=30,headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status(); m=re.search(r'<div[^>]+class=["\']result-container["\'][^>]*>(.*?)</div>',r.text,re.I|re.S); return html.unescape(re.sub(r"<[^>]+>"," ",m.group(1))) if m else None
def _chunks(text,limit=1700):
 parts=re.split(r"(?<=[.!?;])\s+",text); out=[]; cur=""
 for s in parts:
  if cur and len(cur)+len(s)+1>limit: out.append(cur); cur=s
  else: cur=(cur+" "+s).strip()
 if cur: out.append(cur)
 return out
def _postprocess(x):
 x=_clean(x)
 for en,zh in sorted(GLOSSARY.items(),key=lambda kv:-len(kv[0])): x=re.sub(re.escape(en),zh,x,flags=re.I)
 return _clean(x)
def translate_text(text,target="zh-CN",retries=3):
 text=_clean(text)
 if not text:return ""
 results=[]
 for chunk in _chunks(text):
  result=None
  for attempt in range(retries):
   for method in (_google_json,_google_mobile):
    try:
     candidate=method(chunk,target)
     if candidate and _quality(chunk,candidate): result=candidate; break
    except Exception: pass
   if result: break
   if attempt<retries-1: time.sleep(0.8*(attempt+1))
  if result is None:return ""
  results.append(result); time.sleep(0.08)
 out=_postprocess(" ".join(results)); return out if _quality(text,out) else ""
def fallback_translation(text): return _postprocess(text)
def zh(text):
 original=_clean(text)
 if not original:return ""
 translated=translate_text(original)
 if translated:return translated
 glossary=fallback_translation(original)
 if glossary!=original and _has_zh(glossary): return "【自动翻译服务暂不可用；以下为术语增强原文】"+glossary
 return "【自动翻译服务暂不可用，请查看英文原文】"+original
