"""Capture the first native listing images using one pinned Ready536 generation."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import time
import zlib
from http.client import HTTPException
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

ORIGIN = "https://gidvms.ru"
READY = "https://raw.githubusercontent.com/Santehsalon-coder/gidvms-tilda-photos/main/catalog/ready536/"
CATEGORIES = {
    "dushevye-peregorodki",
    "kuhonnye-moyki",
    "aksessuary",
    "mebel-dlya-vannoy",
    "zerkalnye-shkafy",
    "zerkala",
    "smesiteli-i-dush",
    "smesiteli",
    "dushevye-sistemy",
}
# The storefront populates bath spouts in two passes from accessories and mixers.
# It may replace the entire visible row when the second source arrives.
STAGED = {"/?gc=dushevye-sistemy&gs=izlivy-dlya-vanny": {"180974060633", "389742041783"}}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
CORS = {"Access-Control-Allow-Origin": "*"}
MAX_BYTES = 50_000


def get_json(path):
    return json.loads(path.read_bytes())


def array(value):
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return value if isinstance(value, list) else []


def photo_id(url):
    """Ignore the CDN host alias and resize directive, retain the unique asset path."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "static.tildacdn.com",
        "static.tildacdn.net",
        "static.tildacdn.pub",
        "static.tildacdn.info",
        "thb.tildacdn.com",
        "optim.tildacdn.com",
    }:
        return ""
    return re.sub(r"/-/.*/(?=[^/]+$)", "/", parsed.path)


def read_inventory(root):
    meta = get_json(root / "generation.json")
    stamp = meta["at"]
    if meta["schema"] != 536 or not 0 <= time.time() * 1000 - stamp < 86_400_000:
        raise ValueError("Ready536 generation is expired or in the future")
    galleries, dates = {}, {}
    for part, total in meta["parts"].items():
        if not re.fullmatch(r"[0-9]{12}", part):
            raise ValueError("Unexpected part ID")
        feed = get_json(root / (part + ".json"))
        if (
            feed["schema"] != 536
            or feed["part"] != part
            or feed["total"] != total
            or feed["encoding"] != "deflate-raw-base64-chunks"
            or not 0 <= stamp - feed["at"] < 86_400_000
        ):
            raise ValueError(f"Invalid snapshot {part}")
        dates[part] = feed["at"]
        seen = set()
        for chunk in feed["chunks"]:
            raw = zlib.decompress(base64.b64decode(chunk["data"], validate=True), -15)
            if len(raw) != chunk["size"]:
                raise ValueError(f"Invalid raw snapshot length for {part}")
            for product in json.loads(raw):
                uid = str(product["uid"])
                if uid in seen:
                    raise ValueError(f"Duplicate product {uid} in {part}")
                seen.add(uid)
                gallery = galleries.setdefault(uid, set())
                for image in array(product.get("gallery")) + array(product.get("editions")):
                    if isinstance(image, dict) and image.get("img"):
                        key = photo_id(image["img"])
                        if key:
                            gallery.add(key)
        if len(seen) != total:
            raise ValueError(f"Incomplete snapshot {part}")
    return stamp, dates, galleries


def homepage():
    with urlopen(
        Request(ORIGIN + "/", headers={"User-Agent": "GID-Catalog-Media/1.0"}), timeout=30
    ) as response:
        if response.geturl() != ORIGIN + "/":
            raise ValueError("Unexpected storefront")
        return response.read(6_000_001)


REGISTRY = """() => {
  if (!Array.isArray(window.GVS_CATEGORIES37) || !Array.isArray(window.GVSNav273?.kitchenRows)) return false;
  return {groups: GVS_CATEGORIES37.map(c=>[c.id,c.subs.map(s=>s.id)]),
          kitchen:GVSNav273.kitchenRows.map(r=>[r.gc,r.gs])};
}"""

