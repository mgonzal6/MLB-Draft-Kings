r"""Finding and reading the two manual DK downloads in G:\My Drive\DK\load.

Shared by preflight.py and filtered_DK_Salaries.py so the two can never
disagree about which file is today's input or how to parse it — the same
drift that put dk_scoring.py in this repo.

Both scripts used to require '.csv'. On 08/26 the lineups feed arrived as
Lineups_2026_08_26.xlsx and preflight reported the file simply missing, which
reads as "not posted yet" rather than "wrong extension". DK's own salary
export has the opposite habit — a file named .csv that is really an xlsx —
so trust the magic bytes, not the extension, in both directions.
"""
import os

import pandas as pd

LOAD = r"G:\My Drive\DK\load"
EXTS = (".csv", ".xlsx", ".xlsm")


def read_table(path):
    """Read a load-dir file as a DataFrame, whatever DK actually served.

    Strips the UTF-8 BOM off the header names: DK's CSV export carries one
    (08/26's DKSalaries starts EF BB BF), which otherwise rides along on the
    first column name and breaks lookups by that name.
    """
    with open(path, "rb") as fh:
        magic = fh.read(2)
    df = pd.read_excel(path) if magic == b"PK" else pd.read_csv(path)
    df.columns = [str(c).replace("\ufeff", "").replace("ï»¿", "").strip()
                  for c in df.columns]
    return df


def find_inputs(folder=LOAD):
    """-> (lineups_path, dk_salaries_path). Either may be None.

    Last match wins, as it always has, but over a sorted listing so the winner
    is deterministic rather than whatever order the filesystem returned.
    Skips Excel's '~$' lock files, which appear while a workbook is open and
    otherwise look exactly like the input they shadow.
    """
    lineups = salaries = None
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return None, None
    for fn in names:
        low = fn.lower()
        if fn.startswith("~$") or not low.endswith(EXTS) or "unmatched" in low:
            continue
        if "lineup" in low:
            lineups = os.path.join(folder, fn)
        elif "dksalaries" in low:
            salaries = os.path.join(folder, fn)
    return lineups, salaries
