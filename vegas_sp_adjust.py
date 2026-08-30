r"""
vegas_sp_adjust.py — Derive implied team totals from raw odds, then write a
                     Vegas-adjusted SP table for the DraftKings MLB pipeline.

Sits BETWEEN mlb_odds.py (which writes the raw bookmaker rows) and the lineup
build. Produces two clean artifacts the build can trust:

    1. vegas.csv               one row per team:
                               team, game_total, implied_total, opp_implied, is_home
    2. pitcher_bs_cache_adj.csv  the SP cache + Vegas columns:
                               ...original cols..., vegas_adj, adj_bs, adj_blended

Implements Fix #11 / #13 from the baseball-analyzer skill: the cache `bs` and
`blended` are computed WITHOUT Vegas. This applies the game-total and
opponent-implied penalty to BOTH, so the build can tier SPs by vegas-adjusted
blended (the grounded run-prevention number) instead of raw breakout BS.

Usage:
    python vegas_sp_adjust.py                 # uses the folder paths below
    python vegas_sp_adjust.py --selftest      # run the built-in math checks, no files

Inputs (read from LOAD/EXPORT dirs, first match wins):
    mlb_odds.csv            (from mlb_odds.py --csv)
    pitcher_bs_cache.csv    (your local cache builder)
    Filtered_Lineups.csv    (from filtered_DK_Salaries.py)

Design notes:
    * Averages totals/spreads across ALL bookmakers present (robust to a missing book).
    * implied_total = game_total/2 - team_spread/2 ; opp_implied = game_total - implied_total.
    * Coerces blank/garbage numeric cells to NaN with a printed report — a single
      empty cell will never again halt a build with float('').
    * Full 30-team name->abbrev map using DK conventions (WSH, ATH, CWS).
"""

import argparse
import os
import re
import sys
import pandas as pd

import slate_io

# ---------------------------------------------------------------------------
# Folder convention (matches filtered_DK_Salaries.py). Falls back to the
# current directory if the G: drive isn't present (e.g. running elsewhere).
# ---------------------------------------------------------------------------
LOAD_DIR   = r"G:\My Drive\DK\load"
EXPORT_DIR = r"G:\My Drive\DK\export"


def _resolve_dirs():
    load = LOAD_DIR if os.path.isdir(LOAD_DIR) else "."
    export = EXPORT_DIR if os.path.isdir(EXPORT_DIR) else "."
    return load, export


def find_file(name, *dirs):
    """Return the first existing path for `name` across the given dirs."""
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Team name -> DK abbreviation (Odds API uses full names; caches use abbrevs).
# Uses DK conventions: Washington=WSH, Athletics=ATH, White Sox=CWS.
# ---------------------------------------------------------------------------
TEAM_ABBR = {
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS", "New York Yankees": "NYY",
    "Tampa Bay Rays": "TB", "Toronto Blue Jays": "TOR",
    "Chicago White Sox": "CWS", "Cleveland Guardians": "CLE", "Detroit Tigers": "DET",
    "Kansas City Royals": "KC", "Minnesota Twins": "MIN",
    "Houston Astros": "HOU", "Los Angeles Angels": "LAA", "Athletics": "ATH",
    "Oakland Athletics": "ATH", "Seattle Mariners": "SEA", "Texas Rangers": "TEX",
    "Atlanta Braves": "ATL", "Miami Marlins": "MIA", "New York Mets": "NYM",
    "Philadelphia Phillies": "PHI", "Washington Nationals": "WSH",
    "Chicago Cubs": "CHC", "Cincinnati Reds": "CIN", "Milwaukee Brewers": "MIL",
    "Pittsburgh Pirates": "PIT", "St. Louis Cardinals": "STL",
    "Arizona Diamondbacks": "ARI", "Colorado Rockies": "COL",
    "Los Angeles Dodgers": "LAD", "San Diego Padres": "SD", "San Francisco Giants": "SF",
}

# Lineups CSV may still carry legacy codes; normalize to DK abbrevs.
ABBR_REMAP = {"OAK": "ATH", "WAS": "WSH", "CHW": "CWS"}


def abbr(full_name):
    if full_name in TEAM_ABBR:
        return TEAM_ABBR[full_name]
    # last-ditch: maybe it's already an abbreviation
    return ABBR_REMAP.get(full_name, full_name)


# ---------------------------------------------------------------------------
# Date handling — the API returns a multi-day board (6/17 + 6/18 + 6/19...),
# so a same-matchup series (e.g. PIT@ATH three nights running) must be split
# by date BEFORE grouping, or three nights' totals/run lines get averaged
# together into garbage. commence_time is UTC; a 9:40pm ET game is already
# 6/18 in UTC, so convert to Eastern before taking the calendar date.
# ---------------------------------------------------------------------------
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover
    _ET = timezone(timedelta(hours=-4))  # EDT fallback (correct Apr–Oct, incl. MLB season)


