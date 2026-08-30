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


# ── unconfirmed lineups ──────────────────────────────────────────────────────
# The feed marks each row confirmed Y or N. Every script normally accepts only
# Y, which is the right default: an unconfirmed batting order is a projection,
# and a player who ends up scratched scores zero. But on a late slate the
# lineups for the later games have not posted by lock, and refusing to build
# at all is worse than building on the projected order.
#
# Opt in with DK_ALLOW_UNCONFIRMED=1, or --allow-unconfirmed where a script
# takes arguments. One switch honoured everywhere, so the SP pool, the Vegas
# adjustment, the hitter pool and the upload validator can never disagree
# about which rows are usable -- the same reason read_table lives here.
ALLOW_ENV = "DK_ALLOW_UNCONFIRMED"


def allow_unconfirmed():
    """True when confirmed=N rows should be treated as usable."""
    return os.environ.get(ALLOW_ENV, "").strip().lower() in ("1", "true", "yes", "y")


def set_allow_unconfirmed(on=True):
    """Set the switch for this process and anything it spawns."""
    os.environ[ALLOW_ENV] = "1" if on else ""


def confirmed_mask(conf, allow=None):
    """Rows to accept, from the lineups feed's confirmed column.

    Y is always accepted; N only when the switch is on. Anything else (blank,
    junk) is never accepted, so a malformed feed cannot quietly widen the pool.
    """
    if allow is None:
        allow = allow_unconfirmed()
    c = conf.astype(str).str.strip().str.upper()
    return c.isin(("Y", "N")) if allow else (c == "Y")


def unconfirmed_banner(n_unconf, what):
    """Loud, uniform warning. Silent when nothing unconfirmed was used."""
    if not n_unconf:
        return
    print("*" * 68)
    print("*** USING %d UNCONFIRMED %s (confirmed=N)." % (n_unconf, what))
    print("*** Batting orders are projections, not posted lineups, and a")
    print("*** scratched player scores zero. Rebuild once lineups post.")
    print("*" * 68)
