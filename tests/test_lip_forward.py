import pytest

from kpm.analysis.lip_forward import estimate_program_reward, rule_c_share, rule_l_share


def test_no_discount_all_inside():
    # target 1000 -> ref where cum >= 200: 110 at .50, 410 at .49 -> ref .49 ; everything weight 1
    s, ex = rule_c_share([(0.50, 100), (0.49, 300)], 1000, 0.5, 10, 0.50)
    assert s == pytest.approx(10 / 410)
    assert ex is True   # depth 410 < 1000


def test_discount_below_reference():
    # ref .50 (cum 310 >= 200); the .48 level is 2 ticks below -> weight .25
    s, ex = rule_c_share([(0.50, 300), (0.48, 100)], 1000, 0.5, 10, 0.50)
    assert s == pytest.approx(10 / (300 + 10 + 25))
    assert ex is True


def test_our_order_discounted_below_ref():
    s, _ = rule_c_share([(0.50, 300), (0.48, 100)], 1000, 0.5, 10, 0.47)
    assert s == pytest.approx(1.25 / (300 + 25 + 1.25))   # ours 3 ticks below -> 10 * .125


def test_outside_target_scores_zero():
    # 900 at .50 and 200 at .40: ahead of us at .40 is 1100 >= 1000
    s, ex = rule_c_share([(0.50, 900), (0.40, 200)], 1000, 0.5, 10, 0.40)
    assert s == 0.0
    assert ex is False
    s_l, _ = rule_l_share([(0.50, 900), (0.40, 200)], 1000, 0.5, 10, 0.40)
    assert s_l > 0


def test_other_levels_outside_target_dropped():
    # levels beyond the target (cum before >= 1000) do not dilute rule C but do dilute rule L
    lv = [(0.50, 900), (0.49, 100), (0.48, 500)]
    c, _ = rule_c_share(lv, 1000, 1.0, 10, 0.50)
    l, _ = rule_l_share(lv, 1000, 1.0, 10, 0.50)
    assert c == pytest.approx(10 / 1010)
    assert l == pytest.approx(10 / 1510)


def test_back_of_queue_in_level_and_best_bid_improvement():
    # ours at the back of 995: others ahead 995 < 1000 so we qualify
    s, ex = rule_c_share([(0.50, 995)], 1000, 0.5, 10, 0.50)
    assert s == pytest.approx(10 / 1005) and ex is False
    # ours at the back of 1000: queue ahead already reaches the target -> 0
    s, _ = rule_c_share([(0.50, 1000)], 1000, 0.5, 10, 0.50)
    assert s == 0.0


def test_empty_side():
    s, ex = rule_c_share([], 1000, 0.5, 10, 0.5)
    assert s == 1.0 and ex is True


def test_fine_tick():
    s, _ = rule_c_share([(0.500, 300), (0.490, 100)], 1000, 0.5, 10, 0.500, tick=0.005)
    assert s == pytest.approx(10 / (310 + 100 * 0.25))


def test_estimate_unit_and_time_fraction():
    prog = {"period_reward_usd": 200000 / 10_000, "target": 150.0, "discount": 0.5, "start_ts": 0, "end_ts": 1000}
    assert prog["period_reward_usd"] == 20.0     # 200000 centi-cents = $20
    yes = [(0.40, 100)]
    no = [(0.55, 100)]
    # q=100: each side share 100/200 = .5 -> reward 20 * .5 * frac
    assert estimate_program_reward(prog, yes, no, 100, 0) == pytest.approx(10.0)
    assert estimate_program_reward(prog, yes, no, 100, 750) == pytest.approx(2.5)
    assert estimate_program_reward(prog, yes, no, 100, 2000) == 0.0
    assert estimate_program_reward(prog, yes, no, 100, None) == pytest.approx(10.0)


def test_estimate_excluded_when_one_side_short():
    prog = {"period_reward_usd": 20.0, "target": 1000.0, "discount": 0.5, "start_ts": 0, "end_ts": 1000}
    assert estimate_program_reward(prog, [(0.4, 2000)], [(0.5, 100)], 10, 0) == 0.0
    assert estimate_program_reward(prog, [(0.4, 2000)], [], 10, 0) == 0.0
    v = estimate_program_reward(prog, [(0.4, 2000)], [(0.5, 990)], 10, 0)
    assert v == pytest.approx(20 * (0 + 10 / 1000) / 2)   # YES queue ahead (2000) is outside the target -> share 0
