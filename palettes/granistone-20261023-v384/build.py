"""Reproduce only the reviewed public palette assets in a new batch directory."""
import concurrent.futures as cf, hashlib, io, json, pathlib, time, urllib.request
from PIL import Image
ROOT=pathlib.Path('palettes/granistone-20261023-v384')
request=json.loads((ROOT/'build-request.json').read_text())
def sha(data):return hashlib.sha256(data).hexdigest()
def put_image(im,path,expected):
 data=io.BytesIO();im.save(data,format='WEBP',lossless=True,method=6);data=data.getvalue()
 assert sha(data)==expected,('Encoded output differs from reviewed file',path)
 check=Image.open(io.BytesIO(data))
 if im.mode=='RGB':assert check.convert('RGB').tobytes()==im.tobytes(),path
 else:
  white=Image.new('RGBA',im.size,'white')
  assert Image.alpha_composite(white,check.convert('RGBA')).tobytes()==Image.alpha_composite(white,im.convert('RGBA')).tobytes(),path
 f=ROOT/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data)
 return {'path':path,'sha256':sha(data),'bytes':len(data),'size':list(im.size)}
def fetch(url,expected):
 assert url.startswith('https://granistone.ru/upload/iblock/'),url
 for attempt in range(3):
  try:
   data=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=60).read()
   assert sha(data)==expected,('Source changed',url)
   return Image.open(io.BytesIO(data))
  except Exception:
   if attempt==2:raise
   time.sleep(2)
def prepare(x):
 im=fetch(x['source'],x['originalSha256']).convert('RGB').crop(x['crop']);assert im.width==im.height
 return [put_image(im,x['full'],x['fullSha256']),put_image(im.resize((280,280),Image.Resampling.LANCZOS),x['thumb'],x['thumbSha256'])]
assert len(request['products'])==545
results=[]
with cf.ThreadPoolExecutor(max_workers=5) as pool:
 for i,rows in enumerate(pool.map(prepare,request['products']),1):
  results.extend(rows)
  if i%50==0:print('Prepared',i,'/ 545',flush=True)
for s in request['series']:
 im=fetch(s['source'],s['originalSha256']);results.append(put_image(im,s['path'],s['sha256']))
nav=Image.new('RGB',(280,164),'white')
for i,ident in enumerate(request['navIds']):
 im=Image.open(ROOT/('images/full/'+ident+'.webp')).resize((134,76),Image.Resampling.LANCZOS);nav.paste(im,((i%2)*140,(i//2)*82))
results.append(put_image(nav,'images/nav-palette.webp',request['navSha256']))
assert len(results)==1095
(ROOT/'build-report.json').write_text(json.dumps({'count':len(results),'files':results},ensure_ascii=False,indent=2))
print('Verified all',len(results),'public images against reviewed originals and prepared hashes.',flush=True)
