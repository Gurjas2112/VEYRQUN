"""Money is never a float: Decimal at the edges, integer minor units in counters and policy."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# ISO 4217 minor-unit exponents for the currencies VEYRQUN accepts.
CURRENCY_EXPONENT = {"USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "SGD": 2, "JPY": 0}


class MoneyError(ValueError):
    pass


def check_currency(code: str) -> str:
    code = code.upper()
    if code not in CURRENCY_EXPONENT:
        raise MoneyError(f"unsupported currency {code!r}")
    return code


def to_decimal(value: object, currency: str = "USD") -> Decimal:
    """Parse an amount exactly. Rejects values with more precision than the currency allows."""
    try:
        amount = Decimal(str(value)) if not isinstance(value, Decimal) else value
    except InvalidOperation as exc:
        raise MoneyError(f"invalid amount {value!r}") from exc
    if not amount.is_finite():
        raise MoneyError("amount must be finite")
    exponent = CURRENCY_EXPONENT[check_currency(currency)]
    quantum = Decimal(1).scaleb(-exponent)
    if amount != amount.quantize(quantum):
        raise MoneyError(f"{currency} amounts allow at most {exponent} decimal places")
    return amount.quantize(quantum, rounding=ROUND_HALF_EVEN)


def to_minor(amount: Decimal, currency: str = "USD") -> int:
    return int(to_decimal(amount, currency).scaleb(CURRENCY_EXPONENT[check_currency(currency)]))


def from_minor(minor: int, currency: str = "USD") -> Decimal:
    exponent = CURRENCY_EXPONENT[check_currency(currency)]
    return (Decimal(minor).scaleb(-exponent)).quantize(Decimal(1).scaleb(-exponent))


def fmt(amount: Decimal | None) -> str | None:
    """Canonical string form used in API responses and in audit hashes."""
    return None if amount is None else format(amount, "f")
