"""Publish a validated full category snapshot; never replace it with partial data."""
import base64, concurrent.futures, json, math, pathlib, time, urllib.parse, urllib.request, zlib
PART='389742041783'
def page(index):
    query=urllib.parse.urlencode(dict(storepartuid=PART,recid='3742006901',size=100,slice=index,getparts='true',getoptions='true',flag_root='withroot',c=int(time.time()*1000)))
    for attempt in range(3):
        try:
            with urllib.request.urlopen('https://store.tildaapi.com/api/getproductslist/?'+query,timeout=45) as response:
                return json.load(response)
        except Exception:
            if attempt==2: raise
            time.sleep(2)
def build():
    first=page(1);total=int(first['total']);assert 0<=total<=1000000
    count=max(1,math.ceil(total/100))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pages=[first,*pool.map(page,range(2,count+1))]
    items=[]
    for index,data in enumerate(pages,1):
        expected=100 if index<count else total-100*(count-1)
        assert int(data['total'])==total and len(data['products'])==expected
        assert not data.get('nextslice') or int(data['nextslice'])==(index+1 if index<count else 0)
        items.extend(data['products'])
    assert len(items)==total and len({str(p['uid']) for p in items})==total
    assert all(str(p.get('uid','')).isdigit() and str(p['uid'])!='0' for p in items)
    raw=json.dumps(dict(part=PART,at=int(time.time()*1000),total=total,items=items),ensure_ascii=False,separators=(',',':')).encode()
    packed=dict(encoding='deflate-raw-base64',size=len(raw),data=base64.b64encode(zlib.compress(raw,9)[2:-4]).decode())
    target=pathlib.Path('catalog/plumbing.json');target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(packed,separators=(',',':')))
    print(f'Validated {total} products; snapshot {target.stat().st_size} bytes')
if __name__=='__main__': build()
