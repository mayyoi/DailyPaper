from __future__ import annotations
import html,re
import requests
GLOSSARY={"diabetic retinopathy":"糖尿病视网膜病变","diabetic macular edema":"糖尿病黄斑水肿","lipid metabolism":"脂质代谢","lipid homeostasis":"脂质稳态","lipotoxicity":"脂毒性","fatty acid":"脂肪酸","phospholipid":"磷脂","sphingolipid":"鞘脂","ceramide":"神经酰胺","cholesterol":"胆固醇","triglyceride":"甘油三酯","lipoprotein":"脂蛋白","lipid droplet":"脂滴","lipid peroxidation":"脂质过氧化","ferroptosis":"铁死亡","endothelial cell":"内皮细胞","endothelial cells":"内皮细胞","müller cell":"Müller细胞","muller cell":"Müller细胞","retinal pigment epithelium":"视网膜色素上皮","blood-retinal barrier":"血视网膜屏障","mitochondrial":"线粒体","inflammation":"炎症","oxidative stress":"氧化应激","neovascularization":"新生血管形成","permeability":"通透性","apoptosis":"细胞凋亡","macrophage":"巨噬细胞","fatty acid oxidation":"脂肪酸氧化","intravitreal":"玻璃体腔内","sustained release":"持续释药"}
def clean(x):return re.sub(r"\s+"," ",html.unescape(str(x or ""))).strip()
def bad(x):return '�' in x or any(t in x for t in ('Ã','Â','â€','ðŸ','æµ','ç”','ï¿½'))
def quality(src,x):
 x=clean(x)
 if not x or x==clean(src) or bad(x) or not re.search(r'[\u4e00-\u9fff]',x):return False
 zh=len(re.findall(r'[\u4e00-\u9fff]',x));latin=len(re.findall(r'[A-Za-z]',x));return zh>=3 and zh>=max(3,latin//3)
def chunks(text,n=1800):
 parts=re.split(r'(?<=[.!?;])\s+',clean(text));out=[];cur=''
 for s in parts:
  if cur and len(cur)+len(s)+1>n:out.append(cur);cur=s
  else:cur=(cur+' '+s).strip()
 if cur:out.append(cur)
 return out
def translate(text):
 text=clean(text)
 if not text:return ''
 out=[]
 for c in chunks(text):
  try:
   r=requests.get('https://translate.googleapis.com/translate_a/single',params={'client':'gtx','sl':'en','tl':'zh-CN','dt':'t','q':c},headers={'User-Agent':'Mozilla/5.0'},timeout=10);r.raise_for_status();d=r.json();val=''.join(p[0] for p in (d[0] or []) if p and p[0])
  except Exception:return ''
  if not val or not quality(c,val):return ''
  out.append(val)
 x=clean(' '.join(out))
 for en,zh in sorted(GLOSSARY.items(),key=lambda kv:-len(kv[0])):x=re.sub(re.escape(en),zh,x,flags=re.I)
 return x if quality(text,x) else ''
def zh(text):
 o=clean(text);t=translate(o)
 if t:return t
 g=o
 for en,z in sorted(GLOSSARY.items(),key=lambda kv:-len(kv[0])):g=re.sub(re.escape(en),z,g,flags=re.I)
 if g!=o and re.search(r'[\u4e00-\u9fff]',g):return '【自动翻译服务暂不可用；以下为术语增强原文】'+g
 return '【自动翻译服务暂不可用，请查看英文原文】'+o