def et_date(iso_utc):
    """Return the US/Eastern calendar date (datetime.date) for a UTC ISO stamp."""
    if iso_utc is None or str(iso_utc).strip() == "":
        return None
    s = str(iso_utc).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET).date()


def slate_date_from_dk(dk_df):
    """Extract the slate's calendar date from DKSalaries 'Game Info' (MM/DD/YYYY)."""
    import re
    for gi in dk_df.get("Game Info", pd.Series(dtype=str)).dropna():
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(gi))
        if m:
            mm, dd, yyyy = map(int, m.groups())
            return datetime(yyyy, mm, dd).date()
    return None


# ---------------------------------------------------------------------------
# Numeric hygiene — the thing that crashed the 6/17 build (float('')).
# ---------------------------------------------------------------------------
def coerce_numeric(df, cols, label):
    """Coerce listed columns to numeric, reporting any cells that go NaN."""
    for c in cols:
        if c not in df.columns:
            continue
        before = df[c].copy()
        df[c] = pd.to_numeric(df[c], errors="coerce")
        bad = df[df[c].isna() & before.notna() & (before.astype(str).str.strip() != "")]
        blank = df[df[c].isna() & (before.isna() | (before.astype(str).str.strip() == ""))]
        for _, r in pd.concat([bad, blank]).iterrows():
            who = r.get("PLAYER", r.get("Name", r.get("player name", "?")))
            print(f"  [{label}] blank/non-numeric '{c}' for {who} -> NaN")
    return df


# ---------------------------------------------------------------------------
# Step 1 — implied team totals from raw odds.
#
# Two robustness guards added 6/17/26 after a fresh full-board odds pull
# produced a corrupt vegas.csv (PIT implied 8.71, ATH listed twice):
#   * slate_teams filter — only keep games where BOTH teams are on the DK
#     slate, so a multi-day odds pull can't inject off-slate / duplicate games.
#   * MEDIAN (not mean) for totals AND spreads, plus a |point|<=2.5 run-line
#     filter, so alternate run lines or one outlier book can't blow up the
#     average. A -6.0 mean spread is what created the 8.71 bug.
# ---------------------------------------------------------------------------
def _median_run_line(series):
    """Median of standard run-line points (drops alternate lines > 2.5)."""
    s = series.dropna()
    s = s[s.abs() <= 2.5]
    return float(s.median()) if not s.empty else None


