import requests


FUTURES_BASE_URL = "https://fapi.binance.com"


def get_funding_rate():

    url = (
        f"{FUTURES_BASE_URL}"
        "/fapi/v1/premiumIndex"
    )

    params = {
        "symbol": "BTCUSDT"
    }

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    response.raise_for_status()

    data = response.json()

    return float(
        data["lastFundingRate"]
    )


def get_open_interest():

    url = (
        f"{FUTURES_BASE_URL}"
        "/fapi/v1/openInterest"
    )

    params = {
        "symbol": "BTCUSDT"
    }

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    response.raise_for_status()

    data = response.json()

    return float(
        data["openInterest"]
    )


def get_crypto_market_data():

    try:

        funding_rate = (
            get_funding_rate()
        )

        open_interest = (
            get_open_interest()
        )

        return {
            "funding_rate":
                funding_rate,

            "open_interest":
                open_interest,

            "available":
                True,
        }

    except Exception as e:

        print(
            f"Crypto market data error: {e}"
        )

        return {
            "funding_rate": None,
            "open_interest": None,
            "available": False,
        }


def calculate_oi_change(
    current_oi,
    previous_oi
):

    if (
        current_oi is None
        or previous_oi is None
    ):
        return None

    if previous_oi <= 0:
        return None

    return (
        (current_oi - previous_oi)
        / previous_oi
    ) * 100


def calculate_price_change(
    previous_price,
    current_price
):

    if (
        previous_price is None
        or current_price is None
    ):
        return None

    if previous_price <= 0:
        return None

    return (
        (current_price - previous_price)
        / previous_price
    ) * 100


def crypto_signal(
    funding_rate=None,
    open_interest_change=None,
    price_change=None,
):

    if (
        funding_rate is None
        or open_interest_change is None
        or price_change is None
    ):

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason":
                "Funding/OI data unavailable",
        }

    # ======================================================
    # PRICE UP + OI UP
    # ======================================================

    if (
        price_change > 0
        and open_interest_change > 0
    ):

        if funding_rate > 0:

            return {
                "side": "LONG",
                "score": 80,
                "reason":
                    "Price ↑ + OI ↑ + positive funding",
            }

        if funding_rate < 0:

            return {
                "side": "LONG",
                "score": 75,
                "reason":
                    "Price ↑ + OI ↑ + negative funding",
            }

    # ======================================================
    # PRICE DOWN + OI UP
    # ======================================================

    if (
        price_change < 0
        and open_interest_change > 0
    ):

        if funding_rate < 0:

            return {
                "side": "SHORT",
                "score": 80,
                "reason":
                    "Price ↓ + OI ↑ + negative funding",
            }

        if funding_rate > 0:

            return {
                "side": "SHORT",
                "score": 75,
                "reason":
                    "Price ↓ + OI ↑ + positive funding",
            }

    # ======================================================
    # PRICE UP + OI DOWN
    # ======================================================

    if (
        price_change > 0
        and open_interest_change < 0
        and funding_rate < 0
    ):

        return {
            "side": "LONG",
            "score": 70,
            "reason":
                "Price ↑ + OI ↓ + negative funding",
        }

    # ======================================================
    # PRICE DOWN + OI DOWN
    # ======================================================

    if (
        price_change < 0
        and open_interest_change < 0
        and funding_rate > 0
    ):

        return {
            "side": "SHORT",
            "score": 70,
            "reason":
                "Price ↓ + OI ↓ + positive funding",
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason":
            "No strong funding/OI confirmation",
    }
