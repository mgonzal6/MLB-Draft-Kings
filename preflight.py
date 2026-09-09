"""Slate-build preflight gate.

Answers one question before the build spends anything: are today's lineups
actually posted yet?

The 8/24 test run pulled fresh odds (a paid API call) and ran three steps
before vegas_sp_adjust.py discovered that none of the 14 SPs were confirmed
and refused to write the adjusted SP table. That fact was knowable at second
zero from the raw Lineups CSV. This script checks it first, so an early pull
costs nothing but a few milliseconds.

Exit codes:
    0  enough is confirmed to build (possibly with a partial-slate warning)
    1  nothing confirmed yet, or a slate TEAM is entirely unposted
    2  input files missing or unreadable

On 09/08 the 15:43 feed had 18 of 20 teams posted. The build ran anyway and
TOR -- the highest implied total on the slate (5.50) facing the weakest
starter on it (adj_bs -7.21) -- appeared in ZERO of 77 lineups, along with
TEX. Nothing said "you have dropped two teams"; it printed "only 18 of 20
SPs confirmed", which reads like two missing ARMS rather than two missing
OFFENCES. The 9:40 PM ET games had not locked for another 5.9 hours, so
waiting was free. See check_team_coverage.
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

import slate_io

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                        # pragma: no cover
    _ET = timezone(timedelta(hours=-4))  # EDT fallback (correct Apr-Oct)

LOAD = r"G:\My Drive\DK\load"
ROWS_PER_TEAM = 10

# The Lineups feed and DK disagree on three abbreviations. Same map as
# filtered_DK_Salaries.py, build_portfolio.py and vegas_sp_adjust.py.
ABBR_REMAP = {"OAK": "ATH", "WAS": "WSH", "CHW": "CWS"}


def find_inputs():
    lineups, salaries = slate_io.find_inputs(LOAD)
    if lineups is None and salaries is None and not os.path.isdir(LOAD):
        print(f"PREFLIGHT: cannot read {LOAD}")
    return lineups, salaries


def read_salaries(salaries_path):
    try:
        dk = slate_io.read_table(salaries_path)
    except Exception:
        return None
    dk.columns = dk.columns.str.strip()
    return dk


def check_classic(dk, path):
    """Reject anything that is not a DK Classic salary file.

    A Tiers export (Roster Position = T1..T6, no Salary column) sails through
    every earlier check: filtered_DK_Salaries.py only needs Name and
    TeamAbbrev, so the run reaches build_portfolio.py before dying on the
    missing Salary column -- after spending the odds call. Catch it here.
    """
    if dk is None:
        return f"cannot read {os.path.basename(path)}"
    missing = [c for c in ("Salary", "Roster Position", "Game Info",
                           "TeamAbbrev", "Name + ID") if c not in dk.columns]
    if missing:
        kind = ("looks like a TIERS contest file"
                if "Salary" in missing and "Roster Position" in dk.columns
                else "is not a Classic salary export")
        return (f"{os.path.basename(path)} {kind} — missing column(s): "
                f"{', '.join(missing)}")
    slots = set()
    for rp in dk["Roster Position"].astype(str):
        slots.update(rp.split("/"))
    if not slots & {"P", "C", "1B", "2B", "3B", "SS", "OF"}:
        return (f"{os.path.basename(path)} has no Classic roster positions "
                f"(saw {sorted(slots)[:6]}) — wrong contest type")
    return None


def slate_teams(dk):
    """Teams actually on the DK slate, in DK's abbreviations."""
    if dk is None or "TeamAbbrev" not in dk.columns:
        return None
    return set(dk["TeamAbbrev"].astype(str).str.strip().str.upper())


def team_games(dk):
    """{team: (game string, start datetime ET)} for every slate team."""
    out = {}
    if dk is None:
        return out
    for _, r in dk.iterrows():
        team = str(r.get("TeamAbbrev", "")).strip().upper()
        if not team or team in out:
            continue
        gi = str(r.get("Game Info", ""))
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}(?:AM|PM))", gi)
        dt = None
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                       "%m/%d/%Y %I:%M%p").replace(tzinfo=_ET)
            except ValueError:
                dt = None
        out[team] = (gi, dt)
    return out


def check_team_coverage(lu, cf, on_slate):
    """Slate teams with NO confirmed player at all.

    A team whose lineup has not posted is not merely 'missing a starter' --
    every one of its bats is excluded from the hitter pool, so it cannot
    appear in a single lineup. On a 20-team card two such teams is a 10% cut
    to the slate, applied silently and with no signal behind it. The fade
    rules at least act on a number; this acts on a file's timestamp.
    """
    if not on_slate:
        return []
    have = set(lu.loc[cf == "Y", "team code"].astype(str).str.strip()
               .str.upper().replace(ABBR_REMAP))
    return sorted(set(on_slate) - have)


def first_pitch(dk):
    """Earliest start time on the slate, from DKSalaries 'Game Info' (ET)."""
    if dk is None:
        return None
    best = None
    for gi in dk.get("Game Info", pd.Series(dtype=str)).dropna().unique():
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}(?:AM|PM))", str(gi))
        if not m:
            continue
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%m/%d/%Y %I:%M%p")
        except ValueError:
            continue
        dt = dt.replace(tzinfo=_ET)
        best = dt if best is None or dt < best else best
    return best


