"""Preserve every Shevanik photo; publish only this explicitly selected new batch."""
import argparse
import concurrent.futures
import hashlib
import io
import json
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageOps

BATCH = 'shevanik-2026-10-19'
REPO = 'Santehsalon-coder/gidvms-tilda-photos'
MAX_BYTES = 35_000_000


def read_image(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 200:
                    raise ValueError('HTTP ' + str(response.status))
                data = response.read(MAX_BYTES + 1)
            if not data or len(data) > MAX_BYTES:
                raise ValueError('Empty or oversized image')
            im = Image.open(io.BytesIO(data))
            im.load()
            if im.format not in ('JPEG', 'PNG', 'WEBP') or min(im.size) < 80:
                raise ValueError('Unexpected image format or dimensions')
            return data, im
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def equivalent(source, candidate):
    other = Image.open(io.BytesIO(candidate))
    other.load()
    return (
        source.size == other.size
        and source.convert('RGBA').tobytes() == other.convert('RGBA').tobytes()
        and ImageOps.exif_transpose(source).convert('RGBA').tobytes()
        == ImageOps.exif_transpose(other).convert('RGBA').tobytes()
        and source.info.get('icc_profile', b'') == other.info.get('icc_profile', b'')
    )


def process(job, root):
    url = job['url']
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in ('rose-sanitary.ru', 'www.rose-sanitary.ru') or not parsed.path.startswith('/upload/') or parsed.query:
        raise ValueError('Unexpected source URL')
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    record = root / 'records' / (key + '.json')
    if record.exists():
        saved = json.loads(record.read_text())
        existing = pathlib.Path(saved['path']).read_bytes()
        if saved['source'] != url or hashlib.sha256(existing).hexdigest() != saved['sha256']:
            raise ValueError('Existing checkpoint does not match')
        Image.open(io.BytesIO(existing)).load()
        return saved
    data, source = read_image(url)
    ext = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[source.format]
    chosen, method = data, 'original'
    if source.format == 'JPEG':
        optimized = subprocess.run(['jpegtran', '-copy', 'all', '-optimize', '-progressive'], input=data, capture_output=True, timeout=45)
        if optimized.returncode == 0 and len(optimized.stdout) < len(chosen) and equivalent(source, optimized.stdout):
            chosen, method = optimized.stdout, 'jpeg-lossless'
    if source.mode in ('RGB', 'RGBA'):
        candidate = io.BytesIO()
        metadata = {k: source.info[k] for k in ('icc_profile', 'exif', 'xmp') if source.info.get(k)}
        source.save(candidate, 'WEBP', lossless=True, exact=True, method=0, **metadata)
        candidate = candidate.getvalue()
        if len(candidate) < len(chosen) and equivalent(source, candidate):
            chosen, ext, method = candidate, 'webp', 'webp-lossless'
    assert len(chosen) <= len(data) and equivalent(source, chosen)
    dest = root / key[:2] / (key + '.' + ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() != chosen:
        raise ValueError('Refusing to replace different existing bytes')
    dest.write_bytes(chosen)
    result = dict(job, source=url, path=str(dest), width=source.width, height=source.height,
                  source_bytes=len(data), bytes=len(chosen), sha256=hashlib.sha256(chosen).hexdigest(),
                  pixels_equal=True, method=method)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(result, ensure_ascii=False))
    return result


def prepare(shard):
    root = pathlib.Path('photos') / BATCH / f'{shard:02}'
    root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((pathlib.Path('batches') / BATCH / f'manifest-{shard:02}.json').read_text())
    jobs = manifest['images']
    assert manifest['shard'] == shard and len(jobs) == 329 and len({j['url'] for j in jobs}) == len(jobs)
    results, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(process, job, root): job for job in jobs}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append({'source': futures[future]['url'], 'error': str(exc)})
            if i % 25 == 0 or i == len(jobs):
                print(f'Shard {shard:02}: {i}/{len(jobs)}; failed {len(failures)}', flush=True)
    report = {'batch': BATCH, 'shard': shard, 'expected': len(jobs), 'completed': len(results),
              'source_bytes': sum(p['source_bytes'] for p in results), 'bytes': sum(p['bytes'] for p in results),
              'failures': failures, 'photos': sorted(results, key=lambda p: (p['external_id'], p['position']))}
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'photos'}), flush=True)
    if report['bytes'] > 1_500_000_000:
        raise SystemExit('Shard too large for a single safe push; split before publishing')
    if failures or len(results) != len(jobs):
        raise SystemExit('Incomplete shard: do not publish')


def verify(shard, commit):
    assert len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
    root = pathlib.Path('photos') / BATCH / f'{shard:02}'
    report = json.loads((root / 'report.json').read_text())
    def check(p):
        url = f'https://raw.githubusercontent.com/{REPO}/{commit}/{p["path"]}'
        data, im = read_image(url)
        assert len(data) == p['bytes'] and hashlib.sha256(data).hexdigest() == p['sha256']
        assert im.size == (p['width'], p['height'])
        return url
    verified, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(check, p): p for p in report['photos']}
        for future in concurrent.futures.as_completed(futures):
            try:
                verified.append(future.result())
            except Exception as exc:
                failures.append({'path': futures[future]['path'], 'error': str(exc)})
    summary = {'shard': shard, 'photo_commit': commit, 'expected': len(report['photos']),
               'verified': len(verified), 'failures': failures}
    (root / 'verification.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    if failures or len(verified) != report['expected']:
        raise SystemExit('Final URL verification failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--shard', type=int, required=True, choices=range(32))
    parser.add_argument('--verify')
    args = parser.parse_args()
    if args.verify:
        verify(args.shard, args.verify)
    else:
        prepare(args.shard)
