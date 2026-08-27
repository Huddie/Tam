"""Column names for the SEC data lake's two Parquet layers -- shared so the
provider (fetch), store (persist), and normalize (raw -> financials) code
all agree on schema, same convention as tam.data.schema/tam.marketdata.schema.

Two layers, deliberately different shapes (see tam/research/data/sec's own
package docstring for why):

- FACTS_COLUMNS: raw XBRL facts, full fidelity, one row per (cik, concept,
  context, accession) -- nothing deduplicated, nothing normalized away.
- FINANCIALS_COLUMNS: derived, rebuildable-without-refetching, LONG format
  (one row per line item, not one column per metric) -- so a query can ask
  for "every row in this statement" without a schema migration every time
  a line item is added, and so pandas' own df.pivot() has something
  natural to pivot into a wide view when a caller wants one.
"""
from __future__ import annotations

import pandas as pd

CIK = "cik"
ENTITY_NAME = "entity_name"
TAXONOMY = "taxonomy"
CONCEPT = "concept"
UNIT = "unit"
FACT_TYPE = "fact_type"  # "instant" | "duration"
START_DATE = "start_date"
END_DATE = "end_date"
FISCAL_YEAR = "fiscal_year"
FISCAL_PERIOD = "fiscal_period"  # "Q1" | "Q2" | "Q3" | "FY"
FORM = "form"
FILED_DATE = "filed_date"
ACCESSION_NUMBER = "accession_number"
FRAME = "frame"
DIMENSIONS = "dimensions"  # JSON-encoded {axis: member}, or None for the whole-company total
CONTEXT_ID = "context_id"
VALUE = "value"

FACTS_COLUMNS = [
    CIK,
    ENTITY_NAME,
    TAXONOMY,
    CONCEPT,
    UNIT,
    FACT_TYPE,
    START_DATE,
    END_DATE,
    FISCAL_YEAR,
    FISCAL_PERIOD,
    FORM,
    FILED_DATE,
    ACCESSION_NUMBER,
    FRAME,
    DIMENSIONS,
    CONTEXT_ID,
    VALUE,
]

STATEMENT = "statement"  # "income_statement" | "balance_sheet" | "cash_flow_statement"
LINE_ITEM = "line_item"  # normalized name: "revenue" | "net_income" | "total_assets" | ...

FINANCIALS_COLUMNS = [
    CIK,
    FISCAL_YEAR,
    FISCAL_PERIOD,
    ACCESSION_NUMBER,
    FILED_DATE,
    STATEMENT,
    LINE_ITEM,
    CONCEPT,
    VALUE,
]

TICKER = "ticker"
REFERENCE_COLUMNS = [CIK, TICKER, ENTITY_NAME]

# Filing metadata (tam.research.data.sec.store's `submissions` layer)
FILING_ACCESSION_NUMBER = ACCESSION_NUMBER
FILING_FORM = FORM
FILING_FILED_DATE = FILED_DATE
FILING_PERIOD_OF_REPORT = "period_of_report"
FILING_PRIMARY_DOCUMENT = "primary_document"
FILING_IS_XBRL = "is_xbrl"

SUBMISSIONS_COLUMNS = [
    CIK,
    FILING_ACCESSION_NUMBER,
    FILING_FORM,
    FILING_FILED_DATE,
    FILING_PERIOD_OF_REPORT,
    FILING_PRIMARY_DOCUMENT,
    FILING_IS_XBRL,
]


def empty_facts_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=FACTS_COLUMNS)


def empty_financials_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=FINANCIALS_COLUMNS)


def empty_submissions_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SUBMISSIONS_COLUMNS)
