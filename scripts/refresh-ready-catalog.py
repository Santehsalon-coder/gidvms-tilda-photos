"""Build validated category snapshots outside customer browsers.
Publish only complete generations; a failed read leaves the previous files intact.
"""
import argparse, base64, concurrent.futures, json, math, os, pathlib, tempfile, time, urllib.parse, urllib.request, zlib
PARTS = ('338629674673','872810302623','759820970723','496159877623','422452689173','267674723263','156462311843','180974060633','389742041783','376322173453','684839949473','540130186073','103892834853')
SIZE=200

def page(part,index,size=SIZE):
    query=urllib.parse.urlencode(dict(storepartuid=part,recid='3742006901',size=size,slice=index,getparts='true',getoptions='true',flag_root='withroot',c=int(time.time()*1000)))
    for attempt in range(3):
        try:
            with urllib.request.urlopen('https://store.tildaapi.com/api/getproductslist/?'+query,timeout=40) as response:
                return json.load(response)
        except Exception:
            if attempt==2: raise
            time.sleep(attempt+1)

def validate(part,pages,size=SIZE):
    total=int(pages[0]['total']); count=max(1,math.ceil(total/size)); items=[]
    if not 0<=total<=50000 or len(pages)!=count: raise ValueError('Invalid page count '+part)
    for index,data in enumerate(pages,1):
        expected=min(size,max(0,total-(index-1)*size))
        if int(data['total'])!=total or len(data['products'])!=expected: raise ValueError('Category changed '+part)
        if data.get('nextslice') and int(data['nextslice'])!=(index+1 if index<count else 0): raise ValueError('Invalid pagination '+part)
        items.extend(data['products'])
    if len(items)!=total or len({str(p.get('uid')) for p in items})!=total: raise ValueError('Duplicate/missing UID '+part)
    if any(not str(p.get('uid','')).isdigit() or str(p['uid'])=='0' for p in items): raise ValueError('Invalid UID '+part)
    return items

def collect(part):
    first=page(part,1); count=max(1,math.ceil(int(first['total'])/SIZE))
    # Two category workers, two page workers each: at most four API requests.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pages=[first,*pool.map(lambda i:page(part,i),range(2,count+1))]
    items=validate(part,pages)
    raw=json.dumps(dict(schema=536,part=part,at=int(time.time()*1000),total=len(items),items=items),ensure_ascii=False,separators=(',',':')).encode()
    compressor=zlib.compressobj(9,zlib.DEFLATED,-15)
    packed=compressor.compress(raw)+compressor.flush()
    envelope=dict(encoding='deflate-raw-base64',size=len(raw),data=base64.b64encode(packed).decode())
    return part,json.dumps(envelope,separators=(',',':')),len(items)

def build(output):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        built=list(pool.map(collect,PARTS))
    output.mkdir(parents=True,exist_ok=True)
    # The workflow commits all completed files in one Git commit.
    for part,content,count in built:
        tmp=output/(part+'.tmp'); tmp.write_text(content); os.replace(tmp,output/(part+'.json'))
        print(part,count,len(content),flush=True)
    (output/'generation.json').write_text(json.dumps(dict(schema=536,at=int(time.time()*1000),parts={p:n for p,_,n in built}),separators=(',',':')))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=pathlib.Path,default=pathlib.Path('catalog/ready536'));args=parser.parse_args();build(args.output)
