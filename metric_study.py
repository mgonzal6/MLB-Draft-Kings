r"""Score candidate lineup-ranking metrics against realised DK results.

Joins every portfolio_summary to its contest's post_entries on lineup_id and
correlates each numeric metric column with actual points -- per slate and
pooled. This is the harness that decides whether a metric is worth ranking
lineups by; nothing here changes how lineups are built.

Why it exists: floor_target (formerly `floor`) looked strongly predictive on
one day and strongly ANTI-predictive on another. Over 5 slates it netted to
+0.015 on within-slate ranks -- i.e. no signal at all. Any replacement needs
to clear that bar on slates it was not chosen from, so the metrics are
recorded first and judged later.

    python metric_study.py                    # all slates it can pair up
    python metric_study.py --metric ceiling   # one metric, per-lineup detail

Pairing: a summary is matched to the post_entries file whose lineup_ids
overlap it most. Hand-edited entries simply do not match, which is correct --
a lineup that was changed before submission is not the lineup that was built.
"""
import argparse
import glob
import os
import re

import pandas as pd

EXPORT_DIR = r"G:\My Drive\DK\export"
POST_DIR = r"G:\My Drive\DK\Post Contest"
SNAPSHOT_DIR = r"G:\My Drive\DK\Snapshots"

SKIP_COLS = {"#", "salary", "hitter_salary"}   # not ranking metrics
NAME_RE = re.compile(r"^(.*?)\s*\(\d+\)\s*$")


def summaries():
    """Every portfolio_summary on disk, newest copy of each name wins."""
    seen = {}
    for d in [POST_DIR, EXPORT_DIR] + sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "*"))):
        for p in glob.glob(os.path.join(d, "portfolio_summary_*.csv")):
            # _prevHHMM files are superseded records kept for safety, not
            # separate slates -- counting them would weight one slate twice.
            if "_prev" in os.path.basename(p):
                continue
            seen.setdefault(os.path.basename(p), p)
    return sorted(seen.values())


def entries():
    out = []
    for p in sorted(glob.glob(os.path.join(POST_DIR, "post_entries_*.csv"))):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if "lineup_id" in df.columns and "Points" in df.columns:
            out.append((os.path.basename(p), df))
    return out


def backfill_ids(summ, summ_path):
    """Summaries built before lineup_id existed: rebuild ids from the matching
    DK_upload file, whose cells are 'Name (ID)'. Row i of the upload is row i
    of the summary -- both come from the same list in build_portfolio.py."""
    from lineup_id import lineup_id
    up_path = os.path.join(os.path.dirname(summ_path),
                           os.path.basename(summ_path)
                           .replace("portfolio_summary_", "DK_upload_"))
    if not os.path.exists(up_path):
        return None
    up = pd.read_csv(up_path)
    if len(up) != len(summ):
        return None
    ids = []
    for _, row in up.iterrows():
        names = []
        for v in row:
            m = NAME_RE.match(str(v).strip())
            if not m:
                return None
            names.append(m.group(1))
        ids.append(lineup_id(names))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default=None,
                    help="show per-lineup detail for one metric")
    ap.add_argument("--min-n", type=int, default=5,
                    help="skip slates with fewer joined lineups (default 5)")
    args = ap.parse_args()

    ent = entries()
    if not ent:
        raise SystemExit(f"no post_entries_*.csv with lineup_id in {POST_DIR}\n"
                         f"run post_contest.py first")

    paired, pooled, paired_keys = [], [], []
    for sp in summaries():
        try:
            s = pd.read_csv(sp)
        except Exception:
            continue
        # Pre-rename summaries call it "floor"; same number, same formula.
        # Without this the four slates of history sit in a separate row from
        # every future build and the comparison starts over at n=1.
        if "floor" in s.columns and "floor_target" not in s.columns:
            s = s.rename(columns={"floor": "floor_target"})
        if "lineup_id" not in s.columns:
            ids = backfill_ids(s, sp)
            if ids is None:
                continue
            s = s.copy()
            s["lineup_id"] = ids
        # pair with whichever contest shares the most lineups
        best, best_n = None, 0
        for name, e in ent:
            n = len(set(s["lineup_id"]) & set(e["lineup_id"]))
            if n > best_n:
                best, best_n = (name, e), n
        if not best or best_n < args.min_n:
            continue
        name, e = best
        m = s.merge(e, on="lineup_id", how="inner")
        tag = os.path.basename(sp).replace("portfolio_summary_", "").replace(".csv", "")
        # An A/B arm is a DIFFERENT set of lineups in the same contest and must
        # be kept. A summary holding the SAME lineups as one already paired to
        # that contest (e.g. `_control`, which is byte-identical to the plain
        # build) is the same data under another name -- keep one.
        key = (name, frozenset(m["lineup_id"]))
        if key in {k for k, _, _, _ in paired_keys}:
            continue
        paired_keys.append((key, tag, name, m))
        paired.append((tag, name, m))
        pooled.append(m.assign(_slate=tag))

    if not paired:
        raise SystemExit("no summary could be paired with a contest")

    metrics = []
    for _, _, m in paired:
        for c in m.columns:
            if c in SKIP_COLS or c in metrics:
                continue
            if c in ("Points", "Rank", "Pctile", "Won", "DupCount"):
                continue
            if pd.api.types.is_numeric_dtype(m[c]):
                metrics.append(c)

    if args.metric:
        for tag, name, m in paired:
            if args.metric not in m.columns:
                continue
            mm = m.sort_values(args.metric, ascending=False)
            print(f"\n=== {tag}  ({name}, n={len(mm)}) ===")
            print(f"{args.metric:>14}{'points':>9}{'rank':>8}  stack")
            for _, r in mm.iterrows():
                print(f"{r[args.metric]:>14.1f}{r['Points']:>9.2f}"
                      f"{int(r['Rank']):>8}  {r.get('stack', '')}")
        return

    print(f"slates paired: {len(paired)}")
    for tag, name, m in paired:
        print(f"  {tag:<22} {name:<32} n={len(m)}")
    print()

    hdr = f"{'metric':<16}" + "".join(f"{t[:8]:>10}" for t, _, _ in paired) \
          + f"{'POOLED':>10}{'WITHIN':>9}"
    print(hdr)
    print("-" * len(hdr))
    allm = pd.concat(pooled, ignore_index=True)
    for c in metrics:
        cells = ""
        for _, _, m in paired:
            if c in m.columns and m[c].notna().sum() > 2 and m[c].nunique() > 1:
                cells += f"{m[c].corr(m['Points'], method='spearman'):>+10.2f}"
            else:
                cells += f"{'-':>10}"
        if c in allm.columns and allm[c].nunique() > 1:
            pool = allm[c].corr(allm["Points"], method="spearman")
            sub = allm.dropna(subset=[c])
            within = (sub.groupby("_slate")[c].rank(pct=True)
                      .corr(sub.groupby("_slate")["Points"].rank(pct=True)))
            print(f"{c:<16}{cells}{pool:>+10.2f}{within:>+9.2f}")
        else:
            print(f"{c:<16}{cells}{'-':>10}{'-':>9}")

    print()
    print("Spearman rank correlation with realised points. WITHIN = "
          "within-slate percentile ranks,")
    print("the fair test: these metrics only ever compare lineups on ONE "
          "slate. Treat anything")
    print("under ~10 slates as provisional -- floor_target read +0.63 on its "
          "first two.")


if __name__ == "__main__":
    main()
