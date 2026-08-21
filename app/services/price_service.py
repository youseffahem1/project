"""
Live USD price lookups — used ONLY to enforce "$5 equivalent" minimum
deposits for TRX and BTC (USDT needs no conversion, it's already ~1 USD).

Uses CoinGecko's public /simple/price endpoint, which needs NO API key for
this basic usage. Cached in-memory for PRICE_CACHE_SECONDS so we don't hit
CoinGecko on every single deposit check.

*** This is money math — it NEVER silently guesses a price. ***
If CoinGecko is unreachable and there's no usable cached price yet, callers
get PriceUnavailableError and MUST refuse the amount check rather than
approve a deposit against a fabricated rate.

Override: if TRX_USD_RATE / BTC_USD_RATE is set in the environment, that
fixed rate is used instead and CoinGecko is never called at all — for
servers with no outbound internet access, or if you prefer to set/update
the rate yourself on a schedule.
"""
import time
import httpx

from ..config import (
    TRX_USD_RATE_OVERRIDE, BTC_USD_RATE_OVERRIDE,
    PRICE_CACHE_SECONDS, PRICE_STALE_MAX_SECONDS,
)

_cache = {}  # {"TRX": (price, fetched_at_unix), "BTC": (...)}

_COINGECKO_IDS = {"TRX": "tron", "BTC": "bitcoin"}


class PriceUnavailableError(Exception):
    pass


def get_usd_price(symbol: str) -> float:
    symbol = symbol.upper()

    override = {"TRX": TRX_USD_RATE_OVERRIDE, "BTC": BTC_USD_RATE_OVERRIDE}.get(symbol)
    if override:
        return override

    gecko_id = _COINGECKO_IDS.get(symbol)
    if not gecko_id:
        raise PriceUnavailableError(f"No USD price source configured for {symbol}")

    cached = _cache.get(symbol)
    if cached and (time.time() - cached[1]) < PRICE_CACHE_SECONDS:
        return cached[0]

    try:
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": gecko_id, "vs_currencies": "usd"},
            timeout=10,
        )
        resp.raise_for_status()
        price = float(resp.json()[gecko_id]["usd"])
        _cache[symbol] = (price, time.time())
        return price
    except Exception as e:
        # Serve the last known price if it isn't too stale, rather than
        # fail every single deposit check just because CoinGecko hiccuped —
        # but never beyond PRICE_STALE_MAX_SECONDS old.
        if cached and (time.time() - cached[1]) < PRICE_STALE_MAX_SECONDS:
            return cached[0]
        raise PriceUnavailableError(f"Could not fetch {symbol}/USD price: {e}") from e
