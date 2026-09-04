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
from post_contest import read_export  # noqa: E402


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


def build_once(snap, variant, seed, lineups, scratch, build_seeds=1):
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
    # A constrained arm underfills, and `best` over 45 lineups is not
    # comparable to `best` over 60 -- fewer draws is itself worth points. Pass
    # the builder's own multi-seed refill through so both arms are scored at
    # the same portfolio size.
    if build_seeds > 1:
        cmd += ["--seeds", str(build_seeds)]
    # ALWAYS pass --variant, control included. This used to omit it for
    # control, relying on the builder's default being the unconfigured arm.
    # When minspend49 shipped on 09/04 that default changed, so "control"
    # silently rebuilt minspend49 and a whole 340-build sweep compared the
    # arm against itself -- dBest exactly +0.0, "no spread across pairs".
    cmd += ["--variant", variant]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONUTF8="1"), cwd=HERE)
    ups = glob.glob(os.path.join(scratch, "DK_upload_*.csv"))
    ups = [u for u in ups if "_cash_" not in os.path.basename(u)]
    if not ups:
        return None, (r.stdout or r.stderr or "").strip()[-120:]
    return pd.read_csv(ups[0]), None


def tenth_place(scores):
    """The score that finished 10th in the real contest, or None.

    `scores` is the field's score list, descending. Top 10 is the objective,
    so this is the bar every arm is measured against.
    """
    s = sorted(scores, reverse=True)
    return s[9] if len(s) >= 10 else (s[-1] if s else None)


