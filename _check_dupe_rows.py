import pandas as pd

from tam.research.data.sec import SEC

sec = SEC()
df = sec.financials(tickers=["AAPL"], statement="income_statement")
subset = df[(df["fiscal_year"] == 2009) & (df["fiscal_period"] == "Q3") & (df["line_item"] == "cost_of_revenue")]

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
print(subset)
