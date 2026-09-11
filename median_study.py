"""What separates our lineups from the MEDIAN field lineup?

Every study in this file compares us to TOP-10 lineups -- 190 of them against
58,706 field lineups. That answers "why are we not winning". It does not
answer "why do we finish where we finish", which is the question the money
asks: EV is set by the whole distribution, and our median entry lands near the
53rd percentile (post-09/03) against a neutral 50th.

So this splits the FIELD into bands by finishing percentile and profiles each
one the same way, with ours alongside. A feature that separates top-10 from
the field is interesting; a feature that varies MONOTONICALLY across the whole
distribution is actionable, because we can move along it.

Post-09/03 contests only -- pooling across the 09/03 harness-fix and
minspend49 regime change is what produced two wrong conclusions on 09/11.
"""
import collections
import csv
import datetime
import glob
import os
import re
import statistics as st
import sys

D = r"G:\My Drive\DK\Post Contest"
CUT = datetime.date(2026, 9, 3)


def parse(s):
    out = []
    for t in re.split(r"\s(?=(?:1B|2B|3B|C|OF|P|SS)\s)", s.strip()):
        m = re.match(r"^(1B|2B|3B|C|OF|P|SS)\s+(.*)$", t.strip())
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def profile(lineups, fpts, own):
    """Mean features of a set of lineups."""
    z, w4, t3, os_, lo = [], [], [], [], []
    for l in lineups:
        v = sorted(fpts.get(n, 0.0) for _, n in l)
        if len(v) < 10:
            continue
        z.append(sum(1 for x in v if x <= 0.0))
        w4.append(sum(v[:4]))
        t3.append(sum(v[-3:]))
        o = [own.get(n, 0.0) for _, n in l]
        os_.append(sum(o))
        lo.append(sum(sorted(o)[:3]))
    if not z:
        return None
    return dict(n=len(z), zeros=st.mean(z), worst4=st.mean(w4),
                top3=st.mean(t3), own_sum=st.mean(os_), own_low3=st.mean(lo))


def main():
    files = []
    for f in glob.glob(os.path.join(D, "contest-standings-*.csv")):
        if datetime.date.fromtimestamp(os.path.getmtime(f)) >= CUT:
            files.append(f)
    print(f"{len(files)} post-{CUT} standings exports")

    BANDS = [(0, 1, "top 1%"), (1, 10, "1-10%"), (10, 25, "10-25%"),
             (25, 50, "25-50%"), (50, 75, "50-75%"), (75, 100, "75-100%")]
    acc = {b[2]: [] for b in BANDS}
    ours = []
    for f in files:
        cid = re.search(r"contest-standings-(\d+)", os.path.basename(f)).group(1)
        fpts, own, rows = {}, {}, []
        for r in csv.DictReader(open(f, encoding="utf-8-sig", errors="replace")):
            n, v, o = r.get("Player"), r.get("FPTS"), r.get("%Drafted")
            if n and v not in (None, ""):
                try:
                    fpts[n.strip()] = float(v)
                    own[n.strip()] = float(str(o).rstrip("%")) if o else 0.0
                except ValueError:
                    pass
            if r.get("Rank") and r.get("Lineup") and r.get("Points") not in (None, ""):
                try:
                    rows.append((float(r["Points"]), parse(r["Lineup"])))
                except ValueError:
                    pass
        if len(rows) < 100:
            continue
        rows.sort(key=lambda x: -x[0])
        n = len(rows)
        for lo, hi, lbl in BANDS:
            seg = rows[int(lo / 100 * n):max(int(lo / 100 * n) + 1, int(hi / 100 * n))]
            p = profile([l for _, l in seg], fpts, own)
            if p:
                acc[lbl].append(p)
        pe = os.path.join(D, f"post_entries_{cid}.csv")
        if os.path.exists(pe):
            for r in csv.DictReader(open(pe, encoding="utf-8-sig", errors="replace")):
                if r.get("Lineup"):
                    ours.append(parse(r["Lineup"]))
            p = profile(ours[-40:], fpts, own)
            if p:
                acc.setdefault("OURS", []).append(p)

    print(f"\n{'band':<12}{'zeros':>8}{'worst4':>9}{'top3':>9}"
          f"{'own sum':>10}{'own low3':>10}")
    for _, _, lbl in BANDS:
        v = acc.get(lbl)
        if not v:
            continue
        print(f"{lbl:<12}{st.mean(x['zeros'] for x in v):>8.2f}"
              f"{st.mean(x['worst4'] for x in v):>9.2f}"
              f"{st.mean(x['top3'] for x in v):>9.2f}"
              f"{st.mean(x['own_sum'] for x in v):>10.1f}"
              f"{st.mean(x['own_low3'] for x in v):>10.2f}")
    v = acc.get("OURS")
    if v:
        print(f"{'OURS':<12}{st.mean(x['zeros'] for x in v):>8.2f}"
              f"{st.mean(x['worst4'] for x in v):>9.2f}"
              f"{st.mean(x['top3'] for x in v):>9.2f}"
              f"{st.mean(x['own_sum'] for x in v):>10.1f}"
              f"{st.mean(x['own_low3'] for x in v):>10.2f}")
    print("\nA feature that moves monotonically down the bands is one the whole"
          "\ndistribution responds to. Compare OURS against the 25-50% band --"
          "\nthat is where our median entry actually lands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
