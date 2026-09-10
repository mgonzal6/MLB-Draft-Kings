"""Cross-arm fill: merge distinct lineups from a donor arm into a base arm.

Why this exists. A thin slate caps the portfolio well below the entry count --
09/09 delivered 15 of a 90 request -- and the shortfall otherwise becomes
DUPLICATES. A duplicate scores identically to its twin, so it is worth zero
extra draws; a distinct lineup from another arm is worth one, and "more draws"
is the strongest lever measured here (20 -> 40 took the top-10 hit rate
0.062 -> 0.250).

Done by hand on 09/07 (71 distinct + 9 dupes -> 79 + 1) and 09/09 (15 -> 24).
This makes it repeatable and, unlike the 09/09 run, states the concentration
cost instead of silently accepting it.

CONCENTRATION GATE, and what it is measured against. The exposure caps are
fractions of the REQUESTED lineup count, so on a slate delivering a fraction
of the request they are sized to a portfolio that never existed -- 09/09's
base held one SP at 60% against a 40% cap before any merging, and 09/07's
held one bat at 64% against a 20% cap. An absolute cap rejects every donor.

A strictly-no-worse test also rejects every donor, and that version was wrong:
it compares against an empty slot. THE REAL ALTERNATIVE TO A DONOR IS A
DUPLICATE. Duplicating an Edman lineup puts Edman in the portfolio again just
as a donor might, so declining the donor does not protect concentration -- it
just forfeits a draw. Duplicates are proportional to the base, so filling the
shortfall that way leaves the worst exposure roughly where it started.

So the gate allows the worst exposure to drift up to --tolerance above the
BASE portfolio's worst exposure (default 5 points), measured against the base
rather than the running merge so admitting one donor cannot ratchet the bar.
--tolerance 0 restores the strict test; --force skips it and prints the damage.

Pick donors that share the base arm's measured components. minspend49cov +
ilvthin differ ONLY in spec attempt order, so their disagreements are exactly
the lineups the other never reached. minspend49cov + covnofloor was used on
09/09 and DILUTES the salary floor -- the floor is worth -4.08 dBest on deep
slates and -3.79 on mid when removed, so that pairing has a real cost.
"""
import argparse
import collections
import csv
import os
import re
import sys

ABSOLUTE_SP_CAP = 0.40
FILL_CAP = 0.20


def nm(x):
    return re.sub(r"\s*\(\d+\)\s*$", "", str(x)).strip()


def load(path):
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    return rows[0], [r for r in rows[1:] if any(r)]


def sig(row):
    return tuple(sorted(nm(x) for x in row[:10] if str(x).strip()))


def counts(rows):
    sp, hit = collections.Counter(), collections.Counter()
    for r in rows:
        sp[nm(r[0])] += 1
        sp[nm(r[1])] += 1
        for x in r[2:10]:
            hit[nm(x)] += 1
    return sp, hit


def worst(rows):
    """(max SP share, max hitter share) over the portfolio."""
    sp, hit = counts(rows)
    n = max(1, len(rows))
    return (max(sp.values()) / n if sp else 0.0,
            max(hit.values()) / n if hit else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="base arm upload CSV")
    ap.add_argument("--donor", required=True, action="append",
                    help="donor upload CSV (repeatable, applied in order)")
    ap.add_argument("--out", required=True, help="merged upload CSV to write")
    ap.add_argument("--target", type=int, default=0,
                    help="stop once this many distinct lineups are held")
    ap.add_argument("--exclude-team", default="",
                    help="comma-separated teams whose games have STARTED; any "
                         "donor containing one is rejected (late-swap safety)")
    ap.add_argument("--salaries", default=r"G:\My Drive\DK\export\Filtered_DKSalaries.csv")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="how far the worst exposure may drift above the "
                         "BASE portfolio's worst, as a fraction (default "
                         "0.05 = 5 points). The alternative to a donor is a "
                         "duplicate, which concentrates just as much and "
                         "adds no draw, so 0 is too strict")
    ap.add_argument("--force", action="store_true",
                    help="admit donors even if they worsen concentration")
    args = ap.parse_args()

    team = {}
    if os.path.exists(args.salaries):
        team = {r["Name"]: r["TeamAbbrev"]
                for r in csv.DictReader(open(args.salaries, encoding="utf-8-sig"))}
    locked = {t.strip().upper() for t in args.exclude_team.split(",") if t.strip()}

    hdr, base = load(args.base)
    out, seen = list(base), {sig(r) for r in base}
    base_sp, base_hit = worst(base)
    print(f"base {os.path.basename(args.base)}: {len(base)} lineups   "
          f"worst SP {base_sp:.0%}, worst bat {base_hit:.0%}")

    admitted = collections.Counter()
    rejected = collections.Counter()
    for dpath in args.donor:
        if args.target and len(out) >= args.target:
            break
        _, donor = load(dpath)
        label = os.path.basename(dpath)
        for r in donor:
            if args.target and len(out) >= args.target:
                break
            s = sig(r)
            if len(s) != 10 or s in seen:
                rejected["already held / malformed"] += 1
                continue
            if locked and {team.get(nm(x)) for x in r[:10]} & locked:
                rejected["contains a started team"] += 1
                continue
            if not args.force:
                nsp, nhit = worst(out + [r])
                if (nsp > base_sp + args.tolerance
                        or nhit > base_hit + args.tolerance):
                    rejected["past concentration tolerance"] += 1
                    continue
            seen.add(s)
            out.append(r)
            admitted[label] += 1

    sp, hit = counts(out)
    n = len(out)
    print(f"merged: {n} distinct  (+{sum(admitted.values())} from donors)")
    for k, v in admitted.items():
        print(f"    +{v:3d}  {k}")
    for k, v in rejected.items():
        print(f"    rejected {v:4d}  {k}")
    if sp:
        p, c = sp.most_common(1)[0]
        print(f"  top SP  {p} {c}/{n} = {100*c/n:.0f}%   (cap {ABSOLUTE_SP_CAP:.0%} "
              f"of the REQUEST, not of {n})")
    if hit:
        p, c = hit.most_common(1)[0]
        print(f"  top bat {p} {c}/{n} = {100*c/n:.0f}%   (cap {FILL_CAP:.0%})")
    if args.target and n < args.target:
        print(f"  SHORT of {args.target} by {args.target - n} -- the remainder "
              f"becomes duplicates, which add no coverage")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        w.writerows(out)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
