r"""
post_contest.py — DK MLB Classic post-contest analysis.

Reads a DraftKings contest-standings export (the CSV downloaded from the
contest results page) and produces a report on how the portfolio did:

    input (default: newest contest-standings-*.csv in G:\My Drive\DK\Post Contest):
        contest-standings-<id>.csv   two tables side by side in one file:
                                     standings (Rank..Lineup) + player
                                     ownership (Player, %Drafted, FPTS)

    optional join (if the slate's file is still in G:\My Drive\DK\export):
        Filtered_DKSalaries.csv      salaries -> value (FPTS per $1k)

    outputs (written next to the input CSV):
        post_exposure_<id>.csv       my exposure vs field ownership, FPTS, leverage
        post_entries_<id>.csv        one row per my entry: rank, pctile, points

Usage:
    python post_contest.py                     # newest CSV, user fancyplayer
    python post_contest.py --csv <path>
    python post_contest.py --user fancyplayer --top-pct 1.0
"""
import argparse
import glob
import os
import re
import sys
from collections import Counter

import pandas as pd

from lineup_id import lineup_id

POST_DIR = r"G:\My Drive\DK\Post Contest"
EXPORT_DIR = r"G:\My Drive\DK\export"
SNAPSHOT_DIR = r"G:\My Drive\DK\Snapshots"
POS_TOKENS = {"P", "C", "1B", "2B", "3B", "SS", "OF"}

# Payout curve for the $0.10 / 3,567-entry GPP. ROI is reported ONLY when the
# contest's field matches PAYOUT_FIELD — a different contest has a different
# curve, and printing dollars from the wrong table is worse than printing none.
ENTRY_FEE = 0.10
PAYOUT_FIELD = 3567
PAYOUTS = [(1, 1, 30.00), (2, 2, 15.00), (3, 3, 10.00), (4, 4, 5.00),
           (5, 5, 4.00), (6, 7, 3.00), (8, 10, 2.00), (11, 15, 1.50),
           (16, 20, 1.00), (21, 30, 0.75), (31, 60, 0.50), (61, 130, 0.40),
           (131, 340, 0.30), (341, 830, 0.20)]


def pos_payout(p):
    for lo, hi, amt in PAYOUTS:
        if lo <= p <= hi:
            return amt
    return 0.0


def entry_payout(rank, tie_size):
    """DK splits the payouts of the occupied positions across tied entries."""
    return sum(pos_payout(p) for p in range(rank, rank + tie_size)) / tie_size


# ---------------------------------------------------------------- parsing

def newest_standings_csv():
    files = [f for ext in ("csv", "xlsx")
             for f in glob.glob(os.path.join(POST_DIR,
                                             f"contest-standings-*.{ext}"))]
    if not files:
        sys.exit(f"no contest-standings-* file found in {POST_DIR}")
    return max(files, key=os.path.getmtime)


def parse_lineup(lineup_str):
    """'1B Nathaniel Lowe 2B Travis Bazzana ... P MacKenzie Gore' ->
    [('1B', 'Nathaniel Lowe'), ...]. Position tokens delimit names."""
    players, pos, name = [], None, []
    for tok in str(lineup_str).split():
        if tok in POS_TOKENS:
            if pos and name:
                players.append((pos, " ".join(name)))
            pos, name = tok, []
        else:
            name.append(tok)
    if pos and name:
        players.append((pos, " ".join(name)))
    return players


