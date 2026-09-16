from __future__ import annotations
import html
import re
import os
import requests
from urllib.parse import quote_plus

GLOSSARY={"diabetic retinopathy":"糖尿病视网膜病变","diabetic macular edema":"糖尿病黄斑水肿","lipid metabolism":"脂质代谢","lipid homeostasis":"脂质稳态","lipotoxicity":"脂毒性","fatty acid":"脂肪酸","phospholipid":"磷脂","sphingolipid":"鞘脂","ceramide":"神经酰胺","cholesterol":"胆固醇","triglyceride":"甘油三酯","lipoprotein":"脂蛋白","lipid droplet":"脂滴","lipid peroxidation":"脂质过氧化","ferroptosis":"铁死亡","endothelial cell":"内皮细胞","endothelial cells":"内皮细胞","müller cell":"Müller细胞","muller cell":"Müller细胞","retinal pigment epithelium":"视网膜色素上皮","blood-retinal barrier":"血视网膜屏障","mitochondrial":"线粒体","inflammation":"炎症","oxidative stress":"氧化应激","neovascularization":"新生血管形成","permeability":"通透性","apoptosis":"细胞凋亡","macrophage":"巨噬细胞","fatty acid oxidation":"脂肪酸氧化","intravitreal":"玻璃体腔内","sustained release":"持续释药","long-acting":"长效","lipid peroxidation":"脂质过氧化","retinal vascular endothelial cell":"视网膜血管内皮细胞"}

def clean(x):
    x=re.sub(r"\s+"," ",html.unescape(str(x or ""))).strip()
    return x

def bad(x):
    return any(t in x for t in ('�','Ã','Â','â€','ðŸ','æµ','ç”','ï¿½'))

def quality(src,x):
    x=clean(x); src=clean(src)
    if not x or bad(x) or not re.search(r'[\u4e00-\u9fff]',x): return False
    if x==src: return False
    zh=len(re.findall(r'[\u4e00-\u9fff]',x)); latin=len(re.findall(r'[A-Za-z]',x))
    # Scientific Chinese naturally retains gene/protein abbreviations and journal terminology.
    return zh>=12 and zh>=max(8,latin//5)

def chunks(text,n=1500):
    text=clean(text)
    parts=re.split(r'(?<=[.!?;])\s+',text)
    out=[]; cur=''
    for s in parts:
        if cur and len(cur)+len(s)+1>n:
            out.append(cur); cur=s
        else: cur=(cur+' '+s).strip()
    if cur: out.append(cur)
    return out

def apply_glossary(x):
    for en,zh in sorted(GLOSSARY.items(),key=lambda kv:-len(kv[0])):
        x=re.sub(re.escape(en),zh,x,flags=re.I)
    return x

def google_json(url,params,headers):
    r=requests.get(url,params=params,headers=headers,timeout=12)
    r.raise_for_status(); d=r.json()
    if isinstance(d,list) and d and isinstance(d[0],list):
        return ''.join(p[0] for p in d[0] if isinstance(p,list) and p and p[0])
    if isinstance(d,dict) and d.get('sentences'):
        return ''.join(s.get('trans','') for s in d['sentences'])
    return ''

def google_translate(c):
    headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36','Referer':'https://translate.google.com/'}
    endpoints=[
        ('google-web','https://translate.google.com/translate_a/single',{'client':'gtx','sl':'en','tl':'zh-CN','dt':'t','q':c}),
        ('google-chrome','https://clients5.google.com/translate_a/single',{'client':'dict-chrome-ex','dj':'1','dt':'t','sl':'en','tl':'zh-CN','q':c}),
        ('google-api','https://translate.googleapis.com/translate_a/single',{'client':'gtx','sl':'en','tl':'zh-CN','dt':'t','q':c}),
    ]
    for name,url,params in endpoints:
        try:
            val=google_json(url,params,headers)
            if quality(c,val): return name,val
        except Exception as e:
            if os.getenv('TRANSLATION_DEBUG')=='1': print(f'[translation] {name} failed: {type(e).__name__}: {e}')
    # Last Google route: mobile web page, which can work when the JSON endpoint is blocked.
    try:
        url='https://translate.google.com/m?sl=en&tl=zh-CN&q='+quote_plus(c)
        r=requests.get(url,headers=headers,timeout=12); r.raise_for_status()
        m=re.search(r'<div[^>]*class=["\']result-container["\'][^>]*>(.*?)</div>',r.text,re.S|re.I)
        if m:
            val=clean(re.sub(r'<[^>]+>',' ',m.group(1)))
            if quality(c,val): return 'google-mobile',val
    except Exception as e:
        if os.getenv('TRANSLATION_DEBUG')=='1': print(f'[translation] google-mobile failed: {type(e).__name__}: {e}')
    return '', ''

def openai_translate(c):
    key=os.getenv('OPENAI_API_KEY','').strip()
    if not key: return '', ''
    model=os.getenv('TRANSLATION_OPENAI_MODEL','gpt-5').strip()
    headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'}
    prompt=('Translate the following biomedical abstract from English to professional Simplified Chinese. '
            'Preserve gene/protein names, acronyms, drug names, units, numbers, statistical values, and abbreviations exactly when appropriate. '
            'Do not summarize, omit, or add claims. Return only the Chinese translation.\n\n'+c)
    try:
        r=requests.post('https://api.openai.com/v1/responses',headers=headers,json={'model':model,'input':prompt,'temperature':0},timeout=30)
        r.raise_for_status(); d=r.json()
        val=d.get('output_text','')
        if not val:
            for item in d.get('output',[]):
                for part in item.get('content',[]):
                    if part.get('type')=='output_text': val+=part.get('text','')
        if quality(c,val): return 'openai',clean(val)
        if os.getenv('TRANSLATION_DEBUG')=='1': print('[translation] openai returned text that failed quality validation')
    except Exception as e:
        if os.getenv('TRANSLATION_DEBUG')=='1': print(f'[translation] openai failed: {type(e).__name__}: {e}')
    return '', ''

def translate(text):
    text=clean(text)
    if not text:return ''
    providers=[]
    # Prefer ChatGPT when a key is explicitly configured; otherwise use Google first.
    if os.getenv('OPENAI_API_KEY','').strip(): providers.append('openai')
    providers.append('google')
    out=[]
    used=[]
    for c in chunks(text):
        val=''; provider=''
        for p in providers:
            if p=='openai': provider,val=openai_translate(c)
            else: provider,val=google_translate(c)
            if val: break
        if not val: return ''
        out.append(val); used.append(provider)
    x=clean(' '.join(out)); x=apply_glossary(x)
    return x if quality(text,x) else ''

def zh(text):
    o=clean(text); t=translate(o)
    if t:return t
    g=apply_glossary(o)
    if g!=o and re.search(r'[\u4e00-\u9fff]',g): return '【自动翻译服务暂不可用；以下为术语增强原文】'+g
    return '【自动翻译服务暂不可用，请查看英文原文】'+o
