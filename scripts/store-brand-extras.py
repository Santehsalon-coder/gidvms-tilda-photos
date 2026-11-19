"""Find published products outside the Ready536 category files, without fetching the whole store twice.

The public Tilda project hostname serves the gidvms.ru product sitemap. Product UIDs
are resolved against the live store. A newest-first slice and any new store sections
also cover imports while the product sitemap is being regenerated.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree
import zlib

ROOT = '724727657493'
RECORD = '3742006901'
SITEMAP = 'https://project25163343.tilda.ws'
API = 'https://store.tildaapi.com/api'
NS = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
UID = re.compile(r'[1-9]\d{0,15}')
PART_XML = re.compile(r'/sitemap-store-part\d+\.xml')
PRODUCT_PATH = re.compile(r'/tproduct/(\d+)-')
PAGE_SIZE = 60
# Tilda truncates deep root pagination. Never treat a truncated scan as complete.
MAX_RECENT = 4800

def read_url(url, payload=None):
    headers = {'User-Agent': 'GidvmsStoreSync/1.0', 'Accept': '*/*'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers.update({'Referer': 'https://gidvms.ru/', 'Origin': 'https://gidvms.ru',
                        'Content-Type': 'text/plain;charset=UTF-8'})
    for attempt in range(3):
        try:
            with urlopen(Request(url, data=data, headers=headers), timeout=35) as response:
                return response.read()
        except (OSError, TimeoutError) as error:
            if (isinstance(error, HTTPError) and 400 <= error.code < 500 and error.code != 429) or attempt == 2:
                raise
            time.sleep(2 ** attempt)

def uid(value):
    value = str(value)
    if not UID.fullmatch(value):
        raise ValueError('Malformed product identifier')
    return value

def known_rows(directory):
    generation = json.loads((directory/'generation.json').read_bytes())
    if generation.get('schema') != 536 or not generation.get('parts'):
        raise ValueError('Missing published category generation')
    previous = set()
    for part, total in generation['parts'].items():
        section = json.loads((directory/(uid(part)+'.json')).read_bytes())
        if section['part'] != part or section['total'] != total:
            raise ValueError('Incorrect published category ' + part)
        ids = set()
        for chunk in section['chunks']:
            raw = zlib.decompress(base64.b64decode(chunk['data'], validate=True), -15)
            if len(raw) != chunk['size']:
                raise ValueError('Incomplete local catalogue')
            ids.update(uid(p['uid']) for p in json.loads(raw))
        if len(ids) != total:
            raise ValueError('Incomplete category ' + part)
        previous.update(ids)
    return set(generation['parts']), previous

def product_urls(node):
    if node.tag != NS + 'urlset':
        raise ValueError('Expected a product sitemap')
    result = set()
    for url in node.findall(NS+'url/'+NS+'loc'):
        link = urlsplit(url.text or '')
        match = PRODUCT_PATH.match(link.path)
        if link.hostname not in ('gidvms.ru', 'www.gidvms.ru') or not match:
            raise ValueError('Unexpected product URL in store sitemap')
        result.add(uid(match.group(1)))
    return result

def sitemap_ids():
    root = ElementTree.fromstring(read_url(SITEMAP+'/sitemap-store.xml'))
    if root.tag == NS+'urlset':
        return product_urls(root), 1
    if root.tag != NS+'sitemapindex':
        raise ValueError('Expected store sitemap index')
    paths = set()
    for entry in root.findall(NS+'sitemap/'+NS+'loc'):
        link = urlsplit(entry.text or '')
        if link.hostname not in ('gidvms.ru','www.gidvms.ru') or not PART_XML.fullmatch(link.path):
            raise ValueError('Unknown Tilda sitemap shard')
        paths.add(link.path)
    if not paths:
        raise ValueError('Empty sitemap index')
    def fetch(path):
        return product_urls(ElementTree.fromstring(read_url(SITEMAP+path)))
    with ThreadPoolExecutor(max_workers=3) as pool:
        return set().union(*pool.map(fetch, sorted(paths))), 1+len(paths)

def catalog_page(part, number, newest=False):
    params = {'storepartuid':part,'recid':RECORD,'size':PAGE_SIZE,'slice':number}
    if newest:
        params['sort[created]'] = 'desc'
        if number == 1:
            params['getparts'] = 'true'
            params['flag_root'] = 'withroot'
    result = json.loads(read_url(API+'/getproductslist/?'+urlencode(params)))
    if not isinstance(result.get('products'), list) or not str(result.get('total','')).isdigit():
        raise ValueError('Incomplete Tilda store response')
    if str(result.get('partuid')) != part:
        raise ValueError('Unexpected store section')
    return result

def visible_ids(known_parts, known, mapped):
    result = set(mapped - known)
    calls = 0
    anchors = mapped | known
    scanned = set()
    page = 1
    while True:
        info = catalog_page(ROOT, page, True)
        calls += 1
        if page == 1:
            if not isinstance(info.get('parts'), list):
                raise ValueError('Missing Tilda category index')
            sections = {uid(p['uid']) for p in info['parts']} - known_parts - {ROOT}
        ids = [uid(p['uid']) for p in info['products']]
        if len(ids) != len(set(ids)) or scanned.intersection(ids):
            raise ValueError('Root pagination changed; retry the next sync')
        result.update(set(ids) - known)
        scanned.update(ids)
        if not info.get('nextslice') or (ids and all(i in anchors for i in ids)):
            break
        if not ids or len(scanned) >= MAX_RECENT:
            raise ValueError('Newest store slice exceeded the safe window; wait for the Tilda sitemap')
        page += 1
    for part in sorted(sections):
        page = 1
        emitted = set()
        while True:
            info = catalog_page(part, page)
            calls += 1
            ids = [uid(p['uid']) for p in info['products']]
            if len(set(ids)) != len(ids) or emitted.intersection(ids):
                raise ValueError('New category changed during publish')
            emitted.update(ids)
            if len(emitted) == int(info['total']) and int(info['total']) < 10000:
                break
            if not ids or not info.get('nextslice') or len(emitted) >= MAX_RECENT:
                # The generated sitemap provides eventual coverage even for very large sections.
                if emitted <= anchors:
                    break
                raise ValueError('Large new category awaits Tilda sitemap')
            page += 1
        result.update(emitted-known)
    return result, calls

def fetch_products(ids):
    ids = sorted(ids)
    rows = []
    for start in range(0,len(ids),24):
        batch = ids[start:start+24]
        reply = json.loads(read_url(API+'/getproductsbyuid/', {'productsuid':list(map(int,batch))}))
        if not isinstance(reply.get('products'), list):
            raise ValueError('Missing Tilda product batch')
        found = set()
        for p in reply['products']:
            key = uid(p['uid'])
            if key not in batch or key in found:
                raise ValueError('Unexpected store product')
            found.add(key)
            rows.append(p)
        if missing := set(batch)-found:
            print('No longer returned by Tilda:', ', '.join(sorted(missing)), file=sys.stderr)
    return rows

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    parts, known = known_rows(args.directory)
    mapped, xml_requests = sitemap_ids()
    if known and not mapped:
        raise ValueError('Store sitemap unexpectedly empty')
    candidates, page_requests = visible_ids(parts,known,mapped)
    extra = fetch_products(candidates)
    tmp = args.output.with_name(args.output.name+'.tmp')
    tmp.write_text(json.dumps(extra, ensure_ascii=False, separators=(',',':')),encoding='utf-8')
    tmp.replace(args.output)
    print(f'{len(known)} in category files, {len(mapped)} in sitemap, {len(extra)} additional live rows; '
          f'{xml_requests} XML and {page_requests + (len(candidates)+23)//24} store API requests', flush=True)

if __name__ == '__main__':
    main()
