from tam.research.data.sec import SEC
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

sec = SEC()
df = sec.query(
    """
    SELECT cik, concept, unit, fact_type, start_date, end_date, fiscal_year, fiscal_period,
           form, filed_date, accession_number, value
    FROM sec_facts('AAPL')
    WHERE concept = 'CostOfGoodsAndServicesSold' AND accession_number = '0001193125-09-153165'
    ORDER BY start_date, end_date
    """
)
print(df)