def build_vegas(odds_df, slate_teams=None, slate_date=None):
    odds_df = odds_df.copy()
    odds_df["point"] = pd.to_numeric(odds_df["point"], errors="coerce")

    # Date filter FIRST — isolate tonight's games so a multi-day board can't
    # merge a same-matchup series across nights when we group by (away, home).
    if slate_date is not None and "commence_time" in odds_df.columns:
        odds_df["_et_date"] = odds_df["commence_time"].map(et_date)
        before = odds_df["_et_date"].nunique()
        odds_df = odds_df[odds_df["_et_date"] == slate_date]
        if before > 1:
            print(f"  Date filter: kept {slate_date} only "
                  f"({len(odds_df)} rows; board spanned {before} dates).")
        if odds_df.empty:
            print(f"  WARNING: no odds rows match slate date {slate_date}.")

    rows = []
    for (away, home), g in odds_df.groupby(["away_team", "home_team"]):
        a_ab, h_ab = abbr(away), abbr(home)
        # Slate filter: skip any game not fully on tonight's DK slate.
        if slate_teams is not None and not ({a_ab, h_ab} <= set(slate_teams)):
            continue

        totals = g[(g["market"] == "totals") & (g["outcome"] == "Over")]["point"].dropna()
        if totals.empty:
            print(f"  WARNING: no totals for {away} @ {home} — skipped")
            continue
        game_total = float(totals.median())

        sp = g[g["market"] == "spreads"]
        away_spread = _median_run_line(sp[sp["outcome"] == away]["point"])
        home_spread = _median_run_line(sp[sp["outcome"] == home]["point"])
        # If a side's run line is missing, infer it as the negation of the other.
        if away_spread is None and home_spread is None:
            away_spread = home_spread = 0.0
        elif away_spread is None:
            away_spread = -home_spread
        elif home_spread is None:
            home_spread = -away_spread

        away_implied = game_total / 2 - away_spread / 2
        home_implied = game_total / 2 - home_spread / 2

        for team_ab, implied, is_home in ((a_ab, away_implied, 0), (h_ab, home_implied, 1)):
            # Sanity guard — flag physically implausible implied totals.
            if not (1.5 <= implied <= 8.0):
                print(f"  WARNING: {team_ab} implied {implied:.2f} is out of range "
                      f"[1.5, 8.0] — check the odds rows for {away} @ {home}.")
            rows.append({"team": team_ab, "game_total": round(game_total, 2),
                         "implied_total": round(implied, 2),
                         "opp_implied": round(game_total - implied, 2), "is_home": is_home})

    vdf = pd.DataFrame(rows)
    if vdf.empty:
        return vdf
    # Dedupe: if a team still appears twice (shouldn't after slate filter), keep
    # the row whose game is on the slate — here, simply drop exact duplicates.
    dupes = vdf["team"][vdf["team"].duplicated()].unique()
    if len(dupes):
        print(f"  WARNING: team(s) appear in >1 game after filtering: {list(dupes)} "
              f"— odds file may span multiple days; keeping first.")
        vdf = vdf.drop_duplicates(subset="team", keep="first")
    return vdf.sort_values("implied_total", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 2 — Vegas adjustment (Fix #11), applied to BOTH bs and blended (Fix #13).
# ---------------------------------------------------------------------------
def vegas_adj(game_total, opp_implied):
    adj = 0
    if game_total >= 9.5:
        adj -= 10
    elif game_total >= 8.5:
        adj -= 5
    elif game_total <= 7.0:
        adj += 5
    if opp_implied >= 5.5:
        adj -= 5
    elif opp_implied >= 4.5:
        adj -= 2
    return adj


# Same rules as build_portfolio.norm and validate_upload.norm. This one joins
# the BS cache to the lineups feed, and the two spell names differently: the
# cache carries "Matthew Liberatore" while the feed says "Matt". Without the
# nickname map he matched nothing, dropped out of pitcher_bs_cache_adj.csv,
# and then surfaced downstream as build_portfolio's misleading "SP has no
# cache/vegas row" -- a confirmed starter silently absent from the SP pool.
NICKS = {"michael": "mike", "leonardo": "leo", "matthew": "matt",
         "christopher": "chris", "enrique": "kike"}


def normalize_name(s):
    s = str(s).lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    for ch in ".,'-":
        s = s.replace(ch, "")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return " ".join(NICKS.get(t, t) for t in s.split() if len(t) > 1)


def build_sp_adjust(pit_df, lineups_df, vegas_df):
    """Identify confirmed SPs, join their game's Vegas line, adjust bs+blended."""
    pit_df = pit_df.copy()
    pit_df = coerce_numeric(pit_df, ["bs", "blended"], "pitcher_cache")

    # Confirmed SPs from the lineups file (the authoritative SP source).
    lu = lineups_df.copy()
    lu.columns = lu.columns.str.strip()
    lu["bo"] = lu["batting order"].astype(str).str.strip().str.upper()
    lu["conf"] = lu["confirmed"].astype(str).str.strip().str.upper()
    sps = lu[(lu["bo"] == "SP") & slate_io.confirmed_mask(lu["conf"])].copy()
    slate_io.unconfirmed_banner(int((sps["conf"] != "Y").sum()),
                                "STARTING PITCHERS")
    sps["team"] = sps["team code"].astype(str).str.strip().replace(ABBR_REMAP)

    if vegas_df.empty or "team" not in vegas_df.columns:
        print("  WARNING: empty Vegas table — all SP adjustments default to 0.")
        vmap = {}
    else:
        vmap = vegas_df.set_index("team")[["game_total", "opp_implied"]].to_dict("index")
    pit_df["_n"] = pit_df["PLAYER"].map(normalize_name)
    norm_to_team = {normalize_name(r["player name"]): r["team"] for _, r in sps.iterrows()}

    out = []
    for _, r in pit_df[pit_df["_n"].isin(norm_to_team)].iterrows():
        team = norm_to_team[r["_n"]]
        v = vmap.get(team)
        if v is None:
            print(f"  WARNING: no Vegas line for {r['PLAYER']} ({team}) — adj=0")
            adj = 0
        else:
            adj = vegas_adj(v["game_total"], v["opp_implied"])
        row = r.drop(labels=["_n"]).to_dict()
        row["team"] = team
        row["vegas_adj"] = adj
        row["adj_bs"] = round(r["bs"] + adj, 2) if pd.notna(r["bs"]) else None
        row["adj_blended"] = round(r["blended"] + adj, 2) if pd.notna(r["blended"]) else None
        out.append(row)

    adj_df = pd.DataFrame(out)
    if not adj_df.empty:
        adj_df = adj_df.sort_values("adj_blended", ascending=False).reset_index(drop=True)
    return adj_df


# ---------------------------------------------------------------------------
# Self-test — locks the math to the 6/17/26 hand-derived numbers.
# ---------------------------------------------------------------------------
def selftest():
    ok = True

    # Vegas math: PIT@ATH, O/U 10.44, PIT favored by 1.5.
    gt, away_spread = 10.44, -1.50
    pit_implied = gt / 2 - away_spread / 2
    assert abs(pit_implied - 5.97) < 0.01, pit_implied
    assert abs((gt - pit_implied) - 4.47) < 0.01

    # Vegas math: BAL@SEA, O/U 7.68, SEA favored by 1.5.
    gt2, home_spread = 7.68, -1.50
    sea_implied = gt2 / 2 - home_spread / 2
    assert abs(sea_implied - 4.59) < 0.01, sea_implied
    assert abs((gt2 - sea_implied) - 3.09) < 0.01  # BAL

    # SP adjustments (Fix #11) for the four 6/17 arms:
    assert vegas_adj(7.68, 4.59) == -2,  "Bradish vs SEA 4.59 in 7.68 game"   # opp>=4.5
    assert vegas_adj(7.68, 3.09) == 0,   "Kirby vs BAL 3.09 in 7.68 game"     # nothing trips
    assert vegas_adj(10.44, 4.47) == -10, "Ashcraft vs ATH 4.47 in 10.44"     # total>=9.5
    assert vegas_adj(10.44, 5.97) == -15, "Civale vs PIT 5.97 in 10.44"       # total + opp>=5.5

    # adj_blended ranking flips the anchor away from the high-BS narrative arm:
    sp = {  # (raw_bs, raw_blended, game_total, opp_implied)
        "Kirby":    (36.8, 15.61, 7.68, 3.09),
        "Bradish":  (13.8, 16.44, 7.68, 4.59),
        "Ashcraft": (57.6, 17.01, 10.44, 4.47),
        "Civale":   (18.1, 10.64, 10.44, 5.97),
    }
    adj_blended = {k: round(v[1] + vegas_adj(v[2], v[3]), 2) for k, v in sp.items()}
    anchor = max(adj_blended, key=adj_blended.get)
    assert anchor == "Kirby", (anchor, adj_blended)
    # The highest RAW bs (Ashcraft) must NOT be the anchor once Vegas is applied:
    raw_bs_leader = max(sp, key=lambda k: sp[k][0])
    assert raw_bs_leader == "Ashcraft" and anchor != raw_bs_leader

    print("SELFTEST PASSED")
    print("  Implied totals: BAL 3.09 | SEA 4.59 | PIT 5.97 | ATH 4.47")
    print("  Vegas adj:      Kirby 0 | Bradish -2 | Ashcraft -10 | Civale -15")
    print(f"  adj_blended:    {adj_blended}")
    print(f"  -> anchor by adj_blended = {anchor} (raw-BS leader was {raw_bs_leader})")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Derive implied totals + Vegas-adjusted SP table.")
    ap.add_argument("--selftest", action="store_true", help="run math checks only, write nothing")
    ap.add_argument("--allow-unconfirmed", action="store_true",
                    help="accept confirmed=N SPs from the lineups feed")
    args = ap.parse_args()
    if getattr(args, "allow_unconfirmed", False):
        slate_io.set_allow_unconfirmed(True)

    if args.selftest:
        selftest()
        return

    load, export = _resolve_dirs()
    odds_p = find_file("mlb_odds.csv", export, load, ".")
    pit_p  = find_file("pitcher_bs_cache.csv", load, export, ".")
    lu_p   = find_file("Filtered_Lineups.csv", export, load, ".")
    dk_p   = find_file("Filtered_DKSalaries.csv", export, load, ".")

    missing = [n for n, p in [("mlb_odds.csv", odds_p),
                              ("pitcher_bs_cache.csv", pit_p),
                              ("Filtered_Lineups.csv", lu_p)] if p is None]
    if missing:
        sys.exit("Missing required input(s): " + ", ".join(missing))

    print(f"odds:    {odds_p}")
    print(f"pitcher: {pit_p}")
    print(f"lineups: {lu_p}")
    print(f"salaries:{dk_p}")
    print("-" * 60)

    odds_df = pd.read_csv(odds_p)
    pit_df = pd.read_csv(pit_p)
    lineups_df = pd.read_csv(lu_p)

    # Restrict the odds to tonight's DK slate so a multi-day board can't
    # inject off-slate or duplicate games into vegas.csv.
    slate_teams = None
    slate_date = None
    if dk_p is not None:
        dk_df = pd.read_csv(dk_p)
        dk_df.columns = dk_df.columns.str.strip()
        slate_teams = set(dk_df["TeamAbbrev"].astype(str).str.strip().replace(ABBR_REMAP))
        slate_date = slate_date_from_dk(dk_df)
        print(f"slate teams ({len(slate_teams)}): {sorted(slate_teams)}")
        print(f"slate date: {slate_date}")

        # Staleness guard — the 8/14-8/20 failure loop: DK files from 6/17 were
        # still in the load folder, so the date filter matched zero odds rows
        # and the script crashed every scheduled run. Catch it here with a
        # message that says exactly what to do.
        from datetime import date
        if slate_date is not None and slate_date < date.today():
            days_old = (date.today() - slate_date).days
            sys.exit(
                f"\nSTALE INPUT: the DK salary/lineup files are for {slate_date} "
                f"({days_old} days ago).\n"
                f"Download today's Lineups + DKSalaries CSVs into {load} and rerun:\n"
                f"  1. filtered_DK_Salaries.py  (refreshes Filtered_* in export)\n"
                f"  2. vegas_sp_adjust.py\n"
                f"Skipping the Vegas build — vegas.csv was NOT modified."
            )
    else:
        print("WARNING: Filtered_DKSalaries.csv not found — vegas.csv will include "
              "EVERY game/date in the odds file (no slate or date filter).")
    print("-" * 60)

    print("Building vegas.csv ...")
    vegas_df = build_vegas(odds_df, slate_teams=slate_teams, slate_date=slate_date)
    if vegas_df.empty:
        sys.exit(
            "\nERROR: no Vegas rows were built — the odds file has no games "
            "matching the slate's teams/date.\n"
            "Likely causes: stale mlb_odds.csv (rerun mlb_odds.py --csv mlb_odds.csv) "
            "or stale DK files in the load folder.\n"
            "vegas.csv was NOT modified."
        )
    print(vegas_df.to_string(index=False))
    vegas_out = os.path.join(export, "vegas.csv")
    vegas_df.to_csv(vegas_out, index=False)
    print(f"  wrote {vegas_out}")
    print("-" * 60)

    print("Building pitcher_bs_cache_adj.csv ...")
    adj_df = build_sp_adjust(pit_df, lineups_df, vegas_df)
    if adj_df.empty:
        # Distinguish "pulled too early, nothing confirmed yet" from a real
        # name-match bug — they need completely different responses.
        _lu = lineups_df.copy()
        _lu.columns = _lu.columns.str.strip()
        _bo = _lu["batting order"].astype(str).str.strip().str.upper()
        _cf = _lu["confirmed"].astype(str).str.strip().str.upper()
        n_sp = int((_bo == "SP").sum())
        n_conf = int(((_bo == "SP") & (_cf == "Y")).sum())
        if n_sp and not n_conf:
            print(f"  WARNING: {n_sp} SPs listed but NONE are confirmed=Y yet — "
                  f"the lineups file was pulled too early. Re-download closer "
                  f"to lock. No adjusted SP table written.")
        else:
            print("  WARNING: no confirmed SPs matched the cache — "
                  "check name normalization.")
    else:
        show = ["PLAYER", "team", "bs", "adj_bs", "blended", "adj_blended", "vegas_adj"]
        print(adj_df[show].to_string(index=False))
        adj_out = os.path.join(export, "pitcher_bs_cache_adj.csv")
        # Stamp the slate this table was built for. This file is only
        # rewritten when the step above succeeds, so an aborted run leaves the
        # PREVIOUS slate's table sitting exactly where build_portfolio.py
        # reads it — and nothing in the file itself said which day it was for.
        # build_portfolio.py refuses to build on a mismatched stamp.
        adj_df = adj_df.copy()
        adj_df["slate_date"] = slate_date.isoformat() if slate_date else ""
        adj_df.to_csv(adj_out, index=False)
        print(f"  wrote {adj_out}  (stamped slate_date={slate_date})")
        # Surface the anchor call the way the build should read it.
        top = adj_df.iloc[0]
        bs_leader = adj_df.loc[adj_df["bs"].idxmax(), "PLAYER"]
        print("-" * 60)
        print(f"ANCHOR by adj_blended: {top['PLAYER']} ({top['adj_blended']})")
        if bs_leader != top["PLAYER"]:
            print(f"  NOTE: raw-BS leader is {bs_leader} — Fix #13 says do NOT anchor on him.")


if __name__ == "__main__":
    main()
