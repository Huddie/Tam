"""normalize_facts(): maps the raw XBRL concept space onto EdgarTools' own
maintained concept-standardization table (edgar.standardization) instead
of a hand-rolled one -- an earlier version of this module had its own
~13-entry CONCEPT_ALIASES dict, which a live check against real AAPL data
confirmed was already missing aliases edgartools' own `revenue` group
already had ("Revenue", "SalesRevenueGoodsNet", "TotalRevenues",
"OperatingRevenue"). edgar.standardization has no network/heavy-XBRL-
parsing dependency of its own (just a pure-Python lookup table), so
importing it here doesn't pull in EdgarTools' heavier filing-fetch
machinery -- only tam.research.data.sec's raw_filings cache (a different
module) does that.

What edgartools' synonym table does NOT solve for OUR shape: period
deduplication across multiple accession numbers reporting the SAME true
period as a comparative column. Confirmed live against AAPL's real
companyfacts data: the exact same FY2022 revenue value appears under
THREE different accession numbers -- the 2022 10-K's own current year,
then again as a comparative column in the 2023 and 2024 10-Ks -- each
carrying a DIFFERENT fiscal_year label (2022/2023/2024 respectively).
SEC's own per-fact `fy`/`fp` reflect which FILING reported that entry, not
a reliable period identity across filings -- deduping by the raw
start/end dates (the period's actual, stable identity) instead of that
label is what fixes it. Kept as this module's own logic since it's
specific to the companyfacts-JSON raw-facts shape tam.research.data.sec's
provider.py fetches, not something edgartools' own (heavier, per-filing-
XBRL-instance-based) stitching machinery operates on.
"""

from __future__ import annotations

import pandas as pd
from edgar.standardization import get_synonym_groups

from . import schema

_synonyms = get_synonym_groups()


def _dedup_same_period(group: pd.DataFrame, is_duration: bool) -> pd.DataFrame:
    """Collapse multiple accession numbers reporting the identical true
    period (same start/end dates) down to whichever was FILED FIRST -- the
    period's own original, primary report, not a later filing's
    comparative echo of it (see module docstring)."""
    period_cols = [schema.START_DATE, schema.END_DATE] if is_duration else [schema.END_DATE]
    key = [schema.CIK] + period_cols
    return group.sort_values(schema.FILED_DATE).drop_duplicates(subset=key, keep="first")


def normalize_facts(facts_df: pd.DataFrame) -> pd.DataFrame:
    """Raw FACTS_COLUMNS rows -> FINANCIALS_COLUMNS long-format rows,
    naming/categorizing each row via edgartools' own concept-
    standardization table. A concept edgartools doesn't recognize is
    simply not represented in the output -- this never invents a partial/
    best-guess line item for anything outside that table."""
    if facts_df.empty:
        return schema.empty_financials_frame()

    concept_info = {concept: _synonyms.identify_concept(concept) for concept in facts_df[schema.CONCEPT].unique()}
    working = facts_df.copy()
    working["_line_item"] = working[schema.CONCEPT].map(lambda c: concept_info[c].name if concept_info[c] else None)
    working["_statement"] = working[schema.CONCEPT].map(lambda c: concept_info[c].category if concept_info[c] else None)
    recognized = working[working["_line_item"].notna()]
    if recognized.empty:
        return schema.empty_financials_frame()

    pieces = []
    for line_item, group in recognized.groupby("_line_item"):
        is_duration = bool((group[schema.FACT_TYPE] == "duration").any())
        deduped = _dedup_same_period(group, is_duration)
        pieces.append(deduped.assign(**{schema.LINE_ITEM: line_item, schema.STATEMENT: deduped["_statement"].iloc[0]}))

    result = pd.concat(pieces, ignore_index=True)
    return result[schema.FINANCIALS_COLUMNS]
