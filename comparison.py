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

def to_num(col):
    return pd.to_numeric(df[col], errors='coerce').fillna(0)

batting_dk = (
    to_num('Batting 1B')  * 3   +
    to_num('Batting 2B')  * 5   +
    to_num('Batting 3B')  * 8   +
    to_num('Batting HR')  * 10  +
    to_num('Batting RBI') * 2   +
    to_num('Batting R')   * 2   +
    to_num('Batting BB')  * 2   +
    to_num('Batting HBP') * 2   +
    to_num('Batting SB')  * 5
)

pitching_dk = (
    to_num('Pitching IP')        * 2.25 +
    to_num('Pitching SO.1')      * 2    +
    to_num('Pitching W')         * 4    +
    to_num('Pitching ER')        * -2   +
    to_num('Pitching H.1')       * -0.6 +
    to_num('Pitching BB.1')      * -0.6 +
    to_num('Pitching HB')        * -0.6 +
    to_num('Pitching CG')        * 2.5  +
    to_num('Pitching CG\nSHO')   * 2.5  +
    to_num('Pitching NH')        * 5    +
    to_num('Pitching QS')        * 4
)

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

