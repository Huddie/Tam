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
  natural to pivot into a wide view when a caller wants one. Carries
  START_DATE/END_DATE through from the raw facts -- without them, two
  rows with the SAME (unreliable) fiscal_year/fiscal_period label but
  genuinely different real periods (e.g. a 10-Q's current-quarter figure
  next to its year-to-date figure) are indistinguishable from the output
  alone; see normalize.py's own docstring for why those labels aren't a
  reliable period identity in the first place.

pyarrow_schema() builds an EXPLICIT PyArrow schema for a given columns list
-- SecStore._write_parquet() uses this instead of letting
pa.Table.from_pandas() infer each column's type per call. Confirmed live:
a column that's entirely None in one partition (e.g. start_date in a
file covering only instant facts) gets inferred as PyArrow's special
`null` type instead of `string` -- harmless for that one file alone, but
DuckDB's read_parquet() over a glob of MULTIPLE such files then fails to
combine them once another partition's start_date is a real string
("Conversion Error: ... VARCHAR -> NULL"). An explicit, shared schema
means every file gets the SAME column types regardless of what happens to
be null in that particular partition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import pyarrow as pa

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

STATEMENT = "statement"  # see Sec.statements() for the real, current set of values -- sourced
# directly from EdgarTools' own concept categories (normalize.py), not a fixed list worth
# duplicating in a comment here (confirmed live: it's ["balance_sheet", "cash_flow",
# "income_statement", "metrics"], not the 3-way "cash_flow_statement"-suffixed guess this
# comment used to make).
LINE_ITEM = "line_item"  # normalized name: "revenue" | "net_income" | "total_assets" | ...

FINANCIALS_COLUMNS = [
    CIK,
    FISCAL_YEAR,
    FISCAL_PERIOD,
    START_DATE,
    END_DATE,
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

_COLUMN_TYPE_NAMES = {
    CIK: "int64",
    ENTITY_NAME: "string",
    TAXONOMY: "string",
    CONCEPT: "string",
    UNIT: "string",
    FACT_TYPE: "string",
    START_DATE: "string",
    END_DATE: "string",
    FISCAL_YEAR: "int64",
    FISCAL_PERIOD: "string",
    FORM: "string",
    FILED_DATE: "string",
    ACCESSION_NUMBER: "string",
    FRAME: "string",
    DIMENSIONS: "string",
    CONTEXT_ID: "string",
    VALUE: "float64",
    STATEMENT: "string",
    LINE_ITEM: "string",
    TICKER: "string",
    FILING_PERIOD_OF_REPORT: "string",
    FILING_PRIMARY_DOCUMENT: "string",
    FILING_IS_XBRL: "bool",
}


def pyarrow_schema(columns: list[str]) -> pa.Schema:
    """An explicit PyArrow schema for `columns` (one of the *_COLUMNS lists
    above) -- see this module's own docstring for why an explicit,
    per-column-name schema is required here rather than letting
    pa.Table.from_pandas() infer types independently for every file."""
    import pyarrow as pa

    types = {"int64": pa.int64(), "string": pa.string(), "float64": pa.float64(), "bool": pa.bool_()}
    return pa.schema([pa.field(name, types[_COLUMN_TYPE_NAMES[name]]) for name in columns])


def empty_facts_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=FACTS_COLUMNS)


def empty_financials_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=FINANCIALS_COLUMNS)


def empty_submissions_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SUBMISSIONS_COLUMNS)
