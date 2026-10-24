"""ExecClient.server_time_skew(): the median of 3 Date-header probes the live clock-skew gate relies on."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from kpm.execution.client import (CLOCK_ENDPOINT, CLOCK_PROBE_SPACING_S, CLOCK_PROBES, ApiError, ClockProbe,
                                  ClockSample, ExecClient)

from .conftest import CLOCK_START, FakeTransport, kalshi_date, respond

N = 1_791_226_200                      # a whole second on the fake clock


@pytest.fixture
def probe(make_client, watch_env, fake_clock):
    """``probe(skew=..., latency=...)`` -> (client, transport): the exchange clock runs ``skew`` s BEHIND the local clock."""
    def build(skew: float = 0.0, latency: float = 0.0, env=None):
        t = FakeTransport(clock=fake_clock, server_clock=lambda: fake_clock.time() - skew, latency_s=latency)
        t.get("/exchange/status", {"exchange_active": True})
        return make_client(t, env if env is not None else watch_env), t

    return build


# ================================================================================================ accuracy
@pytest.mark.parametrize("true_skew", [0.0, 0.3, 1.7, 2.4, -3.2, 12.5, -45.0])
@pytest.mark.parametrize("phase", [0.0, 0.25, 0.5, 0.75, 0.999])
def test_the_estimate_is_within_the_date_resolution_of_the_true_offset(probe, fake_clock, true_skew, phase):
    """Date has 1 s resolution; centring it keeps the error within +-0.5 s (plus round-trip asymmetry, none here)."""
    fake_clock.now = N + phase
    c, _ = probe(skew=true_skew, latency=0.06)
    assert abs(c.server_time_skew() - true_skew) <= 0.5 + 1e-6


def test_the_sign_is_local_minus_server(probe, fake_clock):
    fake_clock.now = N + 0.5
    ahead, _ = probe(skew=+5.0)                       # the PC clock is 5 s ahead of the exchange: positive
    assert ahead.server_time_skew() == pytest.approx(5.0, abs=0.51) and ahead.server_time_skew() > 4
    behind, _ = probe(skew=-5.0)
    assert behind.server_time_skew() < -4


def test_the_return_value_is_a_plain_float_equal_to_the_probes_median(probe, fake_clock):
    fake_clock.now = N + 0.2
    c, _ = probe(skew=1.0)
    skew = c.server_time_skew()
    assert type(skew) is float
    p = c.clock_probe()
    assert isinstance(p, ClockProbe) and type(p.skew_s) is float and len(p.samples) == CLOCK_PROBES == 3


def test_the_synced_clock_passes_a_2_second_gate_and_a_5_second_error_fails_it(probe, fake_clock):
    fake_clock.now = N + 0.4
    for good in (0.0, 0.4, -1.2, 1.4):
        c, _ = probe(skew=good, latency=0.12)
        assert abs(c.server_time_skew()) <= 2.0
    for bad in (5.0, -5.0, 30.0):
        c, _ = probe(skew=bad, latency=0.12)
        assert abs(c.server_time_skew()) > 2.0


def test_the_midpoint_of_the_round_trip_is_the_local_reference(probe, fake_clock):
    """With an 0.8 s round trip the server stamped Date at the midpoint. Using the send or the receive time instead
    would be off by 0.4 s; the midpoint gives the exact answer (single probe, Date second boundary aligned)."""
    fake_clock.now = N + 2.1                          # midpoint = N + 2.5 ; server (2.0 s behind) = N + 0.5
    c, _ = probe(skew=2.0, latency=0.8)
    p = c.clock_probe(probes=1, spacing_s=0)
    assert p.samples[0].skew_s == pytest.approx(2.0, abs=1e-3)
    assert p.samples[0].rtt_s == pytest.approx(0.8, abs=1e-6)


@pytest.mark.parametrize("phase, expected", [(0.0, -0.5), (0.25, -0.25), (0.5, 0.0), (0.75, 0.25), (0.999, 0.499)])
def test_the_date_header_is_centred_on_its_one_second_window(probe, fake_clock, phase, expected):
    """Identical clocks: Date is the floor of the shared time, so the unbiased estimate is phase - 0.5."""
    fake_clock.now = N + phase
    c, _ = probe(skew=0.0)
    assert c.clock_probe(probes=1, spacing_s=0).skew_s == pytest.approx(expected, abs=1e-6)


# ================================================================================================ the probes themselves
def test_three_unsigned_probes_of_the_status_endpoint_spaced_just_over_the_cache_lifetime(probe, fake_clock, watch_env):
    c, t = probe()
    c.server_time_skew()
    assert [call.endpoint for call in t.calls] == [CLOCK_ENDPOINT] * 3 == ["/exchange/status"] * 3
    assert not any(call.signed for call in t.calls) and all(call.method == "GET" for call in t.calls)
    assert fake_clock.sleeps == [CLOCK_PROBE_SPACING_S] * 2 == [1.1, 1.1]       # /exchange/status is cached for 1 s
    assert CLOCK_PROBE_SPACING_S > 1.0


def test_the_probe_never_touches_the_15_second_cached_markets_list(probe):
    c, t = probe()
    c.server_time_skew()
    assert not any("/markets" in call.endpoint for call in t.calls)


def test_the_probe_works_without_keys_and_with_keys_alike(probe, prod_env, fake_clock):
    for env in (None, prod_env):
        c, t = probe(env=env)
        c.server_time_skew()
        assert len(t.calls) == 3 and not any(call.signed for call in t.calls)


def test_custom_probe_counts_and_spacing(probe, fake_clock):
    c, t = probe()
    c.server_time_skew(probes=5, spacing_s=2.5)
    assert len(t.calls) == 5 and fake_clock.sleeps == [2.5] * 4
    c2, t2 = probe()
    fake_clock.sleeps.clear()
    c2.server_time_skew(probes=1)
    assert len(t2.calls) == 1 and fake_clock.sleeps == []


@pytest.mark.parametrize("kw", [{"probes": 0}, {"probes": 10}, {"probes": True}, {"probes": "3"}, {"probes": 2.0},
                                {"spacing_s": -1}, {"spacing_s": 31}, {"spacing_s": True}, {"spacing_s": None},
                                {"spacing_s": "1"}])
def test_probe_parameters_are_validated_before_any_request(probe, kw):
    c, t = probe()
    with pytest.raises(ValueError):
        c.clock_probe(**kw)
    assert t.calls == []


def test_samples_carry_the_rtt_the_date_and_the_cached_flag(probe, fake_clock):
    fake_clock.now = N + 0.5
    c, _ = probe(skew=1.0, latency=0.2)
    p = c.clock_probe()
    assert all(isinstance(s, ClockSample) and s.cached is False for s in p.samples)
    assert all(s.rtt_s == pytest.approx(0.2, abs=1e-6) for s in p.samples)
    assert all(s.server_date.endswith(" GMT") and s.server_date.startswith(("Mon", "Tue")) for s in p.samples)
    assert p.spread_s == pytest.approx(max(s.skew_s for s in p.samples) - min(s.skew_s for s in p.samples), abs=1e-9)
    assert p.skew_s == sorted(s.skew_s for s in p.samples)[1]                   # the median of three


# ================================================================================================ robustness
def scripted(make_client, watch_env, fake_clock, *headers_per_probe, skew=0.0):
    """A transport whose n-th reply carries headers_per_probe[n] (a callable of the server time, or a dict)."""
    t = FakeTransport(clock=fake_clock)
    replies = []
    for h in headers_per_probe:
        def reply(call, h=h):
            hdr = h(fake_clock.time() - skew) if callable(h) else dict(h)
            return respond(200, json={}, headers=hdr)
        replies.append(reply)
    t.get("/exchange/status", *replies)
    return make_client(t, watch_env), t


def date_of(offset=0.0):
    return lambda server_now: {"Date": kalshi_date(server_now + offset)}


def test_one_wild_probe_does_not_move_the_median(make_client, watch_env, fake_clock):
    fake_clock.now = N + 0.5
    c, _ = scripted(make_client, watch_env, fake_clock, date_of(), date_of(+3600), date_of(), skew=1.0)
    assert c.server_time_skew() == pytest.approx(1.0, abs=0.7)                     # not an hour off
    c2, _ = scripted(make_client, watch_env, fake_clock, date_of(-3600), date_of(), date_of(), skew=-2.0)
    assert c2.server_time_skew() == pytest.approx(-2.0, abs=0.7)


def test_an_age_header_is_added_to_the_date_and_the_sample_is_marked_cached(make_client, watch_env, fake_clock):
    """A response served from a cache 4 s after it was generated: Date is 4 s old, Age says so."""
    fake_clock.now = N + 0.3
    stale = lambda now: {"Date": kalshi_date(now - 4), "Age": "4"}                  # noqa: E731
    c, _ = scripted(make_client, watch_env, fake_clock, stale, stale, stale)
    p = c.clock_probe()
    assert all(s.cached for s in p.samples)
    assert abs(p.skew_s) <= 0.5 + 1e-6                                              # Age compensated: the true skew is 0
    ignoring_age = lambda now: {"Date": kalshi_date(now - 4)}                       # noqa: E731
    c2, _ = scripted(make_client, watch_env, fake_clock, ignoring_age, ignoring_age, ignoring_age)
    assert c2.clock_probe().skew_s > 3                                              # without Age it looks 4 s ahead


def test_the_kalshi_cache_hit_header_flags_a_sample_without_changing_it(make_client, watch_env, fake_clock):
    fake_clock.now = N + 0.5
    c, _ = scripted(make_client, watch_env, fake_clock,
                    lambda now: {"Date": kalshi_date(now), "x-kalshi-cache-hits": "1"},
                    lambda now: {"Date": kalshi_date(now), "x-kalshi-cache-hits": "0"},
                    lambda now: {"Date": kalshi_date(now)})
    p = c.clock_probe()
    assert [s.cached for s in p.samples] == [True, False, False]


@pytest.mark.parametrize("bad_age", ["", "abc", "-5", "1.5", "0"])
def test_a_junk_age_header_counts_as_zero(make_client, watch_env, fake_clock, bad_age):
    fake_clock.now = N + 0.5
    junk = lambda now: {"Date": kalshi_date(now), "Age": bad_age}                    # noqa: E731
    c, _ = scripted(make_client, watch_env, fake_clock, junk, junk, junk)
    assert abs(c.server_time_skew()) <= 0.5 + 1e-6


def test_probes_without_a_usable_date_are_dropped(make_client, watch_env, fake_clock):
    fake_clock.now = N + 0.5
    c, _ = scripted(make_client, watch_env, fake_clock, {}, date_of(), date_of(), skew=0.0)
    p = c.clock_probe()
    assert len(p.samples) == 2 and abs(p.skew_s) <= 0.5 + 1e-6
    c2, _ = scripted(make_client, watch_env, fake_clock, {"Date": "not a date"}, date_of(), date_of())
    assert len(c2.clock_probe().samples) == 2
    c3, _ = scripted(make_client, watch_env, fake_clock, {"Date": ""}, {"Date": "Mon, 99 Foo 2026 99:99:99 GMT"}, date_of())
    with pytest.raises(ApiError, match=r"1 of 3 probes usable"):
        c3.clock_probe()


def test_fewer_than_two_usable_probes_is_no_answer_so_the_gate_fails_closed(make_client, watch_env, fake_clock):
    c, _ = scripted(make_client, watch_env, fake_clock, {}, {}, date_of())
    with pytest.raises(ApiError, match="clock skew unavailable"):
        c.server_time_skew()
    c1, _ = scripted(make_client, watch_env, fake_clock, {})
    with pytest.raises(ApiError, match="no usable Date header"):
        c1.clock_probe(probes=1, spacing_s=0)
    c2, _ = scripted(make_client, watch_env, fake_clock, date_of())
    assert c2.clock_probe(probes=1, spacing_s=0).samples                              # one is enough when one was asked


def test_a_date_with_a_numeric_offset_is_read_correctly(make_client, watch_env, fake_clock):
    fake_clock.now = datetime(2026, 10, 5, 18, 50, 12, 500000, tzinfo=timezone.utc).timestamp()
    c, _ = scripted(make_client, watch_env, fake_clock,
                    {"Date": "Mon, 05 Oct 2026 13:50:12 -0500"}, {"Date": "Mon, 05 Oct 2026 18:50:12 +0000"},
                    {"Date": "Mon, 05 Oct 2026 18:50:12 GMT"})
    p = c.clock_probe(spacing_s=0)
    assert [round(s.skew_s, 3) for s in p.samples] == [0.0, 0.0, 0.0]


def test_the_real_captured_date_format_parses(make_client, watch_env, fake_clock):
    """Dates exactly as the exchange sends them (prod_orderbook_KXBTC15M.headers.txt, 2026-10-05)."""
    fake_clock.now = datetime(2026, 10, 5, 18, 5, 50, 500000, tzinfo=timezone.utc).timestamp()
    c, _ = scripted(make_client, watch_env, fake_clock, *[{"Date": "Mon, 05 Oct 2026 18:05:50 GMT"}] * 3)
    assert c.clock_probe(spacing_s=0).skew_s == pytest.approx(0.0, abs=1e-6)


def test_failed_probes_are_dropped_after_their_read_retries(make_client, watch_env, fake_clock):
    fake_clock.now = N + 0.5
    ok = lambda call: respond(200, json={}, headers={"Date": kalshi_date(fake_clock.time())})        # noqa: E731
    t = FakeTransport(clock=fake_clock)
    t.get("/exchange/status", *[requests.ConnectionError("down")] * 5, ok, ok)
    c = make_client(t, watch_env)
    p = c.clock_probe()
    assert len(p.samples) == 2 and abs(p.skew_s) <= 0.5 + 1e-6
    assert len(t.calls) == 5 + 2 and 0.5 in fake_clock.sleeps and 4.0 in fake_clock.sleeps   # the backoff schedule ran


def test_all_probes_failing_raises_with_the_reason(make_client, watch_env, fake_clock):
    t = FakeTransport(clock=fake_clock)
    t.get("/exchange/status", requests.ConnectionError("down"))
    with pytest.raises(ApiError, match=r"0 of 3 probes usable \(TransportError\)"):
        make_client(t, watch_env).server_time_skew()
    t2 = FakeTransport(clock=fake_clock)
    t2.get("/exchange/status", status=403, text='{"error": {"code": "forbidden", "message": "no"}}')
    with pytest.raises(ApiError, match="clock skew unavailable"):
        make_client(t2, watch_env).server_time_skew()


def test_rate_limited_probes_are_retried_like_any_read(make_client, watch_env, fake_clock):
    fake_clock.now = N + 0.5
    ok = lambda call: respond(200, json={}, headers={"Date": kalshi_date(fake_clock.time())})        # noqa: E731
    t = FakeTransport(clock=fake_clock)
    t.get("/exchange/status", 429, ok, ok, ok)
    c = make_client(t, watch_env)
    assert abs(c.server_time_skew()) <= 0.5 + 1e-6 and len(t.calls) == 4


def test_a_transport_without_response_headers_gives_no_answer(make_client, watch_env, fake_clock):
    class Bare:
        status_code, text = 200, "{}"

    class BareTransport:
        def send(self, method, url, *, params, data, headers, timeout):
            return Bare()

    c = ExecClient(watch_env, transport=BareTransport(), clock=fake_clock.time, sleep=fake_clock.sleep)
    with pytest.raises(ApiError, match="clock skew unavailable"):
        c.server_time_skew()
    assert c.exchange_status() == {}                                                  # everything else still works


def test_plain_dict_headers_work_with_any_capitalisation(make_client, watch_env, fake_clock):
    fake_clock.now = N + 0.5

    class Plain:
        status_code, text = 200, "{}"
        headers = {"DATE": kalshi_date(N + 0.5), "aGe": "0"}

    class T:
        def send(self, method, url, *, params, data, headers, timeout):
            return Plain()

    c = ExecClient(watch_env, transport=T(), clock=fake_clock.time, sleep=fake_clock.sleep)
    assert abs(c.clock_probe(probes=1, spacing_s=0).skew_s) <= 0.5 + 1e-6       # the static Date is only right once


def test_only_whitelisted_response_headers_are_kept(make_client, watch_env, fake_clock):
    """The client never keeps other response headers (Set-Cookie, auth echoes...), only Date / Age / cache hits."""
    from kpm.execution.client import _reply_headers

    class R:
        headers = {"Date": "d", "Age": "1", "X-Kalshi-Cache-Hits": "2", "Set-Cookie": "s=1", "Authorization": "x",
                   "KALSHI-ACCESS-SIGNATURE": "sig"}

    assert _reply_headers(R()) == {"date": "d", "age": "1", "x-kalshi-cache-hits": "2"}
    assert _reply_headers(object()) == {} and _reply_headers(type("E", (), {"headers": None})()) == {}


def test_the_default_constants_match_the_design(probe):
    assert CLOCK_PROBES == 3 and CLOCK_ENDPOINT == "/exchange/status" and CLOCK_START > 1_700_000_000
    c, _ = probe()
    import inspect
    sig = inspect.signature(c.server_time_skew)
    assert sig.parameters["probes"].default == 3 and sig.parameters["spacing_s"].default == 1.1
