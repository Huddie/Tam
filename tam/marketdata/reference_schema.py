"""Column names for the six Massive/Polygon reference datasets ingested
alongside minute/EOD bars -- splits, dividends, IPOs, short volume, short
interest, float. Not price bars (see tam.marketdata.schema for those); this
is event/reference data about securities, fetched as global (not
per-symbol) paginated feeds from the vendor's newer `/stocks/v1/*`/`/vX/*`
REST endpoints (NOT the older `/v3/reference/*` endpoints the `massive`
Python SDK's `list_splits()`/`list_dividends()` wrap, which carry a smaller
schema -- see tam.marketdata.reference_provider's own docstring).

Every date/timestamp field is kept as a plain string at this storage layer
(same convention as tam.research.data.sec.schema) -- cast to a real DATE at
query time in SQL, not here, so a column that's entirely None in one
partition never gets PyArrow's `null` type inferred instead of `string`
(which would then fail to combine with another partition's real string
values under one read_parquet() glob -- see pyarrow_schema()'s own
docstring below for why this needs an EXPLICIT schema at all).

`id` (splits/dividends) is a long hex string on this newer endpoint (see
each sample response in the vendor's own docs), NOT the small integer the
older SDK model uses for the OLDER endpoint of the same name -- confirmed
by reading the vendor's own documented sample responses directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

import pandas as pd

if TYPE_CHECKING:
    import pyarrow as pa

TICKER = "ticker"

# ---- splits -----------------------------------------------------------------

SPLIT_ID = "id"
SPLIT_EXECUTION_DATE = "execution_date"
SPLIT_FROM = "split_from"
SPLIT_TO = "split_to"
SPLIT_ADJUSTMENT_TYPE = "adjustment_type"  # "forward_split" | "reverse_split" | "stock_dividend"
SPLIT_HISTORICAL_ADJUSTMENT_FACTOR = "historical_adjustment_factor"

SPLIT_COLUMNS = [
    SPLIT_ID,
    TICKER,
    SPLIT_EXECUTION_DATE,
    SPLIT_FROM,
    SPLIT_TO,
    SPLIT_ADJUSTMENT_TYPE,
    SPLIT_HISTORICAL_ADJUSTMENT_FACTOR,
]

# ---- dividends ----------------------------------------------------------------

DIVIDEND_ID = "id"
DIVIDEND_CASH_AMOUNT = "cash_amount"
DIVIDEND_CURRENCY = "currency"
DIVIDEND_DECLARATION_DATE = "declaration_date"
DIVIDEND_DISTRIBUTION_TYPE = "distribution_type"  # "recurring" | "special" | "supplemental" | "irregular" | "unknown"
DIVIDEND_EX_DIVIDEND_DATE = "ex_dividend_date"
DIVIDEND_FREQUENCY = "frequency"
DIVIDEND_HISTORICAL_ADJUSTMENT_FACTOR = "historical_adjustment_factor"
DIVIDEND_PAY_DATE = "pay_date"
DIVIDEND_RECORD_DATE = "record_date"
DIVIDEND_SPLIT_ADJUSTED_CASH_AMOUNT = "split_adjusted_cash_amount"

DIVIDEND_COLUMNS = [
    DIVIDEND_ID,
    TICKER,
    DIVIDEND_CASH_AMOUNT,
    DIVIDEND_CURRENCY,
    DIVIDEND_DECLARATION_DATE,
    DIVIDEND_DISTRIBUTION_TYPE,
    DIVIDEND_EX_DIVIDEND_DATE,
    DIVIDEND_FREQUENCY,
    DIVIDEND_HISTORICAL_ADJUSTMENT_FACTOR,
    DIVIDEND_PAY_DATE,
    DIVIDEND_RECORD_DATE,
    DIVIDEND_SPLIT_ADJUSTED_CASH_AMOUNT,
]

# ---- ipos -----------------------------------------------------------------

IPO_ANNOUNCED_DATE = "announced_date"
IPO_CURRENCY_CODE = "currency_code"
IPO_FINAL_ISSUE_PRICE = "final_issue_price"
IPO_HIGHEST_OFFER_PRICE = "highest_offer_price"
IPO_STATUS = "ipo_status"  # "direct_listing_process" | "history" | "new" | "pending" | "postponed" | "rumor" | "withdrawn"
IPO_ISIN = "isin"
IPO_ISSUER_NAME = "issuer_name"
IPO_LAST_UPDATED = "last_updated"
IPO_LISTING_DATE = "listing_date"
IPO_LOT_SIZE = "lot_size"
IPO_LOWEST_OFFER_PRICE = "lowest_offer_price"
IPO_MAX_SHARES_OFFERED = "max_shares_offered"
IPO_MIN_SHARES_OFFERED = "min_shares_offered"
IPO_PRIMARY_EXCHANGE = "primary_exchange"
IPO_SECURITY_DESCRIPTION = "security_description"
IPO_SECURITY_TYPE = "security_type"
IPO_SHARES_OUTSTANDING = "shares_outstanding"
IPO_TOTAL_OFFER_SIZE = "total_offer_size"
IPO_US_CODE = "us_code"

IPO_COLUMNS = [
    TICKER,
    IPO_ISSUER_NAME,
    IPO_STATUS,
    IPO_ANNOUNCED_DATE,
    IPO_LISTING_DATE,
    IPO_LAST_UPDATED,
    IPO_CURRENCY_CODE,
    IPO_FINAL_ISSUE_PRICE,
    IPO_LOWEST_OFFER_PRICE,
    IPO_HIGHEST_OFFER_PRICE,
    IPO_MIN_SHARES_OFFERED,
    IPO_MAX_SHARES_OFFERED,
    IPO_SHARES_OUTSTANDING,
    IPO_TOTAL_OFFER_SIZE,
    IPO_LOT_SIZE,
    IPO_ISIN,
    IPO_US_CODE,
    IPO_PRIMARY_EXCHANGE,
    IPO_SECURITY_TYPE,
    IPO_SECURITY_DESCRIPTION,
]

# ---- short volume -----------------------------------------------------------

SHORT_VOLUME_DATE = "date"
SHORT_VOLUME_SHORT_VOLUME = "short_volume"
SHORT_VOLUME_TOTAL_VOLUME = "total_volume"
SHORT_VOLUME_RATIO = "short_volume_ratio"
SHORT_VOLUME_EXEMPT_VOLUME = "exempt_volume"
SHORT_VOLUME_NON_EXEMPT_VOLUME = "non_exempt_volume"
SHORT_VOLUME_ADF = "adf_short_volume"
SHORT_VOLUME_ADF_EXEMPT = "adf_short_volume_exempt"
SHORT_VOLUME_NASDAQ_CARTERET = "nasdaq_carteret_short_volume"
SHORT_VOLUME_NASDAQ_CARTERET_EXEMPT = "nasdaq_carteret_short_volume_exempt"
SHORT_VOLUME_NASDAQ_CHICAGO = "nasdaq_chicago_short_volume"
SHORT_VOLUME_NASDAQ_CHICAGO_EXEMPT = "nasdaq_chicago_short_volume_exempt"
SHORT_VOLUME_NYSE = "nyse_short_volume"
SHORT_VOLUME_NYSE_EXEMPT = "nyse_short_volume_exempt"

SHORT_VOLUME_COLUMNS = [
    TICKER,
    SHORT_VOLUME_DATE,
    SHORT_VOLUME_SHORT_VOLUME,
    SHORT_VOLUME_TOTAL_VOLUME,
    SHORT_VOLUME_RATIO,
    SHORT_VOLUME_EXEMPT_VOLUME,
    SHORT_VOLUME_NON_EXEMPT_VOLUME,
    SHORT_VOLUME_ADF,
    SHORT_VOLUME_ADF_EXEMPT,
    SHORT_VOLUME_NASDAQ_CARTERET,
    SHORT_VOLUME_NASDAQ_CARTERET_EXEMPT,
    SHORT_VOLUME_NASDAQ_CHICAGO,
    SHORT_VOLUME_NASDAQ_CHICAGO_EXEMPT,
    SHORT_VOLUME_NYSE,
    SHORT_VOLUME_NYSE_EXEMPT,
]

# ---- short interest -----------------------------------------------------------

SHORT_INTEREST_SETTLEMENT_DATE = "settlement_date"
SHORT_INTEREST_SHORT_INTEREST = "short_interest"
SHORT_INTEREST_AVG_DAILY_VOLUME = "avg_daily_volume"
SHORT_INTEREST_DAYS_TO_COVER = "days_to_cover"

SHORT_INTEREST_COLUMNS = [
    TICKER,
    SHORT_INTEREST_SETTLEMENT_DATE,
    SHORT_INTEREST_SHORT_INTEREST,
    SHORT_INTEREST_AVG_DAILY_VOLUME,
    SHORT_INTEREST_DAYS_TO_COVER,
]

# ---- float -----------------------------------------------------------

FLOAT_EFFECTIVE_DATE = "effective_date"
FLOAT_FREE_FLOAT = "free_float"
FLOAT_FREE_FLOAT_PERCENT = "free_float_percent"

FLOAT_COLUMNS = [
    TICKER,
    FLOAT_EFFECTIVE_DATE,
    FLOAT_FREE_FLOAT,
    FLOAT_FREE_FLOAT_PERCENT,
]

_COLUMN_TYPE_NAMES = {
    # Every numeric non-price field below is "float64", even the ones that
    # sound like plain integer counts (split_from/split_to, shares
    # outstanding, share volumes) -- confirmed live against the real API:
    # a real split's split_from came back as 11.5, not a whole number.
    # int64 would raise ArrowInvalid ("Float value ... truncated
    # converting to int64") the first time any field like this arrives
    # non-integral, which int64 can't represent at all; float64 can hold
    # every value int64 could (up to a range far beyond anything these
    # fields will ever see) AND the occasional fractional one, so there's
    # no reason to risk the crash for a schema-purity preference.
    SPLIT_ID: "string",
    TICKER: "string",
    SPLIT_EXECUTION_DATE: "string",
    SPLIT_FROM: "float64",
    SPLIT_TO: "float64",
    SPLIT_ADJUSTMENT_TYPE: "string",
    SPLIT_HISTORICAL_ADJUSTMENT_FACTOR: "float64",
    DIVIDEND_ID: "string",
    DIVIDEND_CASH_AMOUNT: "float64",
    DIVIDEND_CURRENCY: "string",
    DIVIDEND_DECLARATION_DATE: "string",
    DIVIDEND_DISTRIBUTION_TYPE: "string",
    DIVIDEND_EX_DIVIDEND_DATE: "string",
    DIVIDEND_FREQUENCY: "float64",
    DIVIDEND_HISTORICAL_ADJUSTMENT_FACTOR: "float64",
    DIVIDEND_PAY_DATE: "string",
    DIVIDEND_RECORD_DATE: "string",
    DIVIDEND_SPLIT_ADJUSTED_CASH_AMOUNT: "float64",
    IPO_ANNOUNCED_DATE: "string",
    IPO_CURRENCY_CODE: "string",
    IPO_FINAL_ISSUE_PRICE: "float64",
    IPO_HIGHEST_OFFER_PRICE: "float64",
    IPO_STATUS: "string",
    IPO_ISIN: "string",
    IPO_ISSUER_NAME: "string",
    IPO_LAST_UPDATED: "string",
    IPO_LISTING_DATE: "string",
    IPO_LOT_SIZE: "float64",
    IPO_LOWEST_OFFER_PRICE: "float64",
    IPO_MAX_SHARES_OFFERED: "float64",
    IPO_MIN_SHARES_OFFERED: "float64",
    IPO_PRIMARY_EXCHANGE: "string",
    IPO_SECURITY_DESCRIPTION: "string",
    IPO_SECURITY_TYPE: "string",
    IPO_SHARES_OUTSTANDING: "float64",
    IPO_TOTAL_OFFER_SIZE: "float64",
    IPO_US_CODE: "string",
    SHORT_VOLUME_DATE: "string",
    SHORT_VOLUME_SHORT_VOLUME: "float64",
    SHORT_VOLUME_TOTAL_VOLUME: "float64",
    SHORT_VOLUME_RATIO: "float64",
    SHORT_VOLUME_EXEMPT_VOLUME: "float64",
    SHORT_VOLUME_NON_EXEMPT_VOLUME: "float64",
    SHORT_VOLUME_ADF: "float64",
    SHORT_VOLUME_ADF_EXEMPT: "float64",
    SHORT_VOLUME_NASDAQ_CARTERET: "float64",
    SHORT_VOLUME_NASDAQ_CARTERET_EXEMPT: "float64",
    SHORT_VOLUME_NASDAQ_CHICAGO: "float64",
    SHORT_VOLUME_NASDAQ_CHICAGO_EXEMPT: "float64",
    SHORT_VOLUME_NYSE: "float64",
    SHORT_VOLUME_NYSE_EXEMPT: "float64",
    SHORT_INTEREST_SETTLEMENT_DATE: "string",
    SHORT_INTEREST_SHORT_INTEREST: "float64",
    SHORT_INTEREST_AVG_DAILY_VOLUME: "float64",
    SHORT_INTEREST_DAYS_TO_COVER: "float64",
    FLOAT_EFFECTIVE_DATE: "string",
    FLOAT_FREE_FLOAT: "float64",
    FLOAT_FREE_FLOAT_PERCENT: "float64",
}


def pyarrow_schema(columns: List[str]) -> "pa.Schema":
    """An explicit PyArrow schema for `columns` (one of the *_COLUMNS lists
    above) -- same reasoning (and same shape) as
    tam.research.data.sec.schema.pyarrow_schema(): a column that's entirely
    None in one partition would otherwise get PyArrow's `null` type
    inferred instead of the real column type, which then fails to combine
    with another partition's real values under one read_parquet() glob."""
    import pyarrow as pa

    types = {"int64": pa.int64(), "string": pa.string(), "float64": pa.float64(), "bool": pa.bool_()}
    return pa.schema([pa.field(name, types[_COLUMN_TYPE_NAMES[name]]) for name in columns])


def empty_frame(columns: List[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)
