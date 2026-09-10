"""Which contests have SOFT 10th-place bars?

The record says contest selection decides top-10 finishes more often than
construction does, and it has never been measured:

  08/30  rank 11 by 0.50; the same lineup was top-10 in either of the other
         two contests that day (bars 160.30 and 159.95 vs 160.95)
  09/04  the only top-10 needed the softest bar on the card; 148.45 would
         have missed in all six other contests
  09/07  the same 143.85 lineup finished 132nd in a 4,756 field and 6th in
         a 237 field
  09/09  our 132.55 was rank 11 against a 133.65 bar, while a 237-entry
         contest on the same slate had a bar of 110.50

Every arm ever tested moves `best` by a few points. These gaps are 20-50.

Contests are grouped into slates by player-pool overlap (Jaccard on the
standings player block), so the comparison is WITHIN a slate -- same players,
same night, different fields. Across slates the bar mostly measures slate
difficulty, which is not actionable at entry time.
"""
import collections
import csv
import glob
import os
import re
import statistics as st
import sys

D = r"G:\My Drive\DK\Post Contest"


def read_contest(path):
    entries, players = [], set()
    for r in csv.DictReader(open(path, encoding="utf-8-sig", errors="replace")):
        p = (r.get("Player") or "").strip()
        if p:
            players.add(p)
        pts = r.get("Points")
        if r.get("Rank") and pts not in (None, ""):
            try:
                entries.append(float(pts))
            except ValueError:
                pass
    entries.sort(reverse=True)
    if len(entries) < 20 or len(players) < 30:
        return None
    return {
        "n": len(entries),
        "bar10": entries[9],
        "win": entries[0],
        "median": entries[len(entries) // 2],
        "players": players,
    }


def main():
    rows = {}
    for f in glob.glob(os.path.join(D, "contest-standings-*.csv")):
        m = re.search(r"contest-standings-(\d+)", os.path.basename(f))
        if not m:
            continue
        c = read_contest(f)
        if c:
            rows[m.group(1)] = c
    print(f"{len(rows)} contests with >=20 entries and a readable player block")

    # group into slates by player-pool overlap
    slates, seen = [], set()
    for cid, c in rows.items():
        if cid in seen:
            continue
        grp = [cid]
        seen.add(cid)
        for oid, o in rows.items():
            if oid in seen:
                continue
            j = len(c["players"] & o["players"]) / max(len(c["players"] | o["players"]), 1)
            if j >= 0.60:
                grp.append(oid)
                seen.add(oid)
        slates.append(grp)
    multi = [g for g in slates if len(g) >= 2]
    print(f"{len(slates)} slates, {len(multi)} with 2+ contests "
          f"(the ones that can be compared within-slate)")

    print()
    print("WITHIN-SLATE: smallest vs largest field on the same night")
    print(f"{'slate':<7}{'contests':>9}{'smallest n':>12}{'its bar':>9}"
          f"{'largest n':>11}{'its bar':>9}{'bar gap':>9}")
    gaps, ratios = [], []
    for g in sorted(multi, key=lambda g: -len(g)):
        cs = sorted(((rows[c]["n"], rows[c]["bar10"], c) for c in g))
        sn, sb, _ = cs[0]
        ln, lb, _ = cs[-1]
        if sn == ln:
            continue
        gaps.append(lb - sb)
        ratios.append(ln / sn)
        print(f"{g[0][-5:]:<7}{len(g):>9}{sn:>12}{sb:>9.2f}{ln:>11}{lb:>9.2f}"
              f"{lb - sb:>+9.2f}")
    if gaps:
        print()
        print(f"  mean bar gap (large field - small field): {st.mean(gaps):+.2f}")
        print(f"  bigger field had the HIGHER bar in {sum(1 for x in gaps if x > 0)}"
              f" of {len(gaps)} slates")
        print(f"  median field-size ratio: {st.median(ratios):.1f}x")

    print()
    print("ALL CONTESTS by field size (across slates -- directional only)")
    buckets = [(0, 300, "<300"), (300, 1000, "300-1k"), (1000, 2500, "1k-2.5k"),
               (2500, 100000, "2.5k+")]
    print(f"{'field':<10}{'n':>5}{'mean bar':>10}{'median bar':>12}{'mean win':>10}")
    for lo, hi, lbl in buckets:
        b = [c["bar10"] for c in rows.values() if lo <= c["n"] < hi]
        w = [c["win"] for c in rows.values() if lo <= c["n"] < hi]
        if b:
            print(f"{lbl:<10}{len(b):>5}{st.mean(b):>10.2f}{st.median(b):>12.2f}"
                  f"{st.mean(w):>10.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
