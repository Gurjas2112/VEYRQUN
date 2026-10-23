"""Property-based tests: invariants that must hold for *every* input, not just the examples we thought of."""

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from veyrqun import audit, money, service
from veyrqun.db import tx

CURRENCIES = st.sampled_from(sorted(money.CURRENCY_EXPONENT))


@st.composite
def amounts(draw):
    currency = draw(CURRENCIES)
    exp = money.CURRENCY_EXPONENT[currency]
    minor = draw(st.integers(min_value=0, max_value=10**16 - 1))  # NUMERIC(18,2) holds up to 16 integer digits
    return currency, minor, exp


@given(amounts())
def test_minor_units_round_trip_exactly(a):
    currency, minor, _ = a
    assert money.to_minor(money.from_minor(minor, currency), currency) == minor


@given(amounts())
def test_canonical_string_round_trips_through_decimal(a):
    currency, minor, _ = a
    d = money.from_minor(minor, currency)
    assert money.to_decimal(money.fmt(d), currency) == d and "e" not in money.fmt(d).lower()


@given(CURRENCIES, st.decimals(allow_nan=False, allow_infinity=False, places=6, min_value=0, max_value=10**6))
def test_extra_precision_is_rejected_never_rounded(currency, value):
    exp = money.CURRENCY_EXPONENT[currency]
    if value == value.quantize(Decimal(1).scaleb(-exp)):
        assert money.to_decimal(value, currency) == value
    else:
        try:
            money.to_decimal(value, currency)
        except money.MoneyError:
            return
        raise AssertionError(f"{value} {currency} was silently rounded")


TEXT = st.text(min_size=0, max_size=40)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.integers(min_value=0, max_value=10**12), TEXT, TEXT, st.dictionaries(st.text(max_size=8), TEXT, max_size=3))
def test_stored_entries_rehash_identically(minor, actor, action, details):
    """store -> read back -> recompute hash gives the stored hash, for arbitrary text, unicode and amounts."""
    ws = service.default_workspace()
    with tx(ws) as conn:
        entry = audit.append(conn, workspace_id=ws, actor=actor or "a", agent_id="prop", action_type=action or "x",
                             amount=money.from_minor(minor, "USD"), currency="USD", outcome="allow", rule_id=None,
                             policy_version=1, details=details)
    stored = audit.get(entry["id"], ws)
    assert audit.compute_entry_hash(stored) == stored["entry_hash"] == entry["entry_hash"]
    assert audit.verify_entry(stored)["valid"]
