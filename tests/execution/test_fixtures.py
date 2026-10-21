"""Hygiene of tests/fixtures/kalshi_exec: the manifest is complete and honest, and nothing sensitive is committed."""
from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from kpm.execution import units

from .conftest import FIXTURES_DIR, fixture_json

MANIFEST = json.loads((FIXTURES_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
FILES = MANIFEST["files"]
ALL = sorted(p for p in FIXTURES_DIR.rglob("*") if p.is_file())
ALL_NAMES = sorted(p.relative_to(FIXTURES_DIR).as_posix() for p in ALL)


def test_the_manifest_lists_every_fixture_file_and_nothing_else():
    on_disk = {n for n in ALL_NAMES if n != "MANIFEST.json"}
    assert set(FILES) == on_disk, (sorted(set(FILES) - on_disk), sorted(on_disk - set(FILES)))


def test_every_manifest_entry_says_where_it_came_from():
    kinds = {v["kind"] for v in FILES.values()}
    assert kinds == {"public_capture", "spec_example", "synthetic"}
    for name, entry in FILES.items():
        assert entry["source"].strip(), name
        if entry["kind"] != "public_capture":
            assert name.startswith("synthetic/"), f"{name}: only synthetic/ may hold non-captured data"
        else:
            assert not name.startswith("synthetic/"), name
    assert "No file contains a key, key id, signature or account identifier" in MANIFEST["about"]


@pytest.mark.parametrize("name", [n for n in ALL_NAMES if n.endswith(".json")])
def test_every_fixture_is_valid_json(name):
    assert isinstance(fixture_json(name), (dict, list))


@pytest.mark.parametrize("name", ALL_NAMES)
def test_no_fixture_holds_a_credential_or_account_identifier(name):
    text = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    assert not re.search(r"-----BEGIN [A-Z ]*(PRIVATE|PUBLIC) KEY-----", text)
    assert not re.search(r"KALSHI-ACCESS|Authorization|Bearer |X-API|api[_-]?key|private[_-]?key|passw(or)?d", text, re.I)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text), "an e-mail address"
    assert not re.search(r"[A-Za-z0-9+/]{100,}={0,2}", text), "a base64-looking blob (signature or key material)"
    assert not re.search(r"[A-Za-z]:\\\\?Users|/home/\w+|\.pem\b", text), "a local path"


def test_public_captures_hold_market_data_only():
    """Captured public responses never carry account fields (they were fetched without authentication)."""
    for name, entry in FILES.items():
        if entry["kind"] != "public_capture":
            continue
        text = (FIXTURES_DIR / name).read_text(encoding="utf-8")
        for field in ('"user_id"', '"balance"', '"portfolio_value"', '"client_order_id"', '"fill_id"', '"order_id"',
                      '"subaccount'):
            assert field not in text, (name, field)


def test_synthetic_fee_fields_agree_with_the_fee_formula():
    """The synthetic create/fill rows were built from 0.07 x P x (1 - P); keep them consistent with units.taker_fee."""
    filled = fixture_json("synthetic/create_201_ioc_filled.json")
    assert Decimal(filled["average_fee_paid"]) == units.taker_fee(Decimal(1), Decimal(filled["average_fill_price"]))
    for fill in fixture_json("synthetic/fills.json")["fills"]:
        yes, no = Decimal(fill["yes_price_dollars"]), Decimal(fill["no_price_dollars"])
        assert yes + no == 1
        paid = no if fill["outcome_side"] == "no" else yes
        assert Decimal(fill["fee_cost"]) == units.taker_fee(Decimal(fill["count_fp"]), paid)
        assert (fill["outcome_side"], fill["book_side"]) in (("yes", "bid"), ("no", "ask"))     # bid = yes, ask = no


def test_synthetic_settlements_pay_one_dollar_per_winning_contract():
    for row in fixture_json("synthetic/settlements.json")["settlements"]:
        winners = Decimal(row["yes_count_fp"] if row["market_result"] == "yes" else row["no_count_fp"])
        assert row["revenue"] == int(winners * 100) and isinstance(row["revenue"], int)         # integer CENTS


def test_the_real_captures_have_the_properties_the_design_relies_on():
    m = fixture_json("market_KXBTC15M-26OCT051430-30.json")["market"]
    assert m["exchange_index"] == 2 and m["status"] == "active" and m["price_level_structure"] == "tapered_deci_cent"
    book = fixture_json("orderbook_KXBTC15M-26OCT051430-30_full.json")["orderbook_fp"]
    for side in ("yes_dollars", "no_dollars"):
        prices = [Decimal(p) for p, _ in book[side]]
        assert prices == sorted(prices)                                         # ascending: the best bid is LAST
    assert fixture_json("series_KXUSDJPY15M.json")["series"]["exchange_index"] == 0
    assert all(s["exchange_active"] and s["trading_active"] for s in
               fixture_json("exchange_status_prod.json")["exchange_index_statuses"])