def read_export(path):
    """DK sometimes serves the 'CSV' as a real .xlsx. Sniff the magic bytes
    rather than trusting the extension, and strip any UTF-8 BOM off the
    header names."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    df = pd.read_excel(path) if magic == b"PK" else pd.read_csv(path)
    df.columns = [str(c).replace("﻿", "").replace("ï»¿", "").strip()
                  for c in df.columns]
    return df


def load_standings(path):
    df = read_export(path)
    standings = df[["Rank", "EntryId", "EntryName", "Points", "Lineup"]].dropna(
        subset=["EntryName"]).copy()
    standings["Rank"] = standings["Rank"].astype(int)

    own = df[["Player", "Roster Position", "%Drafted", "FPTS"]].dropna(
        subset=["Player"]).copy()
    # CSV exports give "36.81%"; the xlsx variant gives the fraction 0.3681.
    pct = pd.to_numeric(own["%Drafted"].astype(str).str.rstrip("%"),
                        errors="coerce")
    own["%Drafted"] = pct * 100 if pct.max() <= 1.0 else pct
    own["FPTS"] = pd.to_numeric(own["FPTS"], errors="coerce").fillna(0.0)
    # DK lists a player once per roster position; same-name players (two Max
    # Muncys) also collide -- keep the higher-owned row so lookups are scalar.
    own = own.sort_values("%Drafted", ascending=False).drop_duplicates("Player")
    return standings, own


def entry_user(entry_name):
    return re.sub(r"\s*\(\d+/\d+\)\s*$", "", str(entry_name)).strip().lower()


# ---------------------------------------------------------------- analysis

def exposure_counts(lineups):
    """list of [(pos, name), ...] -> Counter of player name appearances."""
    c = Counter()
    for lu in lineups:
        for _, name in lu:
            c[name] += 1
    return c


def load_salaries(slate_dir):
    path = os.path.join(slate_dir, "Filtered_DKSalaries.csv")
    if not os.path.exists(path):
        print(f"  NOTE: no Filtered_DKSalaries.csv in {slate_dir} — "
              f"salary and value columns will be blank.\n")
        return None
    sal = pd.read_csv(path)[["Name", "Salary", "TeamAbbrev"]].drop_duplicates("Name")
    return sal.set_index("Name")


def _coverage(slate_dir, players):
    """Share of the contest's players present in that slate's salary file."""
    path = os.path.join(slate_dir, "Filtered_DKSalaries.csv")
    if not os.path.exists(path) or not players:
        return 0.0
    try:
        names = set(pd.read_csv(path)["Name"].astype(str))
    except Exception:
        return 0.0
    return sum(1 for p in players if p in names) / len(players)


def resolve_slate_dir(explicit, players):
    r"""Pick the salary file that actually matches this contest.

    EXPORT_DIR holds whatever slate was built LAST, so analyzing an earlier
    contest against it silently mismatches every player not on the current
    slate (running the early card against the late card's export left every
    TEX and CWS player unmatched, blanking their value column). Snapshots are
    frozen per slate, so score every candidate by how much of the contest's
    player pool it contains and take the best.
    """
    if explicit:
        return explicit, _coverage(explicit, players), 1.0
    cands = [EXPORT_DIR] + sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*")))
    scored = sorted(((_coverage(d, players), d) for d in cands if os.path.isdir(d)),
                    reverse=True)
    if not scored:
        return EXPORT_DIR, 0.0, 0.0
    best_cov, best = scored[0]
    # Coverage never approaches 100%: Filtered_DKSalaries.csv holds only the
    # confirmed starters (139 rows on this slate) while the contest lists every
    # player anyone rostered (174). So judge by SEPARATION from the runner-up,
    # not by an absolute bar -- the right slate stands clear of the wrong ones.
    margin = best_cov - scored[1][0] if len(scored) > 1 else 1.0
    return best, best_cov, margin


def fmt_pct(x):
    return f"{x:5.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="standings CSV (default: newest)")
    ap.add_argument("--user", default="fancyplayer")
    ap.add_argument("--top-pct", type=float, default=1.0,
                    help="field slice (in %%) used for 'what the winners played'")
    ap.add_argument("--slate-dir", default=None,
                    help="folder holding this slate's Filtered_DKSalaries.csv "
                         "(default: auto-pick the snapshot matching the "
                         "contest's players, else the live export folder)")
    args = ap.parse_args()

    path = args.csv or newest_standings_csv()
    contest_id = re.search(r"(\d+)", os.path.basename(path))
    contest_id = contest_id.group(1) if contest_id else "unknown"
    print(f"analyzing {os.path.basename(path)}  (contest {contest_id})\n")

    standings, own = load_standings(path)
    fpts = own.set_index("Player")["FPTS"]
    field_own = own.set_index("Player")["%Drafted"]
    slate_dir, cov, margin = resolve_slate_dir(args.slate_dir,
                                               list(own["Player"].astype(str)))
    print(f"slate file: {os.path.basename(slate_dir)}  "
          f"({cov:.0%} of contest players matched)")
    # A tie between the live export and its own snapshot is the NORMAL case
    # for the slate just built -- both hold the same file, so a small margin
    # there means agreement, not ambiguity. Only treat a tie as ambiguous when
    # coverage is also mediocre.
    if cov < 0.60 or (margin < 0.05 and cov < 0.85):
        print("  WARNING: no slate folder clearly matches this contest — "
              "salary and value columns may be unreliable. "
              "Pass --slate-dir explicitly.")
    print()
    salaries = load_salaries(slate_dir)

    n_field = len(standings)
    mine = standings[standings["EntryName"].map(entry_user) == args.user.lower()].copy()
    if mine.empty:
        sys.exit(f"no entries found for user '{args.user}'")
    mine["players"] = mine["Lineup"].map(parse_lineup)

    # ---- contest overview
    print("=" * 66)
    print(f"CONTEST OVERVIEW  ({n_field} entries)")
    print("=" * 66)
    print(f"  winning score : {standings['Points'].max():.2f}")
    for pct in (1, 5, 10, 20, 50):
        idx = max(0, int(n_field * pct / 100) - 1)
        line = standings.sort_values("Rank").iloc[idx]
        print(f"  top {pct:>2}% line  : {line['Points']:.2f}  (rank {int(line['Rank'])})")

    # ---- my entries
    print()
    print("=" * 66)
    print(f"MY ENTRIES  ({len(mine)} of {n_field})")
    print("=" * 66)
    mine = mine.sort_values("Rank")
    lineup_keys = mine["players"].map(lambda lu: tuple(sorted(n for _, n in lu)))
    dup_groups = lineup_keys.map(Counter(lineup_keys))
    mine["pctile"] = mine["Rank"] / n_field * 100
    tie_sizes = standings.groupby("Rank")["EntryId"].count()
    roi_ok = n_field == PAYOUT_FIELD
    rows, total_won = [], 0.0
    for (_, r), dups in zip(mine.iterrows(), dup_groups):
        names = [n for _, n in r["players"]]
        sal = int(salaries["Salary"].reindex(names).sum()) if salaries is not None else None
        won = entry_payout(int(r["Rank"]), int(tie_sizes[r["Rank"]])) if roi_ok else 0.0
        total_won += won
        flag = f"  DUPE x{dups}" if dups > 1 else ""
        sal_s = f"  ${sal}" if sal and sal > 0 else ""
        won_s = f"  won ${won:.2f}" if won else ""
        print(f"  rank {int(r['Rank']):>5} (top {r['pctile']:4.1f}%)  "
              f"{r['Points']:6.2f} pts{sal_s}{won_s}{flag}")
        # lineup_id joins these results back to portfolio_summary_<date>.csv,
        # so "did my highest-ranked lineups score best?" becomes answerable.
        rows.append({"Rank": int(r["Rank"]), "Pctile": round(r["pctile"], 1),
                     "lineup_id": lineup_id(names),
                     "Points": r["Points"], "Salary": sal, "Won": round(won, 2),
                     "DupCount": dups, "Lineup": r["Lineup"]})
    n_dupes = int((dup_groups > 1).sum())
    if n_dupes:
        print(f"\n  WARNING: {n_dupes} entries are duplicates of another of my own "
              f"lineups -- wasted entry diversity.")

    if roi_ok:
        cost = len(mine) * ENTRY_FEE
        net = total_won - cost
        print(f"\n  ROI: won ${total_won:.2f} on ${cost:.2f} in entries -> "
              f"net ${net:+.2f} ({net / cost * 100:+.0f}%)")
    else:
        print(f"\n  (ROI skipped: payout table is for a {PAYOUT_FIELD}-entry "
              f"contest, this one has {n_field} — edit PAYOUTS to enable)")

    # ---- my exposure vs field
    my_counts = exposure_counts(mine["players"])
    n_mine = len(mine)
    exp = pd.DataFrame(
        [{"Player": p, "MyExp": c / n_mine * 100,
          "FieldOwn": field_own.get(p, float("nan")),
          "FPTS": fpts.get(p, float("nan"))}
         for p, c in my_counts.items()])
    exp["Leverage"] = exp["MyExp"] - exp["FieldOwn"]
    if salaries is not None:
        exp["Salary"] = exp["Player"].map(salaries["Salary"])
        exp["Val/$1k"] = (exp["FPTS"] / (exp["Salary"] / 1000)).round(2)
    exp = exp.sort_values("MyExp", ascending=False)

    print()
    print("=" * 66)
    print("MY EXPOSURE vs FIELD  (sorted by my exposure)")
    print("=" * 66)
    print(f"  {'player':<24}{'me':>7}{'field':>8}{'lev':>8}{'FPTS':>7}")
    for _, r in exp.head(25).iterrows():
        f_own = fmt_pct(r["FieldOwn"]) if pd.notna(r["FieldOwn"]) else "    - "
        lev = f"{r['Leverage']:+6.1f}" if pd.notna(r["Leverage"]) else "     -"
        print(f"  {r['Player']:<24}{fmt_pct(r['MyExp']):>7}{f_own:>8}{lev:>8}"
              f"{r['FPTS']:>7.1f}")

    # ---- biggest misses: top slate scorers I under-rostered
    print()
    print("=" * 66)
    print("SLATE'S TOP SCORERS -- did I have them?")
    print("=" * 66)
    top_scorers = own.sort_values("FPTS", ascending=False).head(15)
    print(f"  {'player':<24}{'FPTS':>6}{'field':>8}{'me':>7}")
    for _, r in top_scorers.iterrows():
        me = my_counts.get(r["Player"], 0) / n_mine * 100
        print(f"  {r['Player']:<24}{r['FPTS']:>6.1f}{fmt_pct(r['%Drafted']):>8}"
              f"{fmt_pct(me):>7}")

    # ---- busts I was heavy on
    busts = exp[(exp["MyExp"] >= 20) & (exp["FPTS"] <= 5)]
    if not busts.empty:
        print()
        print("=" * 66)
        print("BUSTS I WAS HEAVY ON  (>=20% exposure, <=5 FPTS)")
        print("=" * 66)
        for _, r in busts.sort_values("MyExp", ascending=False).iterrows():
            print(f"  {r['Player']:<24}{fmt_pct(r['MyExp'])} of my lineups, "
                  f"{r['FPTS']:.1f} FPTS")

    # ---- what the top of the field played
    n_top = max(1, int(n_field * args.top_pct / 100))
    top_field = standings.sort_values("Rank").head(n_top)
    top_counts = exposure_counts(top_field["Lineup"].map(parse_lineup))
    print()
    print("=" * 66)
    print(f"WHAT THE TOP {args.top_pct}% PLAYED  ({n_top} lineups)")
    print("=" * 66)
    print(f"  {'player':<24}{'top%':>7}{'field':>8}{'me':>7}{'FPTS':>7}")
    for p, c in top_counts.most_common(15):
        me = my_counts.get(p, 0) / n_mine * 100
        f_own = field_own.get(p, float("nan"))
        f_s = fmt_pct(f_own) if pd.notna(f_own) else "    - "
        print(f"  {p:<24}{fmt_pct(c / n_top * 100):>7}{f_s:>8}{fmt_pct(me):>7}"
              f"{fpts.get(p, 0.0):>7.1f}")

    # ---- best lineup breakdown
    best = mine.iloc[0]
    print()
    print("=" * 66)
    print(f"MY BEST LINEUP  (rank {int(best['Rank'])}, {best['Points']:.2f} pts)")
    print("=" * 66)
    for pos, name in best["players"]:
        f_own = field_own.get(name, float("nan"))
        f_s = fmt_pct(f_own) if pd.notna(f_own) else "    - "
        print(f"  {pos:<4}{name:<24}{fpts.get(name, 0.0):>6.1f} FPTS  "
              f"own {f_s}")

    # ---- write outputs
    out_exp = os.path.join(os.path.dirname(path), f"post_exposure_{contest_id}.csv")
    out_ent = os.path.join(os.path.dirname(path), f"post_entries_{contest_id}.csv")
    exp.round(2).to_csv(out_exp, index=False)
    pd.DataFrame(rows).to_csv(out_ent, index=False)
    print()
    print(f"wrote {out_exp}")
    print(f"wrote {out_ent}")


if __name__ == "__main__":
    main()