ROW = """() => {
  const root = document.querySelector('#gvs-category-page');
  const grid = root?.querySelector('.gc10-results > .gc10-grid');
  const notice = root?.querySelector('.gc10-notice')?.innerText || '';
  const count = notice.match(/Найдено товаров:\\s*([0-9\\s]+)/);
  if (!grid?.offsetWidth || root.querySelector('.gc33-loading') || root.classList.contains('gc33-loading')) return null;
  if (!count && !notice.includes('По выбранным условиям товаров нет')) return null;
  const cells = [...grid.children].filter(e=>e.offsetWidth && e.matches('article[data-product-uid]'));
  const n = count ? Number(count[1].replace(/\\s/g,'')) : 0;
  if (cells.length < Math.min(4,n)) return null;
  return {total:n, cards:cells.slice(0,Math.min(4,n)).map(e => {
    const link=e.querySelector('a.gc10-photo'), image=link?.querySelector('img');
    return [e.dataset.productUid, image?.currentSrc || image?.getAttribute('src') || '', link?.href || ''];
  })};
}"""


async def build(args):
    stamp, dates, galleries = read_inventory(args.snapshots)
    html = args.document.read_bytes() if args.document else await asyncio.to_thread(homepage)
    if len(html) > 6_000_000 or b'data-tilda-project-id="25163343"' not in html:
        raise ValueError("Unexpected storefront HTML")
    script_cache = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=args.browser, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        async def new_page(path, width):
            ctx = await browser.new_context(
                viewport={"width": width, "height": 850},
                locale="ru-RU",
                service_workers="block",
                is_mobile=width < 700,
                has_touch=width < 700,
            )
            seen = set()

            async def intercept(route):
                req = route.request
                url = urlsplit(req.url)
                if req.method not in {"GET", "HEAD"}:
                    return await route.abort()
                if url.netloc == "gidvms.ru" and url.path == "/" and req.is_navigation_request():
                    return await route.fulfill(body=html, content_type="text/html; charset=utf-8")
                if req.url.startswith(READY):
                    name = url.path.rsplit("/", 1)[-1]
                    if name == "generation.json" or name.removesuffix(".json") in dates:
                        if name != "generation.json":
                            seen.add(name[:-5])
                        return await route.fulfill(
                            path=args.snapshots / name, content_type="application/json", headers=CORS
                        )
                if req.resource_type in {"image", "font", "media"}:
                    if req.resource_type == "image":
                        return await route.fulfill(body=GIF, content_type="image/gif", headers=CORS)
                    return await route.abort()
                # Only published scripts and styles are needed. Inventory and product data cannot
                # change halfway through the run by falling back to a live Store API request.
                if req.resource_type in {"stylesheet", "script"} and (
                    url.hostname
                    in {
                        "static.tildacdn.com",
                        "static.tildacdn.info",
                        "static.tildacdn.net",
                        "static.tildacdn.pub",
                        "neo.tildaapi.com",
                        "fonts.googleapis.com",
                    }
                ):
                    mime = "text/css" if req.resource_type == "stylesheet" else "application/javascript"
                    if args.static_cache:
                        key = hashlib.sha256(req.url.split("?")[0].encode()).hexdigest()
                        cached = args.static_cache / key
                        if cached.is_file():
                            return await route.fulfill(path=cached, content_type=mime, headers=CORS)
                    if req.url not in script_cache:
                        script_cache[req.url] = asyncio.create_task(
                            asyncio.to_thread(download_static, req.url)
                        )
                    try:
                        body = await script_cache[req.url]
                    except (OSError, HTTPException, ValueError):
                        return await route.abort()
                    return await route.fulfill(body=body, content_type=mime, headers=CORS)
                return await route.abort()

            await ctx.route("**/*", intercept)
            page = await ctx.new_page()
            try:
                await page.goto(ORIGIN + path, wait_until="domcontentloaded", timeout=30_000)
            except BaseException:
                await ctx.close()
                raise
            return ctx, page, seen

        ctx, page, _ = await new_page("/", 1440)
        try:
            registry = await (await page.wait_for_function(REGISTRY, timeout=15_000)).json_value()
        finally:
            await ctx.close()
        groups = dict(registry["groups"])
        if not CATEGORIES.issubset(groups):
            raise ValueError("Missing storefront categories")
        pairs = {(gc, gs) for gc in CATEGORIES for gs in groups[gc]}
        pairs.update(tuple(r) for r in registry["kitchen"])
        if not pairs or any(not SLUG.fullmatch(gc) or not SLUG.fullmatch(gs) for gc, gs in pairs):
            raise ValueError("Unexpected subcategory registry")
        routes = sorted("/?" + urlencode({"gc": gc, "gs": gs}) for gc, gs in pairs)
        if args.route:
            if not set(args.route) <= set(routes):
                raise ValueError("Unknown test route")
            routes = args.route
        gate = asyncio.Semaphore(args.jobs)

        async def observe(path, width):
            ctx, page, seen = await new_page(path, width)
            try:
                deadline = time.monotonic() + 35
                previous = None
                stable_since = 0
                first_uids = None
                while time.monotonic() < deadline:
                    row = await page.evaluate(ROW)
                    parts = {p: dates[p] for p in seen}
                    if row and row["cards"] and first_uids is None:
                        first_uids = [x[0] for x in row["cards"]]
                    checked = bool(parts) and await page.evaluate(
                        """parts=>Object.entries(parts).every(([id,stamp])=>{
                      const s=window.GVS_PRODUCTS13?.category?.(id)?.getSnapshot?.(); return s?.complete && s.at===stamp;
                    })""",
                        parts,
                    )
                    if (
                        row
                        and checked
                        and STAGED.get(path, set()) <= parts.keys()
                        and (row, parts) == previous
                        and time.monotonic() - stable_since > 1.0
                    ):
                        result = []
                        for uid, url, href in row["cards"]:
                            if parse_qs(urlsplit(href).query).get("gp") != [uid] or photo_id(
                                url
                            ) not in galleries.get(uid, set()):
                                raise ValueError(f"Unverified photo or product URL at {path}: {uid}")
                            result.append([uid, url])
                        if first_uids and first_uids != [x[0] for x in result]:
                            print("First row changed during loading:", path, flush=True)
                            return []
                        return result
                    if (row, parts) != previous or not checked:
                        previous = (row, parts)
                        stable_since = time.monotonic()
                    await asyncio.sleep(0.2)
                if path in STAGED and previous and previous[0]:
                    print(
                        "Staged route did not settle, using normal product image loading:",
                        path,
                        flush=True,
                    )
                    return []
                raise ValueError(
                    f"No stable pinned native result: {path} at {width} px. Parts {sorted(seen)}"
                )
            finally:
                await ctx.close()

        async def capture(path):
            pc = await observe(path, 1440)
            phone = await observe(path, 390)
            shared = []
            for (uid, url), (mobile_uid, mobile_url) in zip(pc, phone):
                if uid != mobile_uid or url != mobile_url:
                    break
                shared.append([uid, url])
            if (pc or phone) and not shared:
                print("Native desktop/mobile first photo differs, skipping:", path, flush=True)
            return path, (pc if len(shared) >= min(2, len(pc)) and len(pc) == len(phone) else shared)

        errors = []

        async def capture_safely(path):
            async with gate:
                for _ in range(2):
                    try:
                        result = await capture(path)
                        print(result[0], len(result[1]), flush=True)
                        return result
                    except (BrowserError, OSError, ValueError) as error:
                        print("Rendering failed:", path, str(error).splitlines()[0], flush=True)
                errors.append(path)
                return path, []

        rows = dict(await asyncio.gather(*(capture_safely(path) for path in routes)))
        # Isolated failures fall back to ordinary image loading for those routes.
        # A broader failure leaves the previously published branch untouched.
        if len(errors) > len(routes) // 20:
            raise RuntimeError(f"Could not verify {len(errors)} of {len(routes)} routes")
        await browser.close()
    return stamp, rows


def download_static(url):
    with urlopen(Request(url, headers={"User-Agent": "GID-Catalog-Media/1.0"}), timeout=15) as r:
        if r.geturl() != url:
            raise ValueError("Unexpected redirect")
        return r.read(5_000_000)


def pack(stamp, rows):
    payload = {"schema": 1, "generation": stamp, "pages": rows}
    for max_items in (4, 3, 2):
        payload["pages"] = {k: v[:max_items] for k, v in sorted(rows.items())}
        raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) <= MAX_BYTES and any(payload["pages"].values()):
            return raw
    raise ValueError("No usable compact media manifest")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, default=Path("catalog/ready536"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", default=os.getenv("STOREFRONT_BROWSER"))
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--document", type=Path)
    parser.add_argument("--static-cache", type=Path)
    parser.add_argument("--route", action="append", help="Test a subset locally")
    args = parser.parse_args()
    stamp, rows = asyncio.run(build(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pack(stamp, rows))
    print(f"{len(rows)} subcategories for generation {stamp}, {args.output.stat().st_size} bytes")
