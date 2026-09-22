"""GID 20260922: public-only source extraction, lossless pictures, URL verification."""
import concurrent.futures as cf
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageDraw

OUT=Path('gid-work'); OUT.mkdir(exist_ok=True)
ROOT=Path('photos/gid-20260922'); ROOT.mkdir(parents=True,exist_ok=True)
BASE='https://gid.com.ru'

def get(url):
    last=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':BASE+'/'})
            with urllib.request.urlopen(req,timeout=35) as r:
                data=r.read(40000001)
                if len(data)>40000000: raise ValueError('Image/file exceeds 40 MB')
                return data,r.headers.get('Content-Type','')
        except Exception as exc:
            last=exc; time.sleep(1+attempt*2)
    raise RuntimeError(str(last))

def source(slug):
    url=BASE+'/shop/product/'+slug
    data,_=get(url)
    soup=BeautifulSoup(data,'html.parser')
    h=soup.select_one('h1')
    article=soup.select_one('.shop2-product-article')
    desc=soup.select_one('.card-page-desc-body')
    photos=[]
    for a in soup.select('a.gr-image-zoom[href]'):
        href=a['href']; match=re.search(r'/d/(.+)$',href)
        if match:
            img=BASE+'/d/'+match.group(1)
            if img not in photos: photos.append(img)
    if not photos: raise ValueError('No original gallery on '+slug)
    attrs={}
    for item in soup.select('.option-item__inner'):
        key=item.select_one('.option-title'); value=item.select_one('.option-body')
        if key and value: attrs[key.get_text(' ',strip=True).rstrip(':')]=value.get_text(' ',strip=True)
    return {'slug':slug,'url':url,'title':h.get_text(' ',strip=True) if h else '', 'article':article.get_text(' ',strip=True) if article else '', 'description':desc.get_text(' ',strip=True) if desc else '', 'attributes':attrs,'photos':photos[:10],'all_gallery':photos}

def sources():
    text=Path('input/gid-product-pages-20260922.txt').read_text().strip()
    assert hashlib.sha256(text.encode()).hexdigest()=='cee96add21ea21e7fd65fd319595a31769db446d11f827bc21f04edde059a23b','Product manifest differs from supplied CSV'
    slugs=text.split(); assert len(slugs)==519 and len(set(slugs))==519
    results=[]; errors=[]
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        tasks={pool.submit(source,x):x for x in slugs}
        for task in cf.as_completed(tasks):
            try: results.append(task.result())
            except Exception as exc: errors.append({'slug':tasks[task],'error':str(exc)})
            if (len(results)+len(errors))%50==0:
                (OUT/'source-progress.json').write_text(json.dumps({'products':results,'errors':errors},ensure_ascii=False))
                print('SOURCE_PROGRESS',len(results),len(errors),flush=True)
    results.sort(key=lambda x:slugs.index(x['slug']))
    (OUT/'metadata.json').write_text(json.dumps({'products':results,'errors':errors},ensure_ascii=False,indent=2))
    print('SOURCE_FINAL',len(results),len(errors),flush=True)
    if errors: print('SOURCE_WARNINGS',json.dumps(errors,ensure_ascii=False),flush=True)

