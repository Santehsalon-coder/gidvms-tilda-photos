"""Build validated category snapshots outside customer browsers.
Publish only complete generations; a failed read leaves the previous files intact.
"""
import argparse, base64, concurrent.futures, json, math, os, pathlib, tempfile, time, urllib.parse, urllib.request, zlib
PARTS = ('338629674673','872810302623','759820970723','496159877623','422452689173','267674723263','156462311843','180974060633','389742041783','376322173453','684839949473','540130186073','103892834853','197837778403','367207151023','387624993293','269147260273','386070158483')
SIZE=200
ACCESSORIES = '180974060633'

def page(part,index,size=SIZE,created=None,brands=None):
    params=dict(storepartuid=part,recid='3742006901',size=size,slice=index,getparts='true',getoptions='true',flag_root='withroot',c=int(time.time()*1000))
    if created:
        params['sort[created]'] = created
    if brands:
        params['filters[brand][]'] = brands
    query=urllib.parse.urlencode(params,doseq=True)
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

def ids(data):
    return [str(row['uid']) for row in data['products']]

def require_page(part, data, index, total, size):
    count = max(1, math.ceil(total / size))
    expected = min(size, total - (index - 1) * size)
    following = index + 1 if index < count else 0
    rows = data['products']
    if (str(data.get('partuid')) != part or int(data['total']) != total
        or int(data['slice']) != index or int(data.get('nextslice') or 0) != following
        or len(rows) != expected):
        raise ValueError('Invalid category page ' + part)
    for row in rows:
        uid = str(row['uid'])
        members = row['partuids']
        if isinstance(members, str):
            members = json.loads(members)
        if (not uid.isdecimal() or int(uid) <= 0
            or not isinstance(members, list) or part not in map(str, members)):
            raise ValueError('Invalid category member ' + part)

def recover_ordered(part, native, size=SIZE):
    total = int(native[0]['total'])
    count = math.ceil(total / size)
    if not 0 < total <= 50000 or len(native) != count:
        raise ValueError('Invalid page count ' + part)
    original = {}
    for index, data in enumerate(native, 1):
        actual = int(data['slice'])
        if actual == 1 and index > 1:
            require_page(part, data, 1, total, size)
            if ids(data) != ids(native[0]):
                raise ValueError('First page changed ' + part)
        else:
            require_page(part, data, index, total, size)
            original[index] = ids(data)

    prefix = []
    for index, data in enumerate(native, 1):
        if index not in original:
            break
        prefix.extend(data['products'])
    prefix_ids = [str(row['uid']) for row in prefix]
    if len(set(prefix_ids)) != len(prefix_ids):
        raise ValueError('Duplicate UID in storefront order ' + part)

    # Date windows establish membership only. Display order comes from raw store responses.
    products = {}
    window = min(count, (count + 1) // 2 + 1)
    for direction in ('asc', 'desc'):
        seen = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            window_pages = list(pool.map(lambda i: page(part, i, size, created=direction), range(1, window+1)))
        for index, data in enumerate(window_pages, 1):
            require_page(part, data, index, total, size)
            for row in data['products']:
                uid = str(row['uid'])
                if uid in seen or (uid in products and products[uid] != row):
                    raise ValueError('Duplicate or changed product ' + part)
                products[uid] = row
                seen.add(uid)
    if len(products) != total or not set(prefix_ids) <= products.keys():
        raise ValueError('Missing category members ' + part)

    missing = products.keys() - set(prefix_ids)
    if not missing:
        raise ValueError('Unexpected empty recovery tail ' + part)
    brands = {products[uid].get('brand') for uid in missing}
    if not brands or not all(isinstance(name, str) and name.strip() for name in brands):
        raise ValueError('Missing brand for ordered tail ' + part)
    filters = sorted(brands)
    first = page(part, 1, size, brands=filters)
    subtotal = int(first['total'])
    if not 0 < subtotal < total:
        raise ValueError('Unbounded brand filter ' + part)
    filtered = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        filtered_pages = [first, *pool.map(lambda i: page(part, i, size, brands=filters),
                                          range(2, math.ceil(subtotal/size)+1))]
    for index, data in enumerate(filtered_pages, 1):
        require_page(part, data, index, subtotal, size)
        filtered.extend(data['products'])

    filtered_ids = [str(row['uid']) for row in filtered]
    filtered_set = set(filtered_ids)
    eligible = {uid for uid, row in products.items() if row.get('brand') in brands}
    if (len(filtered_set) != subtotal or filtered_set != eligible or not missing <= filtered_set
        or any(row.get('brand') not in brands for row in filtered)):
        raise ValueError('Incomplete brand filter ' + part)
    # The narrower listing must start with exactly the same relative order as
    # the native prefix, followed exclusively by the previously unreachable tail.
    overlap = [uid for uid in prefix_ids if uid in filtered_set]
    tail = filtered[len(overlap):]
    if len(overlap) < 2 or filtered_ids[:len(overlap)] != overlap or set(filtered_ids[len(overlap):]) != missing:
        raise ValueError('Filtered display order disagrees with storefront ' + part)
    ordered = prefix + tail
    uids = [str(row['uid']) for row in ordered]
    if len(uids) != total or set(uids) != products.keys():
        raise ValueError('Incomplete recovered category ' + part)
    for index, expected in original.items():
        if uids[(index-1)*size:index*size] != expected:
            raise ValueError('Storefront display order changed ' + part)
    final = page(part, 1, size)
    require_page(part, final, 1, total, size)
    if ids(final) != original[1]:
        raise ValueError('First page changed ' + part)
    return ordered

def collect(part):
    first=page(part,1); count=max(1,math.ceil(int(first['total'])/SIZE))
    # Two category workers, two page workers each: at most four API requests.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pages=[first,*pool.map(lambda i:page(part,i),range(2,count+1))]
    reset = part == ACCESSORIES and any(str(data.get('slice')) == '1' for data in pages[1:])
    items = recover_ordered(part, pages) if reset else validate(part, pages)
    return part,pack(part,items,int(time.time()*1000)),len(items)

def pack(part,items,stamp):
    chunks=[];batch=[];size=2
    def emit(rows):
        raw=('['+','.join(rows)+']').encode()
        packed=zlib.compress(raw,9)[2:-4]
        chunks.append(dict(size=len(raw),data=base64.b64encode(packed).decode()))
    for product in items:
        text=json.dumps(product,ensure_ascii=False,separators=(',',':'));length=len(text.encode())+1
        if length>2_000_000: raise ValueError('Product exceeds bounded decoder size')
        if size+length>2_000_000 and batch:emit(batch);batch=[];size=2
        batch.append(text);size+=length
    if batch or not chunks:emit(batch)
    return json.dumps(dict(schema=536,encoding='deflate-raw-base64-chunks',part=part,at=stamp,total=len(items),chunks=chunks),separators=(',',':'))

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
