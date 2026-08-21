from pathlib import Path
import pandas as pd

file_path_load   = Path(r"G:\.shortcut-targets-by-id\1ixBGWOkSnKSBFHz3CJ47uE28nnPz0q3b\26-mlb-player")
file_path_export = Path(r"G:\My Drive\DK\export")

# Pick the most recently modified xlsx in the source folder
xlsx_files = list(file_path_load.glob("*.xlsx"))
if not xlsx_files:
    raise FileNotFoundError(f"No .xlsx files found in {file_path_load}")

xlsx_path    = max(xlsx_files, key=lambda f: f.stat().st_mtime)
parquet_path = file_path_export / f"{xlsx_path.stem}.parquet"
parquet_path_save = file_path_export / "2026-mlb-season-player-feed.parquet"

print(f"Source: {xlsx_path.name}")

df = pd.read_excel(xlsx_path, header=1, dtype=str)
df = df.iloc[1:].reset_index(drop=True)

df.to_parquet(parquet_path_save, index=False)

print(f"Saved:   {parquet_path_save}")
print(f"Rows: {len(df):,}  |  Columns: {len(df.columns)}")
print(f"Columns: {list(df.columns)}")
