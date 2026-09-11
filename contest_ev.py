"""Price a contest's PAYOUT SHAPE against our own finishing distribution.

Measured 09/10, the first dollar analysis in the project. Three real contests
priced against 1,269 entered lineups, and the same number appeared every time:
our cash rate lands almost exactly ON the payout line (10.64% vs 10.70% in a
28,000 GPP; 23.13% vs 23.30% in a 1,189 flat). We are, in aggregate, an
average entry -- so EV is set by two things:

    EV / fee  =  (1 - rake)  x  edge(curve)

where edge(curve) is how much better than the AVERAGE entry our distribution
scores against that particular payout curve. It is not a constant: against a
winner-take-all it is ~0 (we have never finished 1st), against a flat deep
curve it was +4.6%. So the contest's SHAPE decides whether our edge exists at
all, and the rake decides whether it survives.

This tool computes edge(curve) for arbitrary curves, so a contest can be
priced before entering without uploading anything -- you only need its shape.

Usage:
    python contest_ev.py                 # sweep standard shapes
    python contest_ev.py --bands "1:100,2:60,8:10,277:2" --field 1189 --rake 0.159

`--bands` is "last_place:multiple_of_entry_fee", cumulative, comma separated.
"""
import argparse
import csv
import glob
import os
import statistics as st
import sys

D = r"G:\My Drive\DK\Post Contest"


def our_percentiles():
    """Finishing percentile of every lineup we have ever entered."""
    out = []
    for f in glob.glob(os.path.join(D, "post_entries_*.csv")):
        for r in csv.DictReader(open(f, encoding="utf-8-sig", errors="replace")):
            try:
                out.append(float(r["Pctile"]))
            except (ValueError, KeyError, TypeError):
                pass
    return out


def price(bands, field, pcts):
    """(our EV per entry, average entry's EV) in fee multiples, before rake."""
    def prize(rank):
        for last, mult in bands:
            if rank <= last:
                return mult
        return 0.0
    ours = st.mean(prize(max(1, round(p / 100.0 * field))) for p in pcts)
    pool = 0.0
    prev = 0
    for last, mult in bands:
        pool += (last - prev) * mult
        prev = last
    avg = pool / field                      # what a random entry earns
    return ours, avg


# Standard shapes, expressed as fractions of the field so they scale.
# (label, [(cumulative fraction of field, prize in fee multiples)], note)
def shapes(field):
    f = lambda x: max(1, round(x * field))
    return [
        ("winner-take-all", [(1, 0.85 * field)],
         "one prize, the whole pool"),
        ("top-heavy GPP", [(f(0.0001), 125), (f(0.0005), 50), (f(0.001), 25),
                           (f(0.005), 10), (f(0.01), 6), (f(0.036), 4),
                           (f(0.107), 2)],
         "28,000-entry shape: 27% of pool in the top 10"),
        ("flat deep GPP", [(f(0.0008), 100), (f(0.005), 15), (f(0.018), 5),
                           (f(0.044), 4), (f(0.098), 3), (f(0.233), 2)],
         "1,189-entry shape: pays 23%"),
        ("very flat", [(f(0.05), 4), (f(0.15), 2.5), (f(0.35), 1.6)],
         "pays 35%, shallow top"),
        ("double-up", [(f(0.45), 2.0)],
         "pays top 45% exactly 2x"),
        ("50/50", [(f(0.5), 1.8)],
         "pays top half 1.8x"),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", type=int, default=1189)
    ap.add_argument("--rake", type=float, default=0.159)
    ap.add_argument("--bands", default=None,
                    help="last:multiple,... e.g. 1:100,2:60,277:2")
    a = ap.parse_args()

    pcts = our_percentiles()
    if not pcts:
        print("no post_entries_*.csv found")
        return 2
    print(f"our distribution: {len(pcts)} entered lineups, "
          f"median finish {st.median(pcts):.1f}th percentile")

    if a.bands:
        bands = []
        for tok in a.bands.split(","):
            last, mult = tok.split(":")
            bands.append((int(last), float(mult)))
        ours, avg = price(bands, a.field, pcts)
        edge = ours / avg if avg else 0.0
        ev = ours * (1 - a.rake) / max(avg, 1e-9) * avg / max(avg, 1e-9)
        print(f"\nfield {a.field}, rake {100*a.rake:.1f}%")
        print(f"  average entry earns {avg:.4f}x     we earn {ours:.4f}x")
        print(f"  our edge over the field  {100*(edge-1):+.1f}%")
        print(f"  EV after rake            {ours*(1-a.rake)/avg:.4f}x  "
              f"ROI {100*(ours*(1-a.rake)/avg - 1):+.1f}%")
        return 0

    print(f"\nfield {a.field}, edge = how much better than an AVERAGE entry "
          f"our\ndistribution scores against each shape. "
          f"Break-even needs edge > rake/(1-rake).")
    print(f"\n{'shape':<18}{'pays':>7}{'our edge':>10}{'break-even rake':>17}   note")
    for lbl, bands, note in shapes(a.field):
        ours, avg = price(bands, a.field, pcts)
        edge = (ours / avg - 1) if avg else 0.0
        # profitable while rake < edge/(1+edge)
        be = edge / (1 + edge) if edge > 0 else 0.0
        paid = 100.0 * bands[-1][0] / a.field
        print(f"{lbl:<18}{paid:>6.1f}%{100*edge:>+9.1f}%{100*be:>16.1f}%   {note}")
    print("\nDK rake is typically 10-20%. A shape whose break-even rake is\n"
          "below that cannot be beaten with our current distribution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
