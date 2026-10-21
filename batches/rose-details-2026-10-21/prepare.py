"""Download and losslessly optimize the public Rose product photos for this batch only."""
import concurrent.futures, hashlib, io, json, pathlib, time, urllib.request, urllib.parse, sys
from PIL import Image, ImageOps

BATCH='rose-details-2026-10-21'
ROOT=pathlib.Path('photos')/BATCH
ROOT.mkdir(parents=True,exist_ok=True)
MANIFEST=pathlib.Path('batches')/BATCH/'manifest.json'
def process(job):
    url=job['url']
    if not (url.startswith('https://rs-msk.ru/aaaaaaaaaaaaa/photo-stock/Rose/') or url.startswith('https://rose-sanitary.ru/upload/')):
        raise ValueError('Unexpected source host or path')
    data=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(urllib.parse.quote(url,safe=':/%?=&'),headers={'User-Agent':'Mozilla/5.0'}),timeout=35) as response:
                data=response.read(35000001)
            if len(data)>35000000:raise ValueError('Image too large')
            if not data:raise ValueError('Empty image')
            source=Image.open(io.BytesIO(data));source.load()
            break
        except Exception:
            if attempt==2:raise
            time.sleep(1+attempt)
    if hashlib.sha256(data).hexdigest()!=job['source_sha256']:raise ValueError('Source changed since visual review')
    fmt=source.format
    if fmt not in ('JPEG','PNG','WEBP'):raise ValueError('Unsupported format '+str(fmt))
    if min(source.size)<80:raise ValueError('Unexpectedly small image')
    original=source.convert('RGBA')
    ext={'JPEG':'jpg','PNG':'png','WEBP':'webp'}[fmt]
    chosen=data;method='original'
    # Retain original size, orientation and profile. Convert only when every decoded pixel matches.
    if source.mode in ('RGB','RGBA'):
        buf=io.BytesIO()
        metadata={k:source.info[k] for k in ('icc_profile','exif','xmp') if source.info.get(k)}
        source.save(buf,'WEBP',lossless=True,exact=True,method=4,**metadata)
        candidate=buf.getvalue()
        test=Image.open(io.BytesIO(candidate));test.load()
        if len(candidate)<len(data) and test.size==source.size and test.convert('RGBA').tobytes()==original.tobytes() and ImageOps.exif_transpose(test).size==ImageOps.exif_transpose(source).size:
            chosen=candidate;ext='webp';method='webp-lossless'
    key=hashlib.sha256(url.encode()).hexdigest()[:24]
    dest=ROOT/key[:2]/(key+'.'+ext)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists() and dest.read_bytes()!=chosen:raise ValueError('Refusing to replace different bytes')
    dest.write_bytes(chosen)
    final=Image.open(dest);final.load()
    assert final.size==source.size and final.convert('RGBA').tobytes()==original.tobytes()
    assert 0<len(chosen)<=len(data)
    return {'source':url,'path':str(dest),'width':source.width,'height':source.height,'source_bytes':len(data),'bytes':len(chosen),'sha256':hashlib.sha256(chosen).hexdigest(),'pixels_equal':True,'method':method}
def main():
    manifest=json.loads(MANIFEST.read_text())
    jobs=manifest['images'];results=[];failures=[]
    assert len({x['url'] for x in jobs})==len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(process,j):j for j in jobs}
        for i,f in enumerate(concurrent.futures.as_completed(futures)):
            try:results.append(f.result())
            except Exception as exc:failures.append({'source':futures[f]['url'],'error':str(exc)})
            if (i+1)%100==0:print('Photos',i+1,'/',len(jobs),'failed',len(failures),flush=True)
    report={'batch':BATCH,'products':manifest['products'],'expected':len(jobs),'completed':len(results),'failures':failures,'photos':sorted(results,key=lambda x:x['source'])}
    (ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='photos'},ensure_ascii=False))
    if failures or len(results)!=len(jobs):raise SystemExit('No publish: image verification failed')
def verify(commit):
    report=json.loads((ROOT/'report.json').read_text())
    def check(p):
        url='https://raw.githubusercontent.com/Santehsalon-coder/gidvms-tilda-photos/'+commit+'/'+p['path']
        with urllib.request.urlopen(url,timeout=45) as response:
            assert response.status==200
            data=response.read()
        im=Image.open(io.BytesIO(data));im.load()
        assert len(data)==p['bytes'] and hashlib.sha256(data).hexdigest()==p['sha256']
        assert im.size==(p['width'],p['height'])
        return url
    verified=[];failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(check,p):p for p in report['photos']}
        for f in concurrent.futures.as_completed(futures):
            try:verified.append(f.result())
            except Exception as exc:failures.append({'path':futures[f]['path'],'error':str(exc)})
    summary={'photo_commit':commit,'expected':len(report['photos']),'verified':len(verified),'failures':failures}
    (ROOT/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if failures:raise SystemExit('Final URL verification failed')
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--verify':verify(sys.argv[2])
    else:main()