def score_portfolio(d, fpts, scores, bar):
    """Score one rebuilt portfolio against what actually happened.

    Payout scoring was removed on 09/03. ROI measured the CONTEST rather than
    the build -- the same lineup pays differently in two fields on the same
    slate, and the min-cash tail pays for exactly the mid-pack finishes this
    portfolio is not built for. The objective is a top-10 FINISH, so the arm
    is scored on where its lineups LANDED: best, the gap from best to the real
    10th-place score, and how many lineups cleared that bar.
    """
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
    gap = (s.max() - bar) if bar is not None else float("nan")
    top10 = sum(1 for r in ranks if r <= 10)
    return {"n": len(s), "mean": s.mean(), "sd": s.std(), "best": s.max(),
            "worst": s.min(), "best_rank": min(ranks), "gap": gap,
            "top10": top10, "missing": missing}


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
    ap.add_argument("--build-seeds", type=int, default=1,
                    help="passed to each build as --seeds: let a constrained "
                         "arm refill its shortfall from further seeds so both "
                         "arms are scored at the same portfolio size")
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
    # Per-process, not a fixed "bt_variants": build_once wipes this directory
    # before every build, so two sweeps running at once silently destroyed
    # each other's inputs mid-build.
    scratch = os.path.abspath(os.path.join(tempfile.gettempdir(),
                                           "bt_variants_%d" % os.getpid()))
    results = []
    for snap, cpath, cov in pairs:
        fpts, scores, n_field = contests[cpath]
        bar = tenth_place(scores)
        slate = os.path.basename(snap)
        cid = re.search(r"(\d+)", os.path.basename(cpath)).group(1)
        bar_s = f"{bar:.2f}" if bar is not None else "n/a"
        print(f"\n=== {slate}  vs contest {cid}  "
              f"({n_field} entries, {cov:.0%} player match, "
              f"10th = {bar_s}) ===")
        # Averaged over seeds: mean/sd/best/worst/gap are per-portfolio
        # figures averaged across the seeds, so they stay on the same scale as
        # a single-seed run. bestRk is the best rank ANY seed reached, and n
        # is the portfolio size actually delivered -- a hard floor underfills,
        # and an arm that builds 45 of 60 is not comparable on `best` to one
        # that builds 60 without saying so.
        print(f"{'variant':<12}{'sds':>4}{'n':>4}{'mean':>8}{'+-':>6}{'sd':>7}"
              f"{'best':>8}{'worst':>7}{'bestRk':>8}{'gap':>9}")
        for v in variants:
            per_seed = []
            for sd_ in seeds:
                tick(f"  building {v} seed {sd_} ...")
                d, err = build_once(snap, v, sd_, args.lineups, scratch,
                                    args.build_seeds)
                if d is None:
                    tick()
                    print(f"{v:<12}  seed {sd_} build failed: {err}")
                    continue
                m = score_portfolio(d, fpts, scores, bar)
                m.update(slate=slate, contest=cid, variant=v, seed=sd_)
                results.append(m)
                per_seed.append(m)
            tick()
            if not per_seed:
                continue
            g = pd.DataFrame(per_seed)
            gap_s = ("n/a" if g["gap"].isna().all()
                     else f"{g['gap'].mean():+.2f}")
            print(f"{v:<12}{len(g):>4}{g['n'].mean():>4.0f}"
                  f"{g['mean'].mean():>8.1f}"
                  f"{fmt(se(g['mean'])):>6}{g['sd'].mean():>7.1f}"
                  f"{g['best'].mean():>8.1f}{g['worst'].mean():>7.1f}"
                  f"{g['best_rank'].min():>8}{gap_s:>9}")
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
    print(f"{'variant':<12}{'slates':>7}{'sds':>5}{'nAvg':>6}{'mean':>8}"
          f"{'sd':>7}{'bestAvg':>9}{'gapAvg':>9}{'top10':>7}")
    for v, g in df.groupby("variant", sort=False):
        # gap averages over seeds within a slate, then over slates, so a slate
        # with more seeds does not outvote one with fewer.
        per_slate = g.groupby("slate")[["gap", "top10"]].mean()
        gap_s = ("n/a" if per_slate["gap"].isna().all()
                 else f"{per_slate['gap'].mean():+.2f}")
        print(f"{v:<12}{g['slate'].nunique():>7}{g['seed'].nunique():>5}"
              f"{g['n'].mean():>6.0f}{g['mean'].mean():>8.1f}"
              f"{g['sd'].mean():>7.1f}{g['best'].mean():>9.1f}"
              f"{gap_s:>9}{per_slate['top10'].sum():>7.1f}")


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
    print(f"{'variant':<12}{'pairs':>6}{'dBest':>8}{'+-':>7}{'t':>7}"
          f"{'dMean':>8}{'dGap':>8}{'dN':>6}  verdict")
    piv = {k: df.pivot_table(index=["slate", "seed"], columns="variant",
                             values=k)
           for k in ("mean", "best", "gap", "n")}

    def delta(k, v):
        """Per-pair (variant - control) for one metric, empty when either arm
        has no numbers on that pair."""
        p = piv[k]
        if v not in p.columns or "control" not in p.columns:
            return pd.Series(dtype=float)
        return (p[v] - p["control"]).dropna()

    # The t-test is on BEST, not mean. The objective is a top-10 finish, and
    # mean points per lineup is the proxy that shipped spend15 (+10.60 on the
    # mean, p=0.056, then lost all three live slates). dMean is still printed
    # -- an arm that lifts the mean while losing `best` is the signature of a
    # constraint trading ceiling for floor, and that is worth seeing.
    for v in others:
        d = delta("best", v)
        if d.empty:
            continue
        s = se(d)
        t = d.mean() / s if not pd.isna(s) and s > 0 else float("nan")
        dm = delta("mean", v).mean()
        dg = delta("gap", v).mean()
        dn = delta("n", v).mean()
        if pd.isna(t):
            verdict = ("need >1 pair" if len(d) < 2
                       else "no spread across pairs")
        elif abs(t) < 2:
            verdict = "not separable from noise"
        else:
            verdict = "better than control" if t > 0 else "worse than control"
        print(f"{v:<12}{len(d):>6}{d.mean():>+8.1f}{fmt(s):>7}{fmt(t, '.2f'):>7}"
              f"{dm:>+8.1f}{fmt(dg, '+.2f'):>8}{fmt(dn, '+.1f'):>6}  {verdict}")
    print("\nt is dBest/SE. |t| < 2 means this run cannot tell the arm apart\n"
          "from control -- which is a result, not a failure to measure.\n"
          "dN is the portfolio-size difference: a negative dN means the arm\n"
          "underfilled, and its dBest is then partly just having fewer draws.")


if __name__ == "__main__":
    main()
