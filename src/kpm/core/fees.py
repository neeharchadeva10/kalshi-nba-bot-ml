"""Kalshi trading fee model (configurable).

Kalshi general fee schedule: taker fee = multiplier * C * P * (1 - P), rounded UP,
with multiplier 0.07; maker fee is a fraction (0.25) of that. The KXNFLGAME series
reports fee_type='quadratic_with_maker_fees' and fee_multiplier=1 (i.e. the standard
schedule). Kalshi's fee-rounding docs now describe rounding up to $0.000001, while the
long-standing schedule rounds the order fee up to the next cent; both are supported.
No fee is charged at settlement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

_EPS = 1e-9


def _round_up(x: float, step: float) -> float:
    return math.ceil(x / step - _EPS) * step


def _round_half_even_cent(x: float) -> float:
    # Decimal(str(...)) avoids binary float artifacts deciding which way a half-cent rounds
    return float(Decimal(str(round(x, 9))).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class FeeModel:
    taker_multiplier: float = 0.07
    maker_fraction: float = 0.25
    rounding: str = "cent_ceil"  # cent_ceil | cent_half_even | micro_ceil | none

    @classmethod
    def from_config(cls, cfg: dict, venue: str = "fees") -> "FeeModel":
        f = cfg[venue]
        return cls(f["taker_multiplier"], f["maker_fraction"], f["rounding"])

    def order_fee(self, price: float, count: float, maker: bool = False) -> float:
        """Total fee in dollars for one order of `count` contracts at `price`."""
        if not 0.0 <= price <= 1.0:
            raise ValueError(f"price must be in [0, 1], got {price}")
        if count < 0:
            raise ValueError("count must be >= 0")
        mult = self.taker_multiplier * (self.maker_fraction if maker else 1.0)
        raw = mult * count * price * (1.0 - price)
        if self.rounding == "cent_ceil":
            return round(_round_up(raw, 0.01), 2)
        if self.rounding == "cent_half_even":
            return _round_half_even_cent(raw)
        if self.rounding == "micro_ceil":
            return round(_round_up(raw, 0.000001), 6)
        if self.rounding == "none":
            return raw
        raise ValueError(f"unknown rounding mode {self.rounding!r}")

    def per_contract(self, price: float, count: float = 1, maker: bool = False) -> float:
        """Fee per contract when buying `count` contracts in one order."""
        if count <= 0:
            raise ValueError("count must be > 0")
        return self.order_fee(price, count, maker) / count

    def per_contract_array(self, prices, count: float = 1, maker: bool = False):
        """Vectorized per_contract; NaN prices give NaN fees."""
        import numpy as np

        p = np.asarray(prices, dtype=float)
        mult = self.taker_multiplier * (self.maker_fraction if maker else 1.0)
        raw = mult * count * p * (1.0 - p)
        if self.rounding == "cent_ceil":
            fee = np.round(np.ceil(raw / 0.01 - _EPS) * 0.01, 2)
        elif self.rounding == "cent_half_even":
            fee = np.round(np.round(raw, 9), 2)  # numpy rounds half to even
        elif self.rounding == "micro_ceil":
            fee = np.round(np.ceil(raw / 0.000001 - _EPS) * 0.000001, 6)
        elif self.rounding == "none":
            fee = raw
        else:
            raise ValueError(f"unknown rounding mode {self.rounding!r}")
        return fee / count
