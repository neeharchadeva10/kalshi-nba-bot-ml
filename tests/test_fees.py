import numpy as np
import pytest

from kpm.core.fees import FeeModel

fee = FeeModel()  # 0.07 multiplier, cent rounding


def test_one_contract_at_50c_rounds_up_to_2c():
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> 0.02
    assert fee.order_fee(0.50, 1) == 0.02


def test_hundred_contracts_at_50c():
    # 0.07 * 100 * 0.25 = 1.75 exactly -> no rounding up
    assert fee.order_fee(0.50, 100) == pytest.approx(1.75)
    assert fee.per_contract(0.50, 100) == pytest.approx(0.0175)


def test_exact_cent_is_not_bumped_by_float_error():
    # 0.07 * 100 * 0.3 * 0.7 = 1.47 (float gives 1.4700000000000002)
    assert fee.order_fee(0.30, 100) == pytest.approx(1.47)


def test_small_fee_rounds_up_to_one_cent():
    # 0.07 * 1 * 0.01 * 0.99 = 0.000693 -> 0.01
    assert fee.order_fee(0.01, 1) == 0.01


def test_fee_symmetric_in_price():
    for p in (0.1, 0.25, 0.4):
        assert fee.order_fee(p, 37) == fee.order_fee(1 - p, 37)


def test_zero_and_one_price_have_no_fee():
    assert fee.order_fee(0.0, 10) == 0.0
    assert fee.order_fee(1.0, 10) == 0.0


def test_maker_fee_is_quarter_of_taker_before_rounding():
    m = FeeModel(rounding="none")
    assert m.order_fee(0.5, 100, maker=True) == pytest.approx(0.25 * m.order_fee(0.5, 100))


def test_micro_rounding_mode():
    m = FeeModel(rounding="micro_ceil")
    assert m.order_fee(0.01, 1) == pytest.approx(0.000693)


def test_configurable_multiplier():
    assert FeeModel(taker_multiplier=0.035).order_fee(0.5, 100) == pytest.approx(0.88)  # 0.875 -> 0.88


def test_vectorized_matches_scalar():
    prices = np.array([0.01, 0.17, 0.5, 0.63, 0.99, np.nan])
    vec = fee.per_contract_array(prices, 100)
    for p, v in zip(prices[:-1], vec[:-1]):
        assert v == pytest.approx(fee.per_contract(p, 100))
    assert np.isnan(vec[-1])


def test_invalid_inputs():
    with pytest.raises(ValueError):
        fee.order_fee(1.2, 1)
    with pytest.raises(ValueError):
        fee.per_contract(0.5, 0)
    with pytest.raises(ValueError):
        FeeModel(rounding="bogus").order_fee(0.5, 1)
