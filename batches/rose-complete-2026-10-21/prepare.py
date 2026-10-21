"""Audit and append missing public Rose photos; never alter existing assets or product data."""
import base64,concurrent.futures,gzip,hashlib,html,io,json,pathlib,re,sys,time,urllib.parse,urllib.request
import numpy as np
from PIL import Image,ImageOps
BATCH='rose-complete-2026-10-21'
ROOT=pathlib.Path('photos')/BATCH
ROOT.mkdir(parents=True,exist_ok=True)
WORK=pathlib.Path('/tmp')/BATCH;WORK.mkdir(exist_ok=True)
MANIFEST=pathlib.Path('batches')/BATCH/'manifest.json.gz.b64'
REPO='Santehsalon-coder/gidvms-tilda-photos'
OLD_COMMIT='3ef7c9155589d894f96c6292bce83003a851ef8f'
def encoded(url):
    p=urllib.parse.urlsplit(url)
    assert p.scheme=='https' and p.netloc in ('rose-sanitary.ru','rs-msk.ru','static.tildacdn.com','raw.githubusercontent.com'),url
    return urllib.parse.urlunsplit((p.scheme,p.netloc,urllib.parse.quote(urllib.parse.unquote(p.path),safe='/'),p.query,p.fragment))
def download(url):
    key=hashlib.sha256(url.encode()).hexdigest()
    cache=WORK/key
    if cache.exists():return cache.read_bytes()
    for attempt in range(3):
        try:
            req=urllib.request.Request(encoded(url),headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req,timeout=30) as r:
                assert r.status==200;data=r.read(35000001)
            assert 0<len(data)<=35000000
            cache.write_bytes(data);return data
        except Exception:
            if attempt==2:raise
            time.sleep(1+attempt)
def gallery(p):
    try:
        text=download(p['source']).decode('utf-8')
        match=re.search(r'Артикул</td>\s*<td[^>]*>(.*?)</td>',text,re.S)
        actual=html.unescape(re.sub('<[^>]+>','',match.group(1))).strip() if match else ''
        if actual.upper()!=p['sku'].upper():raise ValueError('SKU mismatch '+actual)
        urls=list(dict.fromkeys(urllib.parse.urljoin(p['source'],html.unescape(s)) for s in re.findall(r'<a class="catalog-element__image-wrap" href="([^"]+)"',text)))
        return dict(sku=p['sku'],images=urls,error=None)
    except Exception as exc:return dict(sku=p['sku'],images=[],error=str(exc))
def analyze(url):
    try:
        data=download(url);image=Image.open(io.BytesIO(data));image.load()
        assert image.format in ('JPEG','PNG','WEBP') and min(image.size)>=50
        im=ImageOps.exif_transpose(image).convert('RGB')
        a=np.asarray(im.resize((32,32),Image.Resampling.LANCZOS),dtype=np.float32)
        c=np.cos(np.pi/32*(np.arange(32)+0.5)[None,:]*np.arange(8)[:,None])
        d=(c@a.mean(axis=2)@c.T).ravel()[1:];bits=d>np.median(d)
        phash=int(''.join('1' if x else '0' for x in bits),2)
        return dict(url=url,sha256=hashlib.sha256(data).hexdigest(),width=image.width,height=image.height,bytes=len(data),phash=phash,array=a,error=None)
    except Exception as exc:return dict(url=url,error=str(exc))
def nearest(a,refs,images):
    best=None
    for ref in refs:
        b=images[ref]
        if b['error']:continue
        distance=(a['phash']^b['phash']).bit_count()
        if distance>12:continue
        rmse=float(np.sqrt(np.mean((a['array']-b['array'])**2)))
        result=dict(url=ref,distance=distance,rmse=round(rmse,4),identical=a['sha256']==b['sha256'])
        if result['identical']:return result
        if best is None or (rmse,distance)<(best['rmse'],best['distance']):best=result
    return best
def optimize(url):
    data=download(url);source=Image.open(io.BytesIO(data));source.load()
    fmt=source.format;ext={'JPEG':'jpg','PNG':'png','WEBP':'webp'}[fmt]
    chosen=data;method='original'
    if source.mode in ('RGB','RGBA'):
        buf=io.BytesIO();metadata={k:source.info[k] for k in ('icc_profile','exif','xmp') if source.info.get(k)}
        source.save(buf,'WEBP',lossless=True,exact=True,method=4,**metadata)
        candidate=buf.getvalue();test=Image.open(io.BytesIO(candidate));test.load()
        if len(candidate)<len(data) and test.size==source.size and test.convert('RGBA').tobytes()==source.convert('RGBA').tobytes() and ImageOps.exif_transpose(test).size==ImageOps.exif_transpose(source).size:
            chosen=candidate;ext='webp';method='webp-lossless'
    key=hashlib.sha256(url.encode()).hexdigest()[:24];dest=ROOT/key[:2]/(key+'.'+ext);dest.parent.mkdir(exist_ok=True)
    if dest.exists() and dest.read_bytes()!=chosen:raise ValueError('Refusing to replace different bytes')
    dest.write_bytes(chosen);final=Image.open(dest);final.load()
    assert final.size==source.size and final.convert('RGBA').tobytes()==source.convert('RGBA').tobytes()
    assert len(chosen)<=len(data)
    return dict(source=url,path=str(dest),width=source.width,height=source.height,source_bytes=len(data),bytes=len(chosen),sha256=hashlib.sha256(chosen).hexdigest(),pixels_equal=True,method=method)
