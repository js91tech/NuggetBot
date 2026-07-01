"""User-facing drug command error messages."""
from __future__ import annotations

WHOLESALE_ERROR_MESSAGES: dict[str, str] = {
    "invalid_drug": "Unknown product — pick something from your stash.",
    "invalid_amount": "Enter a positive quantity to sell.",
    "insufficient_product": "You do not have that much product in your stash.",
}

CONSUME_ERROR_MESSAGES: dict[str, str] = {
    "invalid_drug": "Unknown product.",
    "insufficient_product": "You do not have that product in your stash.",
}


def wholesale_error_message(code: str) -> str:
    return WHOLESALE_ERROR_MESSAGES.get(code, "Could not complete wholesale sale.")


def consume_error_message(code: str) -> str:
    return CONSUME_ERROR_MESSAGES.get(code, "Could not use that product.")
