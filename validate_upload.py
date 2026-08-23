r"""Independent validator for a DK upload file.

Deliberately does NOT import build_portfolio — it re-derives every rule from
the raw DK slate files so a bug in the builder cannot hide itself here.

    python validate_upload.py <upload.csv> [slate_dir]

slate_dir defaults to the live export folder; pass another directory to check
an upload built from a different slate snapshot.
"""
import os
import re
import sys

import pandas as pd

EXPORT = r"G:\My Drive\DK\export"
SLOTS = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
CAP = 50000

if len(sys.argv) < 2:
    sys.exit("usage: validate_upload.py <upload.csv> [slate_dir]")
path = sys.argv[1]
slate = sys.argv[2] if len(sys.argv) > 2 else EXPORT
up = pd.read_csv(path)
dk = pd.read_csv(os.path.join(slate, "Filtered_DKSalaries.csv"))
lu = pd.read_csv(os.path.join(slate, "Filtered_Lineups.csv"))
lu.columns = lu.columns.str.strip()

print(f"file: {path}")
print(f"rows: {len(up)}   columns: {list(up.columns)}")
if [re.sub(r"\.\d+$", "", c) for c in up.columns] != SLOTS:
    print("  !! COLUMN ORDER does not match DK's P,P,C,1B,2B,3B,SS,OF,OF,OF")

# id -> DK row
dk["key"] = dk["Name + ID"].astype(str).str.strip()
by_key = dk.drop_duplicates("key").set_index("key")

opp, game = {}, {}
for gi in dk["Game Info"].dropna().unique():
    m = re.match(r"(\w+)@(\w+)", str(gi))
    if m:
        a, h = m.groups()
        opp[a], opp[h] = h, a
        game[a] = game[h] = f"{a}@{h}"

# confirmed starters straight from the lineups file
def norm(s):
    s = str(s).lower()
    for ch in ".,'-":
        s = s.replace(ch, "")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return " ".join(t for t in s.split() if len(t) > 1)

lu["n"] = lu["player name"].map(norm)
lu["bo"] = lu["batting order"].astype(str).str.strip().str.upper()
lu["cf"] = lu["confirmed"].astype(str).str.strip().str.upper()
conf_bat = set(lu[(lu["cf"] == "Y") & lu["bo"].str.fullmatch(r"[1-9]")]["n"])
conf_sp = set(lu[(lu["cf"] == "Y") & (lu["bo"] == "SP")]["n"])
bo_of = dict(zip(lu["n"], lu["bo"]))

all_ok = True
for i, row in up.iterrows():
    print(f"\n--- lineup {i + 1}")
    errs, warns = [], []
    players, salary = [], 0
    teams, sp_opps, games = {}, [], set()

    for col, key in zip(SLOTS, row):
        key = str(key).strip()
        if key not in by_key.index:
            errs.append(f"unknown DK id: {key}")
            continue
        r = by_key.loc[key]
        name, pos, team = r["Name"], str(r["Roster Position"]), r["TeamAbbrev"]
        players.append(name)
        salary += int(r["Salary"])
        games.add(game.get(team, "?"))
        n = norm(name)

        if col == "P":
            if pos != "P":
                errs.append(f"{name} ({pos}) is not a pitcher")
            if n not in conf_sp:
                errs.append(f"{name} is NOT a confirmed SP in the lineups file")
            sp_opps.append(opp.get(team))
        else:
            if col not in pos.split("/"):
                errs.append(f"{name} ({pos}) cannot fill {col}")
            if n not in conf_bat:
                errs.append(f"{name} is NOT in a confirmed 1-9 batting order")
            teams[team] = teams.get(team, 0) + 1

        status = str(r.get("Status", "")).strip()
        if status and status.lower() not in ("nan", ""):
            warns.append(f"{name} has DK status flag: {status}")
        print(f"  {col:<3} {name:<24} {team:<4} ${int(r['Salary']):>5}  {pos:<6}"
              f"  BO{bo_of.get(n, '?')}")

    if len(players) != 10:
        errs.append(f"{len(players)} players, expected 10")
    if len(set(players)) != len(players):
        errs.append("duplicate player within lineup")
    if salary > CAP:
        errs.append(f"salary ${salary} over cap")
    for t, c in teams.items():
        if c > 5:
            errs.append(f"{t} has {c} hitters (max 5)")
        if t in sp_opps:
            errs.append(f"{t} hitters oppose a rostered SP")
    if len(sp_opps) == 2 and game.get(sp_opps[0]) == game.get(sp_opps[1]):
        errs.append("both SPs are in the same game")
    if len(games) < 2:
        errs.append("lineup does not span >=2 games")

    print(f"  salary ${salary} / ${CAP}   teams: "
          + " ".join(f"{t}x{c}" for t, c in sorted(teams.items(), key=lambda x: -x[1]))
          + f"   games: {len(games)}")
    for w in warns:
        print(f"  WARN  {w}")
    print("  RESULT:", "VALID" if not errs else "INVALID -> " + "; ".join(errs))
    all_ok &= not errs

# cross-lineup
sigs = [tuple(sorted(str(v).strip() for v in row)) for _, row in up.iterrows()]
if len(set(sigs)) != len(sigs):
    print("\n!! duplicate lineups in file")
    all_ok = False
elif len(sigs) == 2:
    print(f"\noverlap between the two lineups: "
          f"{len(set(sigs[0]) & set(sigs[1]))}/10 players")

print("\nOVERALL:", "ALL LINEUPS VALID" if all_ok else "FIX REQUIRED")
