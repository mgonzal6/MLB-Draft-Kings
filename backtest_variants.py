r"""Replay build variants over past slates and score them on what actually happened.

Every snapshot holds a slate's frozen inputs; every contest-standings export
holds each player's realised FPTS. Together that is enough to rebuild a
portfolio under any variant and score it exactly as DK would have -- and,
because the standings also give the full field's score distribution, to
estimate what each simulated lineup would have ranked and paid.

    python backtest_variants.py
    python backtest_variants.py --variants control,boom --lineups 20
    python backtest_variants.py --seeds 10            # 10 portfolios per arm

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

WHY --seeds: every arm's portfolio is RNG-dependent -- control takes the first
valid construction the RNG produces, and the sampling arms draw from
stable_rng(seed, ...). One seed therefore compares a single control draw
against a single variant draw, in an argument that is entirely about variance.
Running S seeds gives S portfolios per arm per slate, and because every arm
shares the same (slate, seed) inputs, the arms can be compared PAIRED: the
delta table at the end differences each variant against control on the same
slate and seed, which removes both slate difficulty and seed luck. Read that
table, not the raw means -- a gap smaller than about 2 SE is not separable
from noise no matter how it looks in the pooled row.

Cost: one build subprocess per slate x variant x seed. --seeds 10 over 4 arms
and 3 slates is 120 builds.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build_portfolio.py")
SNAPSHOT_DIR = r"G:\My Drive\DK\Snapshots"
POST_DIR = r"G:\My Drive\DK\Post Contest"
NEEDED = ["Filtered_DKSalaries.csv", "Filtered_Lineups.csv", "vegas.csv",
          "pitcher_bs_cache_adj.csv", "hitter_bs_cache.csv"]
NAME_RE = re.compile(r"^(.*?)\s*\(\d+\)\s*$")
TICK_CLEAR = " " * 44   # blanks the in-place progress line on stderr

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


def build_once(snap, variant, seed, lineups, scratch):
    """Rebuild one portfolio from a snapshot. -> (upload DataFrame, error str).

    The scratch dir is wiped per build on purpose: build_portfolio names its
    upload by slate date and variant, not by seed, so a stale file left behind
    would be picked up as this seed's portfolio.
    """
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch)
    for f in NEEDED:
        shutil.copy(os.path.join(snap, f), os.path.join(scratch, f))
    cmd = [PY, BUILD, "--export", scratch, "--no-snapshot",
           "--lineups", str(lineups), "--seed", str(seed)]
    if variant != "control":
        cmd += ["--variant", variant]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONUTF8="1"), cwd=HERE)
    ups = glob.glob(os.path.join(scratch, "DK_upload_*.csv"))
    ups = [u for u in ups if "_cash_" not in os.path.basename(u)]
    if not ups:
        return None, (r.stdout or r.stderr or "").strip()[-120:]
    return pd.read_csv(ups[0]), None


def score_portfolio(d, fpts, scores, table):
    """Score one rebuilt portfolio against what actually happened."""
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
    return {"n": len(s), "mean": s.mean(), "sd": s.std(), "best": s.max(),
            "worst": s.min(), "best_rank": min(ranks), "paid": paid,
            "cost": len(pts) * table["fee"] if table else None,
            "missing": missing}


def se(x):
    """Standard error of the mean, NaN when there is only one observation."""
    x = pd.Series(x).dropna()
    return x.std(ddof=1) / (len(x) ** 0.5) if len(x) > 1 else float("nan")


def tick(msg=""):
    """In-place progress on stderr. Skipped when stderr is redirected, where
    a carriage return just litters the log instead of overwriting."""
    if sys.stderr.isatty():
        print((msg or TICK_CLEAR).ljust(len(TICK_CLEAR)), end="\r",
              file=sys.stderr, flush=True)


def fmt(x, spec=".1f"):
    return "n/a" if x is None or pd.isna(x) else format(x, spec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="control,ceiling,topk,boom")
    ap.add_argument("--lineups", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42,
                    help="base seed (default 42)")
    ap.add_argument("--seeds", type=int, default=1,
                    help="how many seeds to run per arm, counting up from "
                         "--seed. 1 (the default) reproduces the old "
                         "single-draw run; >1 enables the paired delta table")
    args = ap.parse_args()
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    if args.seeds < 1:
        raise SystemExit("--seeds must be at least 1")
    seeds = [args.seed + i for i in range(args.seeds)]

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

    print(f"{len(pairs)} slate(s) x {len(variants)} arm(s) x {len(seeds)} "
          f"seed(s) = {len(pairs) * len(variants) * len(seeds)} builds")

    # absolute on purpose: the build subprocess runs with cwd=HERE, so a
    # relative --export would resolve against the repo, not against the dir
    # the inputs were just copied into
    scratch = os.path.abspath(os.path.join(tempfile.gettempdir(), "bt_variants"))
    results = []
    for snap, cpath, cov in pairs:
        fpts, scores, n_field = contests[cpath]
        table = PAYOUT_TABLES.get(n_field)
        slate = os.path.basename(snap)
        cid = re.search(r"(\d+)", os.path.basename(cpath)).group(1)
        print(f"\n=== {slate}  vs contest {cid}  "
              f"({n_field} entries, {cov:.0%} player match) ===")
        # Averaged over seeds: mean/sd/best/worst/paid are per-portfolio
        # figures averaged across the seeds, so they stay on the same scale as
        # a single-seed run. bestRk is the best rank ANY seed reached.
        print(f"{'variant':<12}{'sds':>4}{'mean':>8}{'+-':>6}{'sd':>7}"
              f"{'best':>8}{'worst':>7}{'bestRk':>8}{'paid':>11}")
        for v in variants:
            per_seed = []
            for sd_ in seeds:
                tick(f"  building {v} seed {sd_} ...")
                d, err = build_once(snap, v, sd_, args.lineups, scratch)
                if d is None:
                    tick()
                    print(f"{v:<12}  seed {sd_} build failed: {err}")
                    continue
                m = score_portfolio(d, fpts, scores, table)
                m.update(slate=slate, contest=cid, variant=v, seed=sd_)
                results.append(m)
                per_seed.append(m)
            tick()
            if not per_seed:
                continue
            g = pd.DataFrame(per_seed)
            paid_s = ("n/a" if table is None
                      else f"${g['paid'].mean():.2f}/{g['cost'].mean():.0f}")
            print(f"{v:<12}{len(g):>4}{g['mean'].mean():>8.1f}"
                  f"{fmt(se(g['mean'])):>6}{g['sd'].mean():>7.1f}"
                  f"{g['best'].mean():>8.1f}{g['worst'].mean():>7.1f}"
                  f"{g['best_rank'].min():>8}{paid_s:>11}")
    shutil.rmtree(scratch, ignore_errors=True)

    if not results:
        return
    df = pd.DataFrame(results)

    _pooled(df)
    _paired(df, variants)

    out = os.path.join(POST_DIR, "backtest_variants.csv")
    df.to_csv(out, index=False)
    print(f"\nwrote {out}")


def _pooled(df):
    """Averages across every slate and seed. Descriptive only -- see _paired."""
    print("\n" + "=" * 70)
    print("POOLED ACROSS SLATES")
    print("=" * 70)
    print(f"{'variant':<12}{'slates':>7}{'sds':>5}{'mean':>8}{'sd':>7}"
          f"{'bestAvg':>9}{'paid':>9}{'cost':>8}")
    for v, g in df.groupby("variant", sort=False):
        # paid/cost average over seeds within a slate, then sum over slates:
        # summing raw would multiply an entry fee that was only paid once.
        per_slate = g.groupby("slate")[["paid", "cost"]].mean()
        # all-NaN means no contest in the run had a known payout table; that
        # is "unknown", not "$0.00 returned"
        money = (("n/a", "n/a") if per_slate["paid"].isna().all()
                 else (f"{per_slate['paid'].sum():.2f}",
                       f"{per_slate['cost'].sum():.2f}"))
        print(f"{v:<12}{g['slate'].nunique():>7}{g['seed'].nunique():>5}"
              f"{g['mean'].mean():>8.1f}{g['sd'].mean():>7.1f}"
              f"{g['best'].mean():>9.1f}{money[0]:>9}{money[1]:>8}")


def _paired(df, variants):
    """Difference each arm against control on the SAME slate and seed.

    Unpaired means are dominated by slate difficulty -- a hard slate moves
    every arm together. Pairing on (slate, seed) removes that and the seed
    draw, leaving only what the arm itself did, so the SE is the honest one.
    """
    if "control" not in set(df["variant"]):
        print("\n(no control arm in this run -- skipping paired deltas)")
        return
    others = [v for v in variants if v != "control" and v in set(df["variant"])]
    if not others:
        return
    print("\n" + "=" * 70)
    print("PAIRED VS CONTROL  (same slate, same seed)")
    print("=" * 70)
    print(f"{'variant':<12}{'pairs':>6}{'dMean':>8}{'+-':>7}{'t':>7}"
          f"{'dBest':>8}{'dPaid':>8}  verdict")
    piv = {k: df.pivot_table(index=["slate", "seed"], columns="variant",
                             values=k)
           for k in ("mean", "best", "paid")}

    def delta(k, v):
        """Per-pair (variant - control) for one metric, empty when either arm
        has no numbers -- paid is NaN on any contest with no payout table."""
        p = piv[k]
        if v not in p.columns or "control" not in p.columns:
            return pd.Series(dtype=float)
        return (p[v] - p["control"]).dropna()

    for v in others:
        d = delta("mean", v)
        if d.empty:
            continue
        s = se(d)
        t = d.mean() / s if not pd.isna(s) and s > 0 else float("nan")
        db = delta("best", v).mean()
        dp = delta("paid", v).mean()
        if pd.isna(t):
            verdict = ("need >1 pair" if len(d) < 2
                       else "no spread across pairs")
        elif abs(t) < 2:
            verdict = "not separable from noise"
        else:
            verdict = "better than control" if t > 0 else "worse than control"
        print(f"{v:<12}{len(d):>6}{d.mean():>+8.1f}{fmt(s):>7}{fmt(t, '.2f'):>7}"
              f"{db:>+8.1f}{fmt(dp, '+.2f'):>8}  {verdict}")
    print("\nt is dMean/SE. |t| < 2 means this run cannot tell the arm apart\n"
          "from control -- which is a result, not a failure to measure.")


if __name__ == "__main__":
    main()
