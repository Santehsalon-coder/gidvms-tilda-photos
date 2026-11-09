import concurrent.futures as cf
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from PIL import Image

BASE = Path('batches/sunerzha-existing-722-2026-11-09')
OUT = Path('photos/sunerzha-existing-722-2026-11-09')
OUT.mkdir(parents=True, exist_ok=True)
manifest = json.loads((BASE / 'manifest.json').read_text())
assert manifest['product_count'] == 722 and len(manifest['images']) == 1922
EXTENSIONS = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif'}

def retrieve(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=35) as response:
                if response.status != 200:
                    raise ValueError('HTTP ' + str(response.status))
                data = response.read(25 * 1024 * 1024 + 1)
                if len(data) > 25 * 1024 * 1024:
                    raise ValueError('Image exceeds 25 MiB')
                return data
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            code = getattr(error, 'code', None)
            if attempt == 2 or (code and code not in (429, 500, 502, 503, 504)):
                raise
            delay = 2 ** (attempt + 1)
            if code == 429:
                try:
                    delay = min(60, max(delay, int(error.headers.get('Retry-After', '15'))))
                except (ValueError, AttributeError):
                    delay = 15
            time.sleep(delay)

def inspect(data):
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.format, list(image.size)

def prepare(job):
    try:
        url = job['source']
        assert urllib.parse.urlparse(url).hostname == 'b2b.sunerzha.com'
        assert hashlib.sha256(url.encode()).hexdigest()[:24] == job['key']
        record = OUT / 'records' / (job['key'] + '.json')
        if record.exists():
            previous = json.loads(record.read_text())
            data = Path(previous['path']).read_bytes()
            if previous['source'] == url and hashlib.sha256(data).hexdigest() == previous['sha256']:
                inspect(data)
                return previous
        data = retrieve(url)
        fmt, size = inspect(data)
        if fmt not in EXTENSIONS:
            raise ValueError('Unsupported image format ' + str(fmt))
        path = OUT / 'images' / (job['key'] + EXTENSIONS[fmt])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        result = dict(source=url, key=job['key'], path=str(path), bytes=len(data),
                      sha256=hashlib.sha256(data).hexdigest(), size=size,
                      format=fmt, exact_original=True)
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(result, ensure_ascii=False))
        return result
    except Exception as error:
        return {'source': job['source'], 'key': job['key'], 'error': str(error)}

def verify(entry, commit):
    url = 'https://raw.githubusercontent.com/Santehsalon-coder/gidvms-tilda-photos/' + commit + '/' + entry['path']
    try:
        data = retrieve(url)
        fmt, size = inspect(data)
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], 'Published bytes differ'
        assert fmt == entry['format'] and size == entry['size']
        return dict(source=entry['source'], url=url, sha256=entry['sha256'], verified=True)
    except Exception as error:
        return dict(source=entry['source'], url=url, error=str(error))

def run(tasks, function):
    results = []
    start = time.time()
    with cf.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(function, task) for task in tasks]
        for future in cf.as_completed(futures):
            results.append(future.result())
            if len(results) % 100 == 0 or len(results) == len(tasks):
                print(json.dumps({'completed': len(results), 'total': len(tasks),
                                  'failures': sum('error' in r for r in results),
                                  'seconds': round(time.time() - start)}), flush=True)
    return results

if len(sys.argv) == 3 and sys.argv[1] == '--verify':
    commit = sys.argv[2]
    assert len(commit) == 40 and all(x in '0123456789abcdef' for x in commit)
    report = json.loads((OUT / 'report.json').read_text())
    results = run(report['photos'], lambda entry: verify(entry, commit))
    failures = [r for r in results if 'error' in r]
    verification = {'commit': commit, 'expected': len(manifest['images']),
                    'verified': len(results) - len(failures), 'failures': failures,
                    'photos': [r for r in results if 'error' not in r]}
    (OUT / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False))
    assert not failures and not report['failures']
else:
    results = run(manifest['images'], prepare)
    successes = [r for r in results if 'error' not in r]
    failures = [r for r in results if 'error' in r]
    report = {'expected': len(manifest['images']), 'completed': len(successes),
              'bytes': sum(r['bytes'] for r in successes), 'photos': successes,
              'failures': failures}
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != 'photos'}), flush=True)