def photo(url):
    raw,mime=get(url)
    original=Image.open(io.BytesIO(raw)); original.load()
    fmt=original.format
    if fmt not in ('JPEG','PNG','WEBP'):raise ValueError('Not a supported picture: '+str(fmt))
    if min(original.size)<100: raise ValueError('Unexpectedly small image')
    reference=original.convert('RGBA')
    expected=reference.tobytes()
    ext={'JPEG':'jpg','PNG':'png','WEBP':'webp'}[fmt]
    choices=[(raw,ext,'already-optimized')]
    if fmt=='JPEG':
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/'src.jpg'; dst=Path(directory)/'dst.jpg';src.write_bytes(raw)
            subprocess.run(['jpegtran','-copy','all','-optimize','-progressive','-outfile',str(dst),str(src)],check=True,capture_output=True)
            candidate=dst.read_bytes()
            if len(candidate)<len(raw) and Image.open(io.BytesIO(candidate)).convert('RGBA').tobytes()==expected:
                choices.append((candidate,'jpg','jpeg-lossless'))
    if len(raw)>250000 or fmt=='PNG':
        buffer=io.BytesIO()
        metadata={k:original.info[k] for k in ('icc_profile','exif','xmp') if original.info.get(k)}
        original.convert('RGBA' if 'A' in original.getbands() else 'RGB').save(buffer,'WEBP',lossless=True,exact=True,method=3,**metadata)
        candidate=buffer.getvalue()
        if len(candidate)<len(raw) and Image.open(io.BytesIO(candidate)).convert('RGBA').tobytes()==expected:
            choices.append((candidate,'webp','webp-lossless'))
    data,ext,method=min(choices,key=lambda x:len(x[0]))
    path=ROOT/(hashlib.sha256(url.encode()).hexdigest()[:24]+'.'+ext)
    path.write_bytes(data)
    assert Image.open(path).convert('RGBA').tobytes()==expected
    return {'source':url,'path':str(path),'before':len(raw),'after':len(data),'width':original.width,'height':original.height,'method':method,'pixels_equal':True,'sha256':hashlib.sha256(data).hexdigest()}

def images():
    meta=json.loads((OUT/'metadata.json').read_text())
    urls=list(dict.fromkeys(u for p in meta['products'] for u in p['photos']))
    successes=[]; errors=[]
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        tasks={pool.submit(photo,u):u for u in urls}
        for task in cf.as_completed(tasks):
            try:successes.append(task.result())
            except Exception as exc:errors.append({'url':tasks[task],'error':str(exc)})
            if (len(successes)+len(errors))%100==0:
                (OUT/'photo-progress.json').write_text(json.dumps({'photos':successes,'errors':errors},ensure_ascii=False))
                print('PHOTO_PROGRESS',len(successes),len(errors),flush=True)
    successes.sort(key=lambda x:x['source'])
    report={'expected_unique':len(urls),'completed_unique':len(successes),'bytes_before':sum(x['before'] for x in successes),'bytes_after':sum(x['after'] for x in successes),'errors':errors,'photos':successes}
    (OUT/'photo-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    preview=OUT/'previews';preview.mkdir(exist_ok=True)
    lookup={x['source']:x for x in successes}
    allproducts=meta['products']
    for group_start in range(0,len(allproducts),50):
        group=allproducts[group_start:group_start+50]
        sheet=Image.new('RGB',(1500,220*((len(group)+5)//6)),'white');draw=ImageDraw.Draw(sheet)
        for n,p in enumerate(group):
            x=(n%6)*250;y=(n//6)*220
            try:
                image=ImageOps.exif_transpose(Image.open(lookup[p['photos'][0]]['path'])).convert('RGB');image.thumbnail((240,178))
                sheet.paste(image,(x+(250-image.width)//2,y+(180-image.height)//2))
            except Exception:pass
            draw.text((x+5,y+183),p['slug'][:32],fill='black')
        sheet.save(preview/f'{group_start//50+1:02d}.jpg',quality=88)
    print('PHOTOS_FINAL',len(successes),len(errors),report['bytes_before'],report['bytes_after'],flush=True)

def verify():
    report=json.loads((OUT/'photo-report.json').read_text())
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    def check(p):
        url='https://raw.githubusercontent.com/Santehsalon-coder/gidvms-tilda-photos/'+commit+'/'+p['path']
        try:
            data,mime=get(url)
            good=hashlib.sha256(data).hexdigest()==p['sha256'] and mime.startswith('image/')
            return {'source':p['source'],'url':url,'valid':good,'bytes':len(data)}
        except Exception as exc:return {'source':p['source'],'url':url,'valid':False,'error':str(exc)}
    with cf.ThreadPoolExecutor(max_workers=8) as pool:checks=list(pool.map(check,report['photos']))
    result={'commit':commit,'all_valid':all(x['valid'] for x in checks),'count':len(checks),'checks':checks}
    (OUT/'public-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('PUBLIC_VERIFIED',result['count'],result['all_valid'],commit,flush=True)
    assert result['all_valid']

{'sources':sources,'images':images,'verify':verify}[sys.argv[1]]()
