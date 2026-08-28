import io
from tam.research.data.sec.store import SecStore
from tam.research.data.sec import schema
import pyarrow.parquet as pq

store = SecStore()

# Pick one real financials file to inspect -- adjust the year if this one
# doesn't exist in your bucket (check with _check_sec_r2.py's output).
year = 2023
key = store._financials_key(year)
body = store._read_bytes(key)
if body is None:
    print(f"{key} does not exist -- try a different year.")
else:
    pf = pq.ParquetFile(io.BytesIO(body))
    print(f"{key}")
    print(f"{pf.num_row_groups} row group(s), {pf.metadata.num_rows} total rows")
    cik_idx = pf.schema_arrow.names.index("cik")
    for i in range(pf.num_row_groups):
        stats = pf.metadata.row_group(i).column(cik_idx).statistics
        print(f"  row group {i}: cik range [{stats.min}, {stats.max}]")
    if pf.num_row_groups == 1:
        print("\n=> Still ONE row group spanning the whole file -- the re-encode has NOT happened yet (or ran before the fix was live). Re-run reconcile_sec_parquet_schema.py + rebuild_sec_financials.py now.")
    else:
        print(f"\n=> {pf.num_row_groups} row groups with narrow CIK ranges -- the fix IS live on this file.")