def main():
    lineups_path, salaries_path = find_inputs()
    if not lineups_path or not salaries_path:
        print("PREFLIGHT: missing Lineups or DKSalaries CSV in the load folder.")
        return 2

    try:
        lu = slate_io.read_table(lineups_path)
    except Exception as e:
        print(f"PREFLIGHT: cannot read {os.path.basename(lineups_path)}: {e}")
        return 2
    lu.columns = lu.columns.str.strip()
    for col in ("batting order", "confirmed", "team code"):
        if col not in lu.columns:
            print(f"PREFLIGHT: {os.path.basename(lineups_path)} has no '{col}' column.")
            return 2

    # The Lineups feed covers the whole day; the DK slate is a subset of it.
    # Count only slate teams, otherwise this reports 20 SPs where every
    # downstream script reports 14.
    dk = read_salaries(salaries_path)
    bad = check_classic(dk, salaries_path)
    if bad:
        print("-" * 60)
        print(f"PREFLIGHT: {bad}")
        print("  This pipeline builds DK Classic lineups only. Download the")
        print("  Classic slate's DKSalaries file, or run the contest by hand.")
        print("  Nothing was spent -- no odds API call was made.")
        print("-" * 60)
        return 2
    on_slate = slate_teams(dk)
    if on_slate:
        lu_team = lu["team code"].astype(str).str.strip().str.upper().replace(ABBR_REMAP)
        lu = lu[lu_team.isin(on_slate)]
        if lu.empty:
            print("PREFLIGHT: no lineup rows match the DK slate teams — "
                  "the two files are probably from different days.")
            return 2

    bo = lu["batting order"].astype(str).str.strip().str.upper()
    cf = lu["confirmed"].astype(str).str.strip().str.upper()
    is_sp, is_bat = bo == "SP", bo.isin([str(i) for i in range(1, 10)])

    n_sp, n_sp_conf = int(is_sp.sum()), int((is_sp & (cf == "Y")).sum())
    n_bat_conf = int((is_bat & (cf == "Y")).sum())
    teams = lu.loc[cf == "Y", "team code"].nunique()
    age_min = (datetime.now().timestamp() - os.path.getmtime(lineups_path)) / 60

    print("-" * 60)
    print(f"PREFLIGHT  {os.path.basename(lineups_path)}  "
          f"(downloaded {age_min:.0f} min ago)")
    if on_slate:
        print(f"  slate: {len(on_slate)} teams / {len(on_slate) // 2} games")
    print(f"  confirmed SPs:     {n_sp_conf} of {n_sp}")
    print(f"  confirmed batters: {n_bat_conf}")
    print(f"  teams with anything confirmed: {teams}")

    fp = first_pitch(dk)
    if fp is not None:
        now_et = datetime.now(tz=_ET)
        mins = (fp - now_et).total_seconds() / 60
        when = fp.strftime("%I:%M %p ET").lstrip("0")
        if mins > 0:
            print(f"  first pitch: {when}  ({mins/60:.1f}h away)")
        else:
            print(f"  first pitch: {when}  (*** ALREADY STARTED ***)")

    if n_sp and not n_sp_conf and slate_io.allow_unconfirmed():
        print()
        print("  NOTE: no SP is confirmed yet, but DK_ALLOW_UNCONFIRMED is set,")
        print("        so the build will use PROJECTED batting orders. Those are")
        print("        guesses; a scratched player scores zero. Rebuild once")
        print("        lineups post if there is still time.")
        print("-" * 60)
        return 0

    if n_sp and not n_sp_conf:
        print()
        print("  *** STOPPING: no SP is confirmed=Y yet. The lineups file was")
        print("      pulled before lineups posted, so the Vegas SP table and")
        print("      the portfolio build cannot complete.")
        print("      Re-download the Lineups CSV closer to lock and rerun.")
        print("      Nothing was spent -- no odds API call was made.")
        print("      To build anyway on projected orders, set")
        print("      DK_ALLOW_UNCONFIRMED=1 and rerun.")
        print("-" * 60)
        return 1

    if n_sp_conf < n_sp:
        print()
        print(f"  NOTE: only {n_sp_conf} of {n_sp} SPs confirmed -- the later")
        print("        games have not posted. The build will use what is")
        print("        confirmed; rerun after the rest post for a full slate.")

    # A missing SP costs one arm. A missing TEAM costs an entire offence, and
    # that is the one this gate exists for -- see the module docstring.
    missing = check_team_coverage(lu, cf, on_slate)
    if missing:
        tg = team_games(dk)
        now_et = datetime.now(tz=_ET)
        print()
        print("  " + "*" * 58)
        print(f"  *** {len(missing)} OF {len(on_slate)} SLATE TEAMS HAVE NO "
              f"CONFIRMED PLAYER:")
        print(f"  ***   {', '.join(missing)}")
        print("  ***")
        print("  *** Every one of their bats is excluded from the hitter pool.")
        print("  *** They cannot appear in a single lineup -- this is not a")
        print("  *** fade, it is a file that was pulled too early.")
        for t in missing:
            gi, dt = tg.get(t, ("", None))
            if dt is None:
                print(f"  ***   {t}: {gi}")
                continue
            hrs = (dt - now_et).total_seconds() / 3600
            when = dt.strftime("%I:%M %p ET").lstrip("0")
            if hrs > 0:
                print(f"  ***   {t}: starts {when} -- {hrs:.1f}h away, "
                      f"waiting is free")
            else:
                print(f"  ***   {t}: started {when} -- ALREADY LOCKED")
        print("  " + "*" * 58)
        if slate_io.allow_unconfirmed():
            print()
            print("  DK_ALLOW_UNCONFIRMED is set, so the build will use")
            print("  PROJECTED batting orders for those teams. Those are")
            print("  guesses; rebuild or late-swap once the lineups post.")
        else:
            print()
            print("  *** STOPPING. Choose one:")
            print("      - wait for those lineups to post, re-download, rerun")
            print("      - set DK_ALLOW_UNCONFIRMED=1 to build on projected")
            print("        orders now and late-swap after they post")
            print("-" * 60)
            return 1

    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
