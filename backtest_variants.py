r"""Replay build variants over past slates and score them on what actually happened.

Every snapshot holds a slate's frozen inputs; every contest-standings export
holds each player's realised FPTS. Together that is enough to rebuild a
portfolio under any variant and score it exactly as DK would have -- and,
because the standings also give the full field's score distribution, to
estimate what each simulated lineup would have ranked and paid.

    python backtest_variants.py
    python backtest_variants.py --variants control,boom --lineups 20

Read the numbers with care:

  * This simulates ENTRY, not the contest. Ranks assume the rest of the field
    is unchanged, which is fair for 20 lineups in a 1,000+ entry contest but
    is still an assumption.
  * Variants designed after looking at a slate are contaminated on that slate.
    boom was built from 08/25, so treat 08/25 as in-sample and the earlier
    slates as the real test.
  * A handful of slates cannot separate a small edge from luck. The spread
    (sd) matters as much as the mean here: the whole reason boom exists is
    that argmax selection halved it.
"""
import argparse
import glob
import itertools
import os
import re
import shutil
import subprocess
import sys

import pandas as pd

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build_portfolio.py")
SNAPSHOT_DIR = r"G:\My Drive\DK\Snapshots"
POST_DIR = r"G:\My Drive\DK\Post Contest"
NEEDED = ["Filtered_DKSalaries.csv", "Filtered_Lineups.csv", "vegas.csv",
          "pitcher_bs_cache_adj.csv", "hitter_bs_cache.csv"]
NAME_RE = re.compile(r"^(.*?)\s*\(\d+\)\s*$")

sys.path.insert(0, HERE)
from post_contest import read_export, PAYOUT_TABLES, entry_payout  # noqa: E402


def contest_data(path):
    """-> (player -> FPTS, sorted field scores, n_field)."""
    df = read_export(path)
    own = df[["Player", "FPTS"]].dropna(subset=["Player"]).copy()
    own["FPTS"] = pd.to_numeric(own["FPTS"], errors="coerce").fillna(0.0)
    own = own.drop_duplicates("Player")
    st = df[["Rank", "Points"]].dropna(subset=["Rank"]).copy()
    scores = sorted(pd.to_numeric(st["Points"], errors="coerce").dropna(),
                    reverse=True)
    return dict(zip(own["Player"], own["FPTS"])), scores, len(scores)


def rank_of(score, scores):
    """Where a simulated score would have placed in that field."""
    lo, hi = 0, len(scores)
    while lo < hi:
        mid = (lo + hi) // 2
        if scores[mid] > score:
            lo = mid + 1
        else:
            hi = mid
    return lo + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="control,ceiling,topk,boom")
    ap.add_argument("--lineups", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    # pair each snapshot with the contest whose players it best covers
    contests = {}
    for p in sorted(glob.glob(os.path.join(POST_DIR, "contest-standings-*.csv"))):
        try:
            contests[p] = contest_data(p)
        except Exception:
            continue

    pairs = []
    for snap in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*"))):
        if not all(os.path.exists(os.path.join(snap, f)) for f in NEEDED):
            continue
        names = set(pd.read_csv(os.path.join(snap, "Filtered_DKSalaries.csv"))["Name"])
        best, cov = None, 0.0
        for p, (fpts, _, _) in contests.items():
            # Jaccard, not plain coverage: a 7-game card is a superset of the
            # 6-game late card, so coverage alone lets the wrong contest win
            # every time. Overlap relative to the UNION picks the contest whose
            # player pool actually matches this slate.
            pool = set(fpts)
            j = len(names & pool) / max(len(names | pool), 1)
            if j > cov:
                best, cov = p, j
        if best and cov >= 0.50:
            pairs.append((snap, best, cov))

    if not pairs:
        raise SystemExit("no snapshot could be matched to a contest")

    scratch = os.path.join(os.environ.get("TEMP", "."), "bt_variants")
    results = []
    for snap, cpath, cov in pairs:
        fpts, scores, n_field = contests[cpath]
        table = PAYOUT_TABLES.get(n_field)
        slate = os.path.basename(snap)
        cid = re.search(r"(\d+)", os.path.basename(cpath)).group(1)
        print(f"\n=== {slate}  vs contest {cid}  "
              f"({n_field} entries, {cov:.0%} player match) ===")
        print(f"{'variant':<12}{'n':>3}{'mean':>8}{'sd':>7}{'best':>8}"
              f"{'worst':>7}{'bestRk':>8}{'paid':>9}")
        for v in variants:
            shutil.rmtree(scratch, ignore_errors=True)
            os.makedirs(scratch)
            for f in NEEDED:
                shutil.copy(os.path.join(snap, f), os.path.join(scratch, f))
            cmd = [PY, BUILD, "--export", scratch, "--no-snapshot",
                   "--lineups", str(args.lineups), "--seed", str(args.seed)]
            if v != "control":
                cmd += ["--variant", v]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               env=dict(os.environ, PYTHONUTF8="1"), cwd=HERE)
            ups = glob.glob(os.path.join(scratch, "DK_upload_*.csv"))
            ups = [u for u in ups if "_cash_" not in os.path.basename(u)]
            if not ups:
                print(f"{v:<12}  build failed: "
                      f"{(r.stdout or r.stderr or '')[-120:].strip()}")
                continue
            d = pd.read_csv(ups[0])
            pts, missing = [], 0
            for _, row in d.iterrows():
                tot = 0.0
                for cell in row:
                    m = NAME_RE.match(str(cell).strip())
                    nm = m.group(1) if m else str(cell).strip()
                    if nm in fpts:
                        tot += fpts[nm]
                    else:
                        missing += 1
                pts.append(tot)
            s = pd.Series(pts)
            ranks = [rank_of(x, scores) for x in pts]
            paid = (sum(entry_payout(rk, 1, table["payouts"]) for rk in ranks)
                    if table else None)
            cost = len(pts) * table["fee"] if table else None
            paid_s = (f"${paid:.2f}/{cost:.0f}" if table else "n/a")
            print(f"{v:<12}{len(s):>3}{s.mean():>8.1f}{s.std():>7.1f}"
                  f"{s.max():>8.1f}{s.min():>7.1f}{min(ranks):>8}{paid_s:>9}")
            results.append({"slate": slate, "variant": v, "n": len(s),
                            "mean": s.mean(), "sd": s.std(), "best": s.max(),
                            "worst": s.min(), "best_rank": min(ranks),
                            "paid": paid, "cost": cost, "missing": missing})
    shutil.rmtree(scratch, ignore_errors=True)

    if results:
        df = pd.DataFrame(results)
        print("\n" + "=" * 62)
        print("POOLED ACROSS SLATES")
        print("=" * 62)
        print(f"{'variant':<12}{'slates':>7}{'mean':>8}{'sd':>7}"
              f"{'bestAvg':>9}{'paid':>9}{'cost':>8}")
        for v, g in df.groupby("variant", sort=False):
            paid = g["paid"].sum(skipna=True)
            cost = g["cost"].sum(skipna=True)
            print(f"{v:<12}{len(g):>7}{g['mean'].mean():>8.1f}"
                  f"{g['sd'].mean():>7.1f}{g['best'].mean():>9.1f}"
                  f"{paid:>9.2f}{cost:>8.2f}")
        out = os.path.join(POST_DIR, "backtest_variants.csv")
        df.to_csv(out, index=False)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
