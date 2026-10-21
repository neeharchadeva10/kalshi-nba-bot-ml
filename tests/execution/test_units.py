"""kpm.execution.units: the 100x / wrong-side traps of docs/execution_design.md section 1 (test plan cases 2-4)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kpm.execution import units
from kpm.execution.units import (CENT, CENTICENT, D0, D1, best_bid, best_level, best_no_ask, best_yes_ask, collateral,
                                 dec, fmt_count, fmt_price, from_book, from_cents, from_centicents, on_grid,
                                 outcome_ask, outcome_bid, parse_levels, parse_rfc3339, taker_fee, to_book,
                                 to_centicents, unix_seconds, worst_fee)

from .conftest import fixture_json

D = Decimal


# ------------------------------------------------------------------------------------------------ dec
@pytest.mark.parametrize("text, expected", [
    ("0.0100", D("0.0100")), ("25", D("25")), ("-25.00", D("-25.00")), ("0.010000", D("0.010000")),
    ("2907491.00", D("2907491.00")), ("0", D("0")), ("100", D("100")),
])
def test_dec_parses_plain_decimal_strings_exactly(text, expected):
    got = dec(text)
    assert got == expected and isinstance(got, Decimal)
    assert str(got) == text                              # no float round trip, no reformatting


@pytest.mark.parametrize("bad", [0.01, 1.0, 5, True, None, [], b"1"])
def test_dec_refuses_floats_ints_bools_and_other_types(bad):
    with pytest.raises(TypeError):
        dec(bad)


@pytest.mark.parametrize("bad", ["", " 1", "1 ", "+1", ".5", "5.", "NaN", "Infinity", "-Infinity", "1e3", "1E-2",
                                 "1,000", "0x10", "--1", "1.2.3", "٣", "１２", "$1.00", "1_000"])
def test_dec_refuses_malformed_strings(bad):
    with pytest.raises(ValueError):
        dec(bad)


def test_dec_passes_finite_decimals_and_refuses_nonfinite():
    x = D("1.50")
    assert dec(x) is x
    for bad in (D("NaN"), D("Infinity"), D("-Infinity")):
        with pytest.raises(ValueError):
            dec(bad)


# ------------------------------------------------------------------------------------------------ fmt_price / fmt_count
@pytest.mark.parametrize("price, text", [
    (D("0.01"), "0.0100"), (D("0.5"), "0.5000"), (D("0.56"), "0.5600"), (D("0.9999"), "0.9999"),
    (D("0.0001"), "0.0001"), (D("0.095"), "0.0950"), (D("0.9550"), "0.9550"), (D("0.10"), "0.1000"),
])
def test_fmt_price_is_four_decimal_dollars(price, text):
    assert fmt_price(price) == text


@pytest.mark.parametrize("bad", [D("0"), D("1"), D("50"), D("1.0000"), D("-0.1"), D("1.5"), D("0.12345"),
                                 D("0.00001"), D("100"), D("NaN"), D("Infinity")])
def test_fmt_price_rejects_zero_one_cents_and_excess_precision(bad):
    with pytest.raises(ValueError):
        fmt_price(bad)


@pytest.mark.parametrize("bad", [0.01, 0.5, 1, 50, "0.01", None, True])
def test_fmt_price_rejects_floats_ints_and_strings(bad):
    with pytest.raises(TypeError):
        fmt_price(bad)


@pytest.mark.parametrize("count, text", [
    (D("1"), "1.00"), (D("25"), "25.00"), (D("0.25"), "0.25"), (D("0.01"), "0.01"), (D("10.5"), "10.50"),
    (D("1000"), "1000.00"),
])
def test_fmt_count_is_two_decimal_contracts(count, text):
    assert fmt_count(count) == text


def test_fmt_count_keeps_25_and_0_25_100x_apart():
    a, b = D(fmt_count(D("25"))), D(fmt_count(D("0.25")))
    assert a == 100 * b and fmt_count(D("25")) != fmt_count(D("0.25"))


@pytest.mark.parametrize("bad", [D("0"), D("-1"), D("0.001"), D("1.005"), D("0.009"), D("NaN"), D("1E+40")])
def test_fmt_count_rejects_zero_negative_and_sub_step_counts(bad):
    with pytest.raises(ValueError):
        fmt_count(bad)


@pytest.mark.parametrize("bad", [1.0, 25, "25", None, True])
def test_fmt_count_rejects_floats_ints_and_strings(bad):
    with pytest.raises(TypeError):
        fmt_count(bad)


# ------------------------------------------------------------------------------------------------ to_book / from_book
def test_buying_no_at_one_cent_is_an_ask_at_99_cents():
    """The 99x trap: NO @ 0.01 is side 'ask' at YES price 0.99, never 'ask' at 0.01."""
    assert to_book("no", D("0.01")) == ("ask", D("0.99"))
    assert to_book("yes", D("0.01")) == ("bid", D("0.01"))


@pytest.mark.parametrize("outcome, price, expected", [
    ("yes", D("0.56"), ("bid", D("0.56"))), ("no", D("0.44"), ("ask", D("0.56"))),
    ("yes", D("0.9990"), ("bid", D("0.9990"))), ("no", D("0.0010"), ("ask", D("0.9990"))),
    ("no", D("0.5"), ("ask", D("0.5"))), ("yes", D("0.0001"), ("bid", D("0.0001"))),
])
def test_to_book(outcome, price, expected):
    assert to_book(outcome, price) == expected


@pytest.mark.parametrize("outcome", ["YES", "No", "buy", "bid", "ask", "", None, 1])
def test_to_book_rejects_anything_but_yes_or_no(outcome):
    with pytest.raises(ValueError):
        to_book(outcome, D("0.5"))


@pytest.mark.parametrize("price", [D("0"), D("1"), D("-0.1"), D("1.5"), D("50")])
def test_to_book_rejects_prices_outside_the_open_unit_interval(price):
    for outcome in ("yes", "no"):
        with pytest.raises(ValueError):
            to_book(outcome, price)


@pytest.mark.parametrize("price", [0.5, 1, "0.5", None])
def test_to_book_rejects_non_decimal_prices(price):
    with pytest.raises(TypeError):
        to_book("yes", price)


def test_to_book_and_from_book_round_trip_over_the_whole_grid():
    for outcome in ("yes", "no"):
        for cents in range(1, 100):
            p = D(cents) / 100
            side, yes_price = to_book(outcome, p)
            assert from_book(side, yes_price) == (outcome, p)
            # what the order locks equals what the outcome contracts cost: the invariant behind the caps
            assert collateral(side, yes_price, D(7)) == 7 * p
    for tenth_cent in (1, 5, 10, 999):
        p = D(tenth_cent) / 1000
        assert from_book(*to_book("no", p)) == ("no", p)


def test_from_book_validates():
    assert from_book("bid", D("0.3")) == ("yes", D("0.3"))
    assert from_book("ask", D("0.99")) == ("no", D("0.01"))
    with pytest.raises(ValueError):
        from_book("yes", D("0.3"))
    with pytest.raises(ValueError):
        from_book("bid", D("1"))


# ------------------------------------------------------------------------------------------------ collateral
def test_collateral_of_the_25_contract_test_order():
    assert collateral("ask", D("0.99"), D("25")) == D("0.25")             # buy NO @ 0.01, correctly priced
    assert collateral("ask", D("0.01"), D("25")) == D("24.75")            # the wrong-side order the caps must refuse
    assert collateral("bid", D("0.01"), D("25")) == D("0.25")
    assert collateral("bid", D("0.99"), D("25")) == D("24.75")


def test_collateral_validates_inputs():
    assert collateral("bid", D("0.5"), D("0")) == 0
    for args in (("yes", D("0.5"), D("1")), ("bid", D("0"), D("1")), ("bid", D("1"), D("1")),
                 ("bid", D("0.5"), D("-1"))):
        with pytest.raises(ValueError):
            collateral(*args)
    for args in (("bid", 0.5, D("1")), ("bid", D("0.5"), 1)):
        with pytest.raises(TypeError):
            collateral(*args)


# ------------------------------------------------------------------------------------------------ fees
@pytest.mark.parametrize("count, price, expected", [
    (D(1), D("0.5"), D("0.0175")),
    (D(1), D("0.56"), D("0.017248")),             # the synthetic fixtures use this value
    (D(1), D("0.59"), D("0.016933")),
    (D(25), D("0.01"), D("0.017325")),
    (D(100), D("0.37"), D("1.6317")),
    (D(3), D("0.5"), D("0.0525")),
    (D("0.01"), D("0.5"), D("0.000175")),
    (D(1), D("0.01"), D("0.000693")),
])
def test_taker_fee_exact_values(count, price, expected):
    got = taker_fee(count, price)
    assert got == expected
    assert got == got.quantize(D("0.000001"))        # always a $0.000001 amount


@pytest.mark.parametrize("count, price, raw, rounded_up", [
    (D(1), D("0.123"), D("0.00755097"), D("0.007551")),
    (D("0.01"), D("0.33"), D("0.00015477"), D("0.000155")),
    (D(1), D("0.0001"), D("0.0000069993"), D("0.000007")),
    (D(7), D("0.917"), D("0.03729439"), D("0.037295")),
])
def test_taker_fee_rounds_up_never_down_to_a_micro_dollar(count, price, raw, rounded_up):
    assert D("0.07") * count * price * (1 - price) == raw           # the hand-computed exact fee
    got = taker_fee(count, price)
    assert got == rounded_up
    assert got >= raw > got - D("0.000001")                         # the smallest micro-dollar amount that is >= raw


def test_taker_fee_is_symmetric_in_price_and_scales_with_count_and_multiplier():
    assert taker_fee(D(5), D("0.2")) == taker_fee(D(5), D("0.8"))
    assert taker_fee(D(10), D("0.5")) == 10 * taker_fee(D(1), D("0.5"))
    assert taker_fee(D(1), D("0.5"), D(2)) == 2 * taker_fee(D(1), D("0.5"))
    assert taker_fee(D(1), D("0.5"), D(0)) == 0


@pytest.mark.parametrize("args", [(D(0), D("0.5")), (D(-1), D("0.5")), (D(1), D(0)), (D(1), D(1)), (D(1), D("1.5"))])
def test_taker_fee_rejects_bad_inputs(args):
    with pytest.raises(ValueError):
        taker_fee(*args)


def test_taker_fee_rejects_a_negative_multiplier():
    with pytest.raises(ValueError):
        taker_fee(D(1), D("0.5"), D(-1))


def test_taker_fee_rejects_floats():
    for args in ((1, D("0.5")), (D(1), 0.5), (D(1), D("0.5"), 1.0)):
        with pytest.raises(TypeError):
            taker_fee(*args)


def test_worst_fee_bounds_the_budget_of_the_25_contract_order():
    # design 5.2: 25 contracts at $0.01 -> collateral $0.25, worst case $0.2675
    assert worst_fee(D(25), D("0.01")) == D("0.0175")
    assert collateral("bid", D("0.01"), D(25)) + worst_fee(D(25), D("0.01")) == D("0.2675")
    assert worst_fee(D(1), D("0.5")) == D("0.0176")
    assert worst_fee(D(1), D("0.5"), grid=CENT) == D("0.03")            # a $0.01 balance grid (non-direct member)
    for count, price in ((D(1), D("0.37")), (D("0.25"), D("0.9")), (D(100), D("0.5"))):
        assert worst_fee(count, price) > taker_fee(count, price)
        assert worst_fee(count, price) - taker_fee(count, price) <= 2 * CENTICENT


def test_worst_fee_validates():
    with pytest.raises(ValueError):
        worst_fee(D(1), D("0.5"), grid=D(0))
    with pytest.raises(ValueError):
        worst_fee(D(0), D("0.5"))


# ------------------------------------------------------------------------------------------------ price grids
TAPERED = fixture_json("market_KXBTC15M-26OCT051430-30.json")["market"]["price_ranges"]
LINEAR = fixture_json("markets_KXUSDJPY15M_open.json")["markets"][0]["price_ranges"]


def test_the_captured_grids_are_what_the_design_says():
    assert [(b["start"], b["end"], b["step"]) for b in TAPERED] == [
        ("0.0000", "0.1000", "0.0010"), ("0.1000", "0.9000", "0.0100"), ("0.9000", "1.0000", "0.0010")]
    assert [(b["start"], b["end"], b["step"]) for b in LINEAR] == [("0.0000", "1.0000", "0.0100")]


@pytest.mark.parametrize("price", ["0.0950", "0.10", "0.55", "0.9550", "0.001", "0.0010", "0.999", "0.1", "0.9",
                                   "0.0050", "0.9950", "0.5", "0.01", "0.99", "0.8900", "0.1100"])
def test_tapered_grid_accepts(price):
    assert on_grid(D(price), TAPERED)


@pytest.mark.parametrize("price", ["0.5550", "0.0005", "0.1050", "0.9005", "0.55001", "0.1010", "0.8950", "0.0000",
                                   "1", "1.0000", "0", "-0.01", "1.01"])
def test_tapered_grid_rejects(price):
    assert not on_grid(D(price), TAPERED)


@pytest.mark.parametrize("price, ok", [("0.55", True), ("0.01", True), ("0.99", True), ("0.555", False),
                                       ("0.0150", False), ("0.0950", False), ("0.9550", False), ("0.5", True)])
def test_linear_cent_grid(price, ok):
    assert on_grid(D(price), LINEAR) is ok


def test_zero_and_one_are_never_on_a_grid_and_bounds_are_the_safety_layers_job():
    for grid in (TAPERED, LINEAR):
        assert not on_grid(D("0"), grid) and not on_grid(D("1"), grid)
    # 0.0050 and 0.9950 ARE on the tapered grid. Since 2026-10-06 they also lie inside the 0.001..0.999 bounds of the caps
    # (safety.py), as does every tick of both real grids: the bounds only matter on a grid finer than Kalshi's today
    assert on_grid(D("0.0050"), TAPERED) and on_grid(D("0.9950"), TAPERED)
    assert not on_grid(D("0.0050"), LINEAR) and not on_grid(D("0.9950"), LINEAR)


def test_on_grid_edge_cases():
    assert not on_grid(D("0.5"), [])
    assert not on_grid(D("0.5"), None)
    assert not on_grid(D("0.5"), [{"start": "0", "end": "1", "step": "0"}])           # a zero step is skipped
    assert on_grid(D("0.5"), [{"start": "0.0000", "end": "1.0000", "step": "0.5000"}])
    assert on_grid(D("0.1"), [{"start": "0.0000", "end": "0.1000", "step": "0.0100"},
                              {"start": "0.1000", "end": "0.9000", "step": "0.0500"}])      # boundary in either band
    with pytest.raises(TypeError):
        on_grid(0.5, TAPERED)


# ------------------------------------------------------------------------------------------------ orderbook
DEPTH3 = fixture_json("orderbook_KXBTC15M-26OCT051430-30_depth3.json")["orderbook_fp"]
FULL = fixture_json("orderbook_KXBTC15M-26OCT051430-30_full.json")["orderbook_fp"]
SUBCENT = fixture_json("orderbook_KXBTC15M-26OCT051430-30_full_subcent.json")["orderbook_fp"]
ONE_SIDED = fixture_json("orderbook_demo_one_sided.json")["orderbook_fp"]


def test_the_best_bid_is_the_last_element():
    assert DEPTH3["yes_dollars"][0][0] == "0.5600" and DEPTH3["yes_dollars"][-1][0] == "0.5800"
    assert best_bid(DEPTH3["yes_dollars"]) == D("0.5800")                  # [0] is the WORST level shown
    assert best_bid(DEPTH3["no_dollars"]) == D("0.4100")
    assert best_level(DEPTH3["yes_dollars"]) == (D("0.5800"), D("2685.09"))


def test_best_yes_ask_is_one_minus_the_best_no_bid():
    assert best_yes_ask(DEPTH3["no_dollars"]) == D("0.5900")
    assert best_no_ask(DEPTH3["yes_dollars"]) == D("0.4200")
    assert best_bid(DEPTH3["yes_dollars"]) < best_yes_ask(DEPTH3["no_dollars"])       # an uncrossed book


@pytest.mark.parametrize("book", [FULL, SUBCENT], ids=["full", "full_subcent"])
def test_real_full_books_parse_ascending_and_the_best_is_the_maximum(book):
    for side in ("yes_dollars", "no_dollars"):
        levels = parse_levels(book[side])
        assert len(levels) > 20
        prices = [p for p, _ in levels]
        assert prices == sorted(prices) and len(set(prices)) == len(prices)
        assert all(D0 < p < D1 and s > 0 for p, s in levels)
        assert best_bid(book[side]) == max(prices) == prices[-1]
    yes_bid, no_bid = best_bid(book["yes_dollars"]), best_bid(book["no_dollars"])
    assert yes_bid + no_bid < D1                           # YES bid below YES ask (= 1 - NO bid): uncrossed
    assert best_yes_ask(book["no_dollars"]) == D1 - no_bid and best_no_ask(book["yes_dollars"]) == D1 - yes_bid


def test_the_subcent_book_has_sub_cent_levels_above_ninety_cents():
    prices = [p for p, _ in parse_levels(SUBCENT["yes_dollars"])]
    assert any(p > D("0.9") and p % D("0.01") != 0 for p in prices)


def test_empty_sides_have_no_best_price():
    assert best_bid([]) is None and best_bid(None) is None
    assert best_yes_ask(ONE_SIDED["no_dollars"]) is None                  # NO side empty: there is no YES ask
    assert best_no_ask(ONE_SIDED["yes_dollars"]) == D("0.9900")
    assert best_level([]) is None and best_yes_ask([]) is None and best_no_ask(None) is None


def test_outcome_ask_and_bid_from_a_parsed_book():
    book = {"yes": parse_levels(DEPTH3["yes_dollars"]), "no": parse_levels(DEPTH3["no_dollars"])}
    assert outcome_ask(book, "yes") == (D("0.5900"), D("5367.45"))         # lift the best NO bid (0.4100 x 5367.45)
    assert outcome_ask(book, "no") == (D("0.4200"), D("2685.09"))          # lift the best YES bid (0.5800 x 2685.09)
    assert outcome_bid(book, "yes") == (D("0.5800"), D("2685.09"))
    assert outcome_bid(book, "no") == (D("0.4100"), D("5367.45"))
    assert outcome_ask({"yes": [], "no": []}, "yes") is None and outcome_bid({"yes": []}, "yes") is None
    with pytest.raises(ValueError):
        outcome_ask(book, "bid")
    with pytest.raises(ValueError):
        outcome_bid(book, "")


def test_levels_may_be_already_parsed_decimal_pairs():
    parsed = [(D("0.40"), D("5")), (D("0.45"), D("7"))]
    assert best_bid(parsed) == D("0.45") and best_yes_ask(parsed) == D("0.55")
    assert parse_levels(parsed) == parsed
    assert parse_levels([[D("0.4"), "5.00"]]) == [(D("0.4"), D("5.00"))]


def test_descending_or_duplicate_levels_are_refused_rather_than_guessed():
    descending = [["0.5800", "1.00"], ["0.5700", "1.00"], ["0.5600", "1.00"]]
    for fn in (best_bid, best_yes_ask, best_no_ask, parse_levels, best_level):
        with pytest.raises(ValueError, match="ascending"):
            fn(descending)
    with pytest.raises(ValueError):
        parse_levels([["0.5600", "1.00"], ["0.5600", "2.00"]])


def test_placeholders_and_malformed_levels_are_refused():
    """/markets shows an empty side as 0.0000 x 0.00 or 1.0000 x 0.00; those are never quotes."""
    for bad in ([["0.0000", "0.00"]], [["1.0000", "0.00"]], [["0.5000", "-1.00"]], [["1.5000", "1.00"]],
                [["0.5"]], [["0.5", "1", "2"]], ["0.5"], [[0.5, "1.00"]], [["0.5", 1.0]], [["abc", "1"]]):
        with pytest.raises((ValueError, TypeError)):
            parse_levels(bad)


def test_zero_size_levels_are_dropped_so_the_best_level_is_a_real_one():
    levels = [["0.4000", "5.00"], ["0.4100", "0.00"]]
    assert parse_levels(levels) == [(D("0.4000"), D("5.00"))]
    assert best_bid(levels) == D("0.4000")


# ------------------------------------------------------------------------------------------------ money units
def test_from_cents_and_centicents():
    assert from_cents(2500) == D("25") and from_cents(2500) == D("25.00")
    assert from_cents(1) == D("0.01") and from_cents(0) == 0 and from_cents(-5) == D("-0.05")
    assert from_centicents(200000) == D("20") and from_centicents(10000) == D("1")
    for bad in (25.0, "2500", None, True):
        with pytest.raises(TypeError):
            from_cents(bad)
        with pytest.raises(TypeError):
            from_centicents(bad)


def test_to_centicents():
    assert to_centicents(D("1")) == 10000
    assert to_centicents(D("0.0001")) == 1 and to_centicents(D("5.00")) == 50000 and to_centicents(D("0")) == 0
    for bad in (D("1.00005"), D("0.00001"), D("0.00015")):
        with pytest.raises(ValueError):
            to_centicents(bad)
    with pytest.raises(TypeError):
        to_centicents(1.0)


def test_settlement_cents_become_dollars_without_float_error():
    row = fixture_json("synthetic/settlements.json")["settlements"][0]
    assert from_cents(row["revenue"]) == D("1.00")                       # revenue is integer cents
    assert dec(row["fee_cost"]) == D("0.017248") and dec(row["yes_total_cost_dollars"]) == D("0.5600")


def test_unix_seconds_guard_refuses_milliseconds():
    assert unix_seconds(1_791_223_380) == 1_791_223_380
    for ms in (1_791_223_380_000, 1_703_123_456_789):
        with pytest.raises(ValueError):
            unix_seconds(ms)
    for bad in (999_999_999, 0, -1, 10_000_000_000):
        with pytest.raises(ValueError):
            unix_seconds(bad)
    for bad in (1_791_223_380.0, "1791223380", None, True):
        with pytest.raises(TypeError):
            unix_seconds(bad)


# ------------------------------------------------------------------------------------------------ RFC 3339
def utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("text, expected", [
    ("2026-10-05T18:30:00Z", utc(2026, 10, 5, 18, 30, 0)),                        # 0 fractional digits
    ("2026-10-05T19:01:05.5Z", utc(2026, 10, 5, 19, 1, 5, 500000)),               # 1 digit (settlements fixture)
    ("2026-10-05T18:15:11.9227Z", utc(2026, 10, 5, 18, 15, 11, 922700)),          # 4 digits
    ("2026-10-05T18:15:00.77334Z", utc(2026, 10, 5, 18, 15, 0, 773340)),          # 5 digits (market updated_time)
    ("2026-10-05T17:10:41.988526Z", utc(2026, 10, 5, 17, 10, 41, 988526)),        # 6 digits
    ("2026-10-05T17:10:41.123456789Z", utc(2026, 10, 5, 17, 10, 41, 123456)),     # 9 digits: truncated to micros
    ("2026-10-05T17:10:41.5z", utc(2026, 10, 5, 17, 10, 41, 500000)),             # lower-case zone
    ("2026-10-05t17:10:41Z", utc(2026, 10, 5, 17, 10, 41)),                       # lower-case separator
    ("2026-10-05T14:30:00-04:00", utc(2026, 10, 5, 18, 30, 0)),                   # offsets
    ("2026-10-05T20:30:00+02:00", utc(2026, 10, 5, 18, 30, 0)),
    ("2026-10-05T18:30:00+00:00", utc(2026, 10, 5, 18, 30, 0)),
])
def test_parse_rfc3339(text, expected):
    assert parse_rfc3339(text) == pytest.approx(expected, abs=1e-6)


def test_parse_rfc3339_on_every_captured_timestamp_shape():
    market = fixture_json("market_KXBTC15M-26OCT051430-30.json")["market"]
    assert parse_rfc3339(market["close_time"]) - parse_rfc3339(market["open_time"]) == 15 * 60
    assert parse_rfc3339(market["created_time"]) < parse_rfc3339(market["open_time"])
    assert parse_rfc3339(fixture_json("user_data_timestamp.json")["as_of_time"]) > 1_790_000_000
    assert parse_rfc3339("2026-10-05T19:01:05.5Z") < parse_rfc3339("2026-10-05T19:02:31.123456Z")


@pytest.mark.parametrize("bad", ["2026-10-05T18:30:00", "2026-10-05", "18:30:00Z", "", "not a date", "2026-13-05T18:30:00Z",
                                 "2026-10-05T25:30:00Z", "2026-10-05T18:30:00.Z", "2026-10-05T18:30:00 UTC",
                                 "２０２６-10-05T18:30:00Z", "1791223380"])
def test_parse_rfc3339_rejects_zoneless_or_malformed(bad):
    with pytest.raises(ValueError):
        parse_rfc3339(bad)


@pytest.mark.parametrize("bad", [None, 1791223380, 1.5, b"2026-10-05T18:30:00Z"])
def test_parse_rfc3339_rejects_non_strings(bad):
    with pytest.raises(TypeError):
        parse_rfc3339(bad)


# ------------------------------------------------------------------------------------------------ module surface
def test_the_contract_surface_exists():
    for name in ("dec", "fmt_price", "fmt_count", "to_book", "collateral", "taker_fee", "worst_fee", "on_grid",
                 "best_bid", "best_yes_ask", "best_no_ask", "from_cents", "unix_seconds", "parse_rfc3339"):
        assert callable(getattr(units, name)), name
