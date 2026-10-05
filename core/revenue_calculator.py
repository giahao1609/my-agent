from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True, slots=True)
class RevenueCalculationResult:
    gross_amount: Decimal
    discount_amount: Decimal
    vat_amount: Decimal
    net_revenue: Decimal
    currency: str = "VND"


class RevenueCalculator:
    """Calculates gross amount, tiered discounts, VAT and net revenue."""

    def __init__(self, vat_rate: Decimal = Decimal("0.10")) -> None:
        self._vat_rate = vat_rate

    def calculate(
        self,
        gross_amount: Decimal | float | int,
        discount_rate: Decimal | float | int = Decimal("0.0"),
    ) -> RevenueCalculationResult:
        gross = Decimal(str(gross_amount))
        disc_rate = Decimal(str(discount_rate))

        if gross < 0:
            raise ValueError("gross_amount cannot be negative")
        if disc_rate < 0 or disc_rate > Decimal("1.0"):
            raise ValueError("discount_rate must be between 0.0 and 1.0")

        discount_amount = (gross * disc_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        after_discount = gross - discount_amount
        vat_amount = (after_discount * self._vat_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        net_revenue = after_discount + vat_amount

        return RevenueCalculationResult(
            gross_amount=gross,
            discount_amount=discount_amount,
            vat_amount=vat_amount,
            net_revenue=net_revenue,
            currency="VND",
        )
