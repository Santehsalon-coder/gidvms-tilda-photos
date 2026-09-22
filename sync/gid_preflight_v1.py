"""GID/Tilda v1: preflight and optional authentication check; NEVER writes products."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FEED = "https://gid.com.ru/e/share/yandex_turbo"
CATALOGUE = "https://store.tildaapi.com/api/getproductslist/"
CONNECTOR = "https://store.tilda.ru/connectors/commerceml/"
PROJECT_ID = "25163343"
SAMPLE = {"sku": "L070BMGD", "external_id": "GID-L070BMGD", "uid": "986190510523"}
REPORTS = Path("reports")


class CheckFailed(Exception):
    """A safe, non-sensitive error message."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckFailed("Redirect rejected; no credentials were forwarded.")


def get_bytes(url: str, *, auth: str | None = None, limit: int = 20000000) -> bytes:
    """Only the fixed supplier, public catalogue, or checkauth endpoint may be read."""
    p = urllib.parse.urlsplit(url)
    target = (p.scheme, p.netloc, p.path)
    permitted = {
        ("https", "gid.com.ru", "/e/share/yandex_turbo"),
        ("https", "store.tildaapi.com", "/api/getproductslist/"),
        ("https", "store.tilda.ru", "/connectors/commerceml/"),
    }
    if target not in permitted:
        raise CheckFailed("Network target is not allowed.")
    if p.netloc == "store.tilda.ru":
        if urllib.parse.parse_qs(p.query) != {"type": ["catalog"], "mode": ["checkauth"]}:
            raise CheckFailed("Only the non-import authentication check is allowed.")
    elif auth is not None:
        raise CheckFailed("Credentials cannot be sent to the supplier or public catalogue.")
    headers = {"User-Agent": "GidVMS-preflight/1.0", "Cache-Control": "no-cache"}
    if auth is not None:
        headers["Authorization"] = auth
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=35) as response:
            if response.status != 200:
                raise CheckFailed("Unexpected HTTP status.")
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise CheckFailed(f"HTTP {exc.code}; response body not logged.") from None
    except (urllib.error.URLError, TimeoutError):
        raise CheckFailed("Source could not be read; no product data was changed.") from None
    if len(data) > limit:
        raise CheckFailed("Response exceeded the configured size limit.")
    return data


def price(value: object) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise CheckFailed("Invalid retail price.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("10000000"):
        raise CheckFailed("Retail price is outside the allowed range.")
    if amount != amount.quantize(Decimal("0.01")):
        raise CheckFailed("Retail price has excessive decimal precision.")
    return amount


def quantity(value: object) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,7}", text):
        raise CheckFailed("Quantity must be an explicit non-negative integer.")
    return int(text)


