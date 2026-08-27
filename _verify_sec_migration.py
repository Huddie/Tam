from tam.research.data.sec import SEC
from tam.research.data.sec.store import SecStore
from scripts.migrate_sec_layout import _list_old_keys

store = SecStore()
old_keys = _list_old_keys(store)
print(f"{len(old_keys)} old-style object(s) remaining.")
for old_key, new_key in old_keys[:10]:
    print(f"  {old_key}  ->  {new_key}")

print()
sec = SEC()
print(sec.financials(tickers=["AAPL"], statement="income_statement").head())
