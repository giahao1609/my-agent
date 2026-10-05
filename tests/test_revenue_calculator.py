from __future__ import annotations

from decimal import Decimal
import pytest

from core.revenue_calculator import RevenueCalculator


def test_revenue_calculator_standard():
    calc = RevenueCalculator(vat_rate=Decimal("0.10"))
    res = calc.calculate(gross_amount=1_000_000, discount_rate=Decimal("0.15"))

    assert res.gross_amount == Decimal("1000000")
    assert res.discount_amount == Decimal("150000")
    assert res.vat_amount == Decimal("85000")
    assert res.net_revenue == Decimal("935000")
    assert res.currency == "VND"


def test_revenue_calculator_no_discount():
    calc = RevenueCalculator(vat_rate=Decimal("0.10"))
    res = calc.calculate(gross_amount=500_000, discount_rate=0)

    assert res.discount_amount == Decimal("0")
    assert res.vat_amount == Decimal("50000")
    assert res.net_revenue == Decimal("550000")


def test_revenue_calculator_invalid_inputs():
    calc = RevenueCalculator()
    with pytest.raises(ValueError, match="gross_amount cannot be negative"):
        calc.calculate(gross_amount=-100)

    with pytest.raises(ValueError, match="discount_rate must be between"):
        calc.calculate(gross_amount=100, discount_rate=1.5)