def parse_feed(data: bytes) -> dict:
    # This YML feed includes the standard shops.dtd declaration. Strip that
    # exact declaration without fetching it; reject all other DTDs/entities.
    data = data.replace(b'<!DOCTYPE yml_catalog SYSTEM "shops.dtd">', b"", 1)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise CheckFailed("XML with entity declarations is not accepted.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise CheckFailed("Supplier returned invalid XML.") from None
    if root.tag != "yml_catalog":
        raise CheckFailed("Unexpected supplier XML format.")
    offers = root.findall("./shop/offers/offer")
    matches = [o for o in offers if (o.findtext("vendorCode") or "").strip() == SAMPLE["sku"]]
    if len(matches) != 1:
        raise CheckFailed("Sample SKU is absent or duplicated in the supplier XML.")
    offer = matches[0]
    if (offer.findtext("vendor") or "").strip().upper() != "GID":
        raise CheckFailed("Unexpected sample brand.")
    if (offer.findtext("currencyId") or "").strip() != "RUB":
        raise CheckFailed("Unexpected retail price currency.")
    amount = price(offer.findtext("price"))  # NEVER use price_opt.
    count = quantity(offer.findtext("count"))
    if offer.get("available") not in ("true", "false"):
        raise CheckFailed("Availability flag is missing or invalid.")
    if (offer.get("available") == "true") != (count > 0):
        raise CheckFailed("Availability and supplier quantity disagree.")
    return {
        "feed_date_text": root.get("date", ""),
        "feed_date_timezone_confirmed": False,
        "offers_count": len(offers),
        "sample_offer_id": offer.get("id"),
        "sku": SAMPLE["sku"],
        "price": format(amount, ".2f"),
        "quantity": count,
    }


def catalogue() -> list[dict]:
    token = str(time.time_ns())
    def page(n: int) -> dict:
        params = {
            "storepartuid": "724727657493", "recid": "3742006901",
            "size": "100", "slice": str(n), "sort[created]": "desc", "_gvs": token,
        }
        data = json.loads(get_bytes(CATALOGUE + "?" + urllib.parse.urlencode(params)))
        if not isinstance(data.get("products"), list):
            raise CheckFailed("Catalogue response has no products.")
        return data
    first = page(1)
    total = int(first["total"])
    if not 1 <= total <= 50000:
        raise CheckFailed("Catalogue total is invalid.")
    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = [first, *pool.map(page, range(2, (total + 99) // 100 + 1))]
    items = [item for batch in pages for item in batch["products"]]
    if any(int(batch["total"]) != total for batch in pages):
        raise CheckFailed("Catalogue changed during pagination; retry the preflight.")
    if len(items) != total or len({str(p["uid"]) for p in items}) != total:
        raise CheckFailed("Catalogue is incomplete or contains repeated identifiers.")
    return items


def match_product(items: list[dict]) -> dict:
    found = [p for p in items if (
        str(p.get("uid")) == SAMPLE["uid"]
        or str(p.get("externalid", "")) == SAMPLE["external_id"]
        or str(p.get("sku", "")) == SAMPLE["sku"]
    )]
    if len(found) != 1:
        raise CheckFailed("Tilda identifiers do not resolve to exactly one product.")
    p = found[0]
    if (str(p.get("uid")), p.get("externalid"), p.get("sku"), str(p.get("brand", "")).upper()) != (
        SAMPLE["uid"], SAMPLE["external_id"], SAMPLE["sku"], "GID"
    ):
        raise CheckFailed("Tilda SKU, external ID, UID, or brand changed.")
    editions = p.get("editions") or []
    if len(editions) > 1 or (editions and str(editions[0].get("uid")) != SAMPLE["uid"]):
        raise CheckFailed("A product with multiple variants cannot be the first test.")
    price(p.get("price"))
    quantity(p.get("quantity"))
    return p


def write_report(name: str, data: dict) -> None:
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def preview() -> None:
    supplier = parse_feed(get_bytes(FEED))
    items = catalogue()
    current = match_product(items)
    before = {"price": format(price(current["price"]), ".2f"), "quantity": quantity(current["quantity"])}
    after = {"price": supplier["price"], "quantity": supplier["quantity"]}
    protected = {k: v for k, v in current.items() if k not in ("price", "quantity", "editions")}
    report = {
        "mode": "preview-only", "store_modified": False, "scheduled": False,
        "read_at_utc": datetime.now(timezone.utc).isoformat(),
        "secret_verified_by_login": False, "catalogue_total": len(items),
        "sample": SAMPLE, "title": current["title"], "supplier": supplier,
        "before": before, "proposed_after": after,
        "changed_fields": [k for k in before if before[k] != after[k]],
        "protected_fields_sha256": hashlib.sha256(
            json.dumps(protected, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "notes": [
            "Only this one imported sample is inspected; the other 470 are not update targets yet.",
            "The 21 pre-existing cards are outside this sample.",
            "Supplier quantity is not a reservation; availability requires confirmation before payment.",
            "No CommerceML import/init/file commands and no order queries were sent.",
            "Supplier date timezone and stale-feed threshold must be settled before scheduling.",
        ],
    }
    write_report("preview.json", report)
    text = (
        "# GID / Tilda: проверка без изменений\n\n"
        "Карточки Tilda не изменялись. Расписание не включено.\n\n"
        f"Тестовый артикул: `{SAMPLE['sku']}`.\n\n"
        "| Поле | Сейчас в Tilda | В свежем XML GID |\n"
        "| --- | ---: | ---: |\n"
        f"| Розничная цена, ₽ | {before['price']} | {after['price']} |\n"
        f"| Количество | {before['quantity']} | {after['quantity']} |\n\n"
        "Артикул, внешний код и системный идентификатор однозначно сопоставлены.\n"
        "Пароль в этой проверке не отправлялся в Tilda; правильность пароля ещё не проверена.\n"
    )
    (REPORTS / "summary.md").write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    print("Preflight complete; one product inspected; zero product updates.")


def check_auth() -> None:
    """Manual handshake only. No init/file/import calls and no order export."""
    if os.environ.get("TILDA_AUTH_CHECK_APPROVED") != "YES":
        raise CheckFailed("Manual approval for a connection check is required.")
    secret = os.environ.pop("TILDA_COMMERCEML_PASSWORD", "")
    if not secret or secret != secret.strip() or "\n" in secret or "\r" in secret:
        raise CheckFailed("Secret is absent or includes whitespace; value not logged.")
    token = base64.b64encode(f"{PROJECT_ID}:{secret}".encode()).decode()
    raw = get_bytes(CONNECTOR + "?type=catalog&mode=checkauth", auth="Basic " + token, limit=65536)
    del token, secret
    lines = raw.decode("utf-8-sig", errors="replace").strip().splitlines()
    passed = len(lines) >= 3 and lines[0].strip().lower() == "success"
    del raw, lines
    write_report("connection.json", {
        "authentication_passed": passed, "store_modified": False,
        "orders_accessed": False, "import_commands_sent": 0,
    })
    if not passed:
        raise CheckFailed("Connection was not confirmed; check enabled state and saved connector password.")
    print("Connection confirmed. No product updates and no orders accessed.")


def self_test() -> None:
    class ValidationTests(unittest.TestCase):
        def fixture(self):
            return b'<yml_catalog><shop><offers><offer id="1" available="true"><vendorCode>L070BMGD</vendorCode><vendor>Gid</vendor><price>13500</price><price_opt>9300</price_opt><count>7</count><currencyId>RUB</currencyId></offer></offers></shop></yml_catalog>'
        def test_retail_not_wholesale(self):
            self.assertEqual(parse_feed(self.fixture())["price"], "13500.00")
        def test_zero_quantity(self):
            self.assertEqual(quantity("0"), 0)
        def test_missing_quantity(self):
            with self.assertRaises(CheckFailed): quantity("")
        def test_negative_quantity(self):
            with self.assertRaises(CheckFailed): quantity("-1")
        def test_invalid_price(self):
            for value in ["0", "NaN", "-2", "Infinity", "hello"]:
                with self.assertRaises(CheckFailed): price(value)
        def test_availability_conflict(self):
            with self.assertRaises(CheckFailed):
                parse_feed(self.fixture().replace(b'available="true"', b'available="false"'))
        def test_duplicate_supplier_sku(self):
            data = self.fixture()
            offer = re.search(b"<offer .*?</offer>", data).group()
            with self.assertRaises(CheckFailed): parse_feed(data.replace(offer, offer + offer))
        def test_wrong_currency(self):
            with self.assertRaises(CheckFailed): parse_feed(self.fixture().replace(b"RUB", b"USD"))
        def test_standard_yml_doctype(self):
            self.assertEqual(parse_feed(b'<!DOCTYPE yml_catalog SYSTEM "shops.dtd">' + self.fixture())["quantity"], 7)
        def test_entity_decl(self):
            with self.assertRaises(CheckFailed): parse_feed(b"<!DOCTYPE test>" + self.fixture())
        def test_duplicate_store_mapping(self):
            p = {"uid": SAMPLE["uid"], "sku": SAMPLE["sku"], "externalid": SAMPLE["external_id"]}
            with self.assertRaises(CheckFailed): match_product([p, dict(p)])
        def test_forbidden_network_target(self):
            with self.assertRaises(CheckFailed): get_bytes("https://example.com/")
        def test_forbidden_import(self):
            with self.assertRaises(CheckFailed): get_bytes(CONNECTOR + "?type=catalog&mode=import")
        def test_forbidden_credentials_destination(self):
            with self.assertRaises(CheckFailed): get_bytes(FEED, auth="not-a-real-secret")
        def test_auth_requires_approval(self):
            saved = os.environ.pop("TILDA_AUTH_CHECK_APPROVED", None)
            try:
                with self.assertRaises(CheckFailed): check_auth()
            finally:
                if saved is not None: os.environ["TILDA_AUTH_CHECK_APPROVED"] = saved
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ValidationTests))
    if not result.wasSuccessful(): raise CheckFailed("Local safety tests failed.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preview", "check-auth", "self-test"))
    args = parser.parse_args()
    try:
        {"preview": preview, "check-auth": check_auth, "self-test": self_test}[args.mode]()
    except CheckFailed as exc:
        write_report("error.json", {"message": str(exc), "store_modified": False})
        print("Check stopped:", str(exc))
        return 1
    except Exception:
        write_report("error.json", {"message": "Unexpected error; details suppressed for safety.", "store_modified": False})
        print("Unexpected error; no updates were sent. Details suppressed for safety.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
