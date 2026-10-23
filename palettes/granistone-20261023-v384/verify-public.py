import concurrent.futures as cf,hashlib,io,json,pathlib,threading,time
import requests
from PIL import Image
R=pathlib.Path('palettes/granistone-20261023-v384');req=json.loads((R/'url-request.json').read_text());tls=threading.local()
def check(x):
 if not hasattr(tls,'session'):tls.session=requests.Session()
 assert x['url'].startswith('https://raw.githubusercontent.com/Santehsalon-coder/gidvms-tilda-photos/')
 for attempt in range(3):
  try:
   r=tls.session.get(x['url'],timeout=45);r.raise_for_status();assert r.status_code==200;assert hashlib.sha256(r.content).hexdigest()==x['sha256']
   if x['kind']=='image':im=Image.open(io.BytesIO(r.content));im.load();assert list(im.size)==x['size']
   return {**x,'http':200,'verified':True,'bytes':len(r.content)}
  except Exception as e:
   if attempt==2:return {**x,'verified':False,'error':str(e)}
   time.sleep(1)
result=[]
with cf.ThreadPoolExecutor(max_workers=12) as pool:
 for i,x in enumerate(pool.map(check,req),1):
  result.append(x)
  if i%100==0:print('Verified',i,'/',len(req),flush=True)
report={'expected':len(req),'checked':len(result),'passed':sum(x['verified'] for x in result),'errors':[x for x in result if not x['verified']],'files':result}
pathlib.Path('palette-public-url-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('PASS',report['passed'],'ERRORS',len(report['errors']),flush=True);assert not report['errors']
