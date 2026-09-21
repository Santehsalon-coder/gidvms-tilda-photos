"""Prepare only the explicitly supplied sink photographs; never edit older files."""
import concurrent.futures
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from PIL import Image, ImageOps, ImageDraw

ROOT = Path('photos/import-20260921')
WORK = Path('import-work-20260921')
ROOT.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)
manifest = json.loads(Path('input/new-sinks-20260921.json').read_text())
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.avito.ru/'}

def fetch(url, timeout=45):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read(30000000)
                return data
        except Exception as exc:
            last = exc
            time.sleep(1 + attempt * 2)
    raise RuntimeError(str(last))

def process(job):
    ident, pos, slug = job
    url = 'https://www.avito.ru/autoload/1/items-to-feed/images?imageSlug=/image/1/' + slug
    data = fetch(url)
    source = Image.open(io.BytesIO(data))
    source.load()
    fmt = source.format
    if fmt not in ('JPEG', 'PNG', 'WEBP'):
        raise ValueError(f'{ident}/{pos}: unsupported image format {fmt}')
    ext = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[fmt]
    if source.width < 100 or source.height < 100:
        raise ValueError(f'{ident}/{pos}: unexpectedly small image')
    # Preserve decoded pixels, native dimensions, orientation and colour profile.
    reference = source.convert('RGBA')
    options = [(data, ext, 'original-already-compressed')]
    if fmt == 'JPEG':
        with tempfile.TemporaryDirectory() as folder:
            inp = Path(folder) / 'in.jpg'
            out = Path(folder) / 'out.jpg'
            inp.write_bytes(data)
            subprocess.run(['jpegtran', '-copy', 'all', '-optimize', '-progressive', '-outfile', str(out), str(inp)], check=True, capture_output=True)
            candidate = out.read_bytes()
            test = Image.open(io.BytesIO(candidate)).convert('RGBA')
            if test.size == reference.size and test.tobytes() == reference.tobytes():
                options.append((candidate, 'jpg', 'jpeg-lossless-optimization'))
    if len(data) > 70000:
        buffer = io.BytesIO()
        metadata = {}
        for key in ('icc_profile', 'exif', 'xmp'):
            if source.info.get(key):
                metadata[key] = source.info[key]
        source.convert('RGBA' if 'A' in source.getbands() else 'RGB').save(buffer, 'WEBP', lossless=True, exact=True, method=4, **metadata)
        candidate = buffer.getvalue()
        test = Image.open(io.BytesIO(candidate)).convert('RGBA')
        if test.size == reference.size and test.tobytes() == reference.tobytes():
            options.append((candidate, 'webp', 'webp-lossless'))
    chosen, ext, method = min(options, key=lambda item: len(item[0]))
    directory = ROOT / ident
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{pos:02d}.{ext}'
    path.write_bytes(chosen)
    final = Image.open(path).convert('RGBA')
    assert final.size == reference.size and final.tobytes() == reference.tobytes()
    assert len(chosen) <= len(data)
    return {'id': ident, 'position': pos, 'source': url, 'path': str(path), 'width': source.width, 'height': source.height, 'before': len(data), 'after': len(chosen), 'method': method, 'pixels_equal': True}

jobs = [(ident, n, slug) for ident, slugs in manifest.items() for n, slug in enumerate(slugs, 1)]
results, failures = [], []
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(process, job): job for job in jobs}
    for future in concurrent.futures.as_completed(futures):
        job = futures[future]
        try:
            item = future.result()
            results.append(item)
            print('OK', item['id'], item['position'], item['before'], item['after'], item['method'], flush=True)
        except Exception as exc:
            failures.append({'id': job[0], 'position': job[1], 'error': str(exc)})
            print('FAIL', job[0], job[1], str(exc), flush=True)
results.sort(key=lambda item: (item['id'], item['position']))
report = {'product_count': len(manifest), 'expected_photos': len(jobs), 'completed_photos': len(results), 'bytes_before': sum(x['before'] for x in results), 'bytes_after': sum(x['after'] for x in results), 'all_pixels_equal': all(x['pixels_equal'] for x in results), 'failures': failures, 'photos': results}
(WORK / 'photo-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
# Public catalogue lookup is read-only and is used solely to prevent duplicate imports.
try:
    products, pages, seen = [], [], set()
    part = 1
    while part not in seen:
        seen.add(part)
        query = urllib.parse.urlencode({'storepartuid': '724727657493', 'recid': '3742006901', 'size': '100', 'slice': str(part)})
        payload = json.loads(fetch('https://store.tildaapi.com/api/getproductslist/?' + query))
        if not isinstance(payload.get('products'), list):
            raise ValueError('Public catalogue did not return a products array')
        products.extend(payload['products'])
        pages.append({k: v for k, v in payload.items() if k != 'products'})
        following = payload.get('nextslice')
        if following:
            part = int(following)
        elif len(products) < int(payload.get('total', len(products))) and payload['products']:
            part += 1
        else:
            break
        if len(seen) > 80:
            raise ValueError('Unexpected catalogue pagination')
    (WORK / 'catalogue.json').write_text(json.dumps({'products': products, 'pages': pages}, ensure_ascii=False, indent=2))
    print('CATALOGUE_RECORDS', len(products), flush=True)
except Exception as exc:
    (WORK / 'catalogue-error.txt').write_text(str(exc))
    print('CATALOGUE_LOOKUP_FAILED', str(exc), flush=True)
# Contact sheets are verification previews, not replacement product photographs.
preview_dir = WORK / 'previews'
preview_dir.mkdir(exist_ok=True)
for ident in manifest:
    items = sorted([x for x in results if x['id'] == ident], key=lambda x: x['position'])
    if not items:
        continue
    cols = 5
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new('RGB', (300 * cols, 250 * rows), 'white')
    draw = ImageDraw.Draw(sheet)
    for n, item in enumerate(items):
        img = ImageOps.exif_transpose(Image.open(item['path'])).convert('RGB')
        img.thumbnail((290, 215))
        x, y = (n % cols) * 300, (n // cols) * 250
        sheet.paste(img, (x + (300 - img.width)//2, y + (215 - img.height)//2))
        draw.text((x + 8, y + 220), f"{ident} / {item['position']:02d} / {item['after']//1024} KiB", fill='black')
    sheet.save(preview_dir / (ident + '.jpg'), quality=88)
print(json.dumps({k: v for k, v in report.items() if k != 'photos'}, ensure_ascii=False), flush=True)
if failures or len(results) != len(jobs):
    raise SystemExit('Not publishing: one or more supplied photos failed')
(ROOT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
