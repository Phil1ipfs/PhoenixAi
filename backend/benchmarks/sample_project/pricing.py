"""Pricing rules for the shop."""

BULK_THRESHOLD = 10      # quantity at which the bulk discount starts
BULK_DISCOUNT = 0.15     # 15% off the subtotal
TAX_RATE = 0.12


def apply_discount(subtotal, quantity):
    """Return the subtotal after the bulk discount (if the quantity qualifies)."""
    if quantity >= BULK_THRESHOLD:
        return subtotal * (1 - BULK_DISCOUNT)
    return subtotal


def add_tax(amount):
    """Add sales tax and round to centavos."""
    return round(amount * (1 + TAX_RATE), 2)
