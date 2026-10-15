"""Inventory management for a small shop."""
from pricing import apply_discount, add_tax


class Inventory:
    def __init__(self):
        self.items = {}

    def add_item(self, sku, name, price, quantity=0):
        self.items[sku] = {"name": name, "price": price, "quantity": quantity}

    def remove_stock(self, sku, amount):
        item = self.items[sku]
        if amount > item["quantity"]:
            raise ValueError(f"Insufficient stock for {sku}")
        item["quantity"] -= amount

    def total_value(self):
        return sum(i["price"] * i["quantity"] for i in self.items.values())

    def order_total(self, sku, quantity):
        subtotal = self.items[sku]["price"] * quantity
        return add_tax(apply_discount(subtotal, quantity))
