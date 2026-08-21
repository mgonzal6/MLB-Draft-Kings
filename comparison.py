from pathlib import Path
import pandas as pd

file_path = Path(r"G:\My Drive\DK\export\2026-mlb-season-player-feed.parquet")
df = pd.read_parquet(file_path)

cols = list(df.columns)
renamed = {}
for i, col in enumerate(cols):
    if 8 <= i <= 28:
        renamed[col] = f"Batting {col}"
    elif i >= 29:
        renamed[col] = f"Pitching {col}"
df.rename(columns=renamed, inplace=True)

# Actuals are scored with the SAME shared formulas the projections use.
# (The old inline version here added a QS x4 bonus DK Classic doesn't award,
# and multiplied raw "6.2" IP by 2.25 instead of converting to 6 2/3 innings —
# both inflated pitcher actuals relative to the projections.)
from dk_scoring import dk_pitcher, dk_hitter

batting_dk  = df.apply(lambda r: dk_hitter(r,  prefix='Batting '),  axis=1)
pitching_dk = df.apply(lambda r: dk_pitcher(r, prefix='Pitching '), axis=1)

df['DK_Batting_Score']  = batting_dk.round(2)
df['DK_Pitching_Score'] = pitching_dk.round(2)
df['DK_Total_Score']    = pitching_dk.where(pitching_dk != 0, batting_dk).round(2)

# --- Projections ---
proj = pd.concat(
    [pd.read_csv(f) for f in Path(r"G:\My Drive\DK\Projections").glob("*.csv")],
    ignore_index=True
)

proj['proj'] = proj['blended'].where(proj['blended'] != 0, proj['avg26'])

# Normalize dates for join — extract date portion from slate (e.g. "05/25 Evening" → 2026-05-25)
proj['join_date'] = pd.to_datetime(
    proj['slate'].str.extract(r'(?<!\d)(\d{1,2}/\d{1,2})')[0] + '/2026',
    format='%m/%d/%Y'
).dt.normalize()

df['join_date'] = pd.to_datetime(df['DATE'], errors='coerce').dt.normalize()

# --- Join on player + date ---
comparison = (df
              .merge(proj[['name','join_date','slate','pos','salary','bs','avg26','blended','proj']],
                     left_on=['PLAYER','join_date'], right_on=['name','join_date'],
                     how='inner')
              .drop(columns='name'))

comparison['proj']           = pd.to_numeric(comparison['proj'],           errors='coerce')
comparison['DK_Total_Score'] = pd.to_numeric(comparison['DK_Total_Score'], errors='coerce')
comparison['diff']           = (comparison['DK_Total_Score'] - comparison['proj']).round(2)

comparison = comparison.sort_values('diff', ascending=False)

comparison.to_csv(r"G:\My Drive\DK\export\comparison.csv", index=False)

