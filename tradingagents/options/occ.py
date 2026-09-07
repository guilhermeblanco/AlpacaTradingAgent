from __future__ import annotations

import re
from datetime import date, datetime


_OCC_PATTERN = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def format_occ_symbol(underlying: str, expiration: date | datetime | str,
                      contract_type: str, strike: float) -> str:
    if isinstance(expiration, str):
        expiration = date.fromisoformat(expiration)
    elif isinstance(expiration, datetime):
        expiration = expiration.date()
    kind = contract_type.upper()
    if kind not in {"CALL", "PUT", "C", "P"}:
        raise ValueError("contract_type must be call or put")
    strike_units = round(float(strike) * 1000)
    if strike_units <= 0 or strike_units > 99_999_999:
        raise ValueError("strike is outside OCC range")
    return f"{underlying.upper()[:6]}{expiration:%y%m%d}{'C' if kind in {'CALL', 'C'} else 'P'}{strike_units:08d}"


def parse_occ_symbol(symbol: str) -> dict:
    match = _OCC_PATTERN.fullmatch(symbol.upper())
    if not match:
        raise ValueError(f"Invalid OCC option symbol: {symbol}")
    underlying, raw_date, kind, raw_strike = match.groups()
    expiration = datetime.strptime(raw_date, "%y%m%d").date()
    return {
        "symbol": symbol.upper(), "underlying": underlying, "expiration": expiration,
        "contract_type": "call" if kind == "C" else "put",
        "strike": int(raw_strike) / 1000.0,
    }