def main():
    manifest=json.loads(gzip.decompress(base64.b64decode(MANIFEST.read_text())));products=manifest['products']
    prior=json.loads(pathlib.Path('photos/rose-2026-10-12/report.json').read_text())
    reuse={p['source']:p for p in prior['photos']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        pages=[]
        for n,p in enumerate(pool.map(gallery,products),1):
            pages.append(p)
            if n%50==0:print('Pages',n,'/',len(products),flush=True)
    bysku={p['sku']:p for p in pages}
    urls=list(dict.fromkeys(u for p in products for u in p['existing']+bysku[p['sku']]['images']+p['supplier']))
    images={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for n,a in enumerate(pool.map(analyze,urls),1):
            images[a['url']]=a
            if n%100==0:print('Images',n,'/',len(urls),'errors',sum(bool(x['error']) for x in images.values()),flush=True)
    audits=[];publish=set();reused=set()
    for p in products:
        audit={**p,'official_count':len(bysku[p['sku']]['images']),'page_error':bysku[p['sku']]['error'],'additions':[],'duplicates':[],'ambiguous':[],'errors':[]}
        failed=[u for u in p['existing'] if images[u]['error']]
        if failed:audit['errors'] += [dict(url=u,error=images[u]['error'],kind='existing') for u in failed]
        refs=list(p['existing'])
        for url in dict.fromkeys(bysku[p['sku']]['images']+p['supplier']):
            a=images[url]
            if a['error']:
                audit['errors'].append(dict(url=url,error=a['error'],kind='source'));continue
            match=nearest(a,refs,images)
            if url in refs or (match and (match['identical'] or (match['distance']<=2 and match['rmse']<=1.5))):
                audit['duplicates'].append(dict(source=url,match=match));continue
            if match and match['distance']<=10 and match['rmse']<15:
                audit['ambiguous'].append(dict(source=url,match=match));continue
            if failed:continue
            audit['additions'].append(url);refs.append(url)
            if url in reuse:reused.add(url)
            else:publish.add(url)
        audits.append(audit)
    photos=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for n,p in enumerate(pool.map(optimize,sorted(publish)),1):
            photos.append(p)
            if n%50==0:print('Prepared',n,'/',len(publish),flush=True)
    reused_photos=[]
    for url in sorted(reused):
        p=reuse[url];final_url='https://raw.githubusercontent.com/'+REPO+'/'+OLD_COMMIT+'/'+p['path']
        data=download(final_url);im=Image.open(io.BytesIO(data));im.load()
        assert hashlib.sha256(data).hexdigest()==p['sha256'] and im.size==(p['width'],p['height'])
        reused_photos.append({**p,'url':final_url})
    clean_images=[{k:v for k,v in a.items() if k!='array'} for a in images.values()]
    (ROOT/'image-audit.json').write_text(json.dumps(clean_images,ensure_ascii=False))
    report=dict(batch=BATCH,products=len(products),pages=pages,audits=audits,photos=photos,reused=reused_photos,summary=dict(products_changed=sum(bool(p['additions']) for p in audits),added=sum(len(p['additions']) for p in audits),new_files=len(photos),reused_files=len(reused_photos),ambiguous=sum(len(p['ambiguous']) for p in audits),errors=sum(len(p['errors']) for p in audits),page_errors=sum(bool(p['page_error']) for p in audits)))
    (ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report['summary']),flush=True)
def verify(commit):
    report=json.loads((ROOT/'report.json').read_text())
    def check(p):
        url='https://raw.githubusercontent.com/'+REPO+'/'+commit+'/'+p['path']
        data=download(url);im=Image.open(io.BytesIO(data));im.load()
        assert hashlib.sha256(data).hexdigest()==p['sha256'] and im.size==(p['width'],p['height'])
        return url
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:verified=list(pool.map(check,report['photos']))
    summary=dict(photo_commit=commit,expected=len(report['photos']),verified=len(verified),failures=[])
    (ROOT/'verification.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--verify':verify(sys.argv[2])
    else:main()
