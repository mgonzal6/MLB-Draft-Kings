"""
dk_scoring.py — single source of truth for DraftKings MLB Classic scoring.

Both base_analysis.py (projections/caches) and comparison.py (actuals) import
from here so they can never drift apart again. Before this module existed,
comparison.py scored pitchers with a QS x4 bonus (not part of DK Classic) and
treated BigDataBall's "6.2 IP" as 6.2 innings instead of 6 2/3 — so every
projection-vs-actual diff was measured against a different scoring system than
the projections used.

DK Classic scoring:
    Pitchers: out x0.75, K x2, W x4, ER x-2, H x-0.6, BB x-0.6, HBP x-0.6,
              CG x2.5, CG SHO x2.5, No-hitter x5.   (No QS bonus.)
    Hitters:  1B x3, 2B x5, 3B x8, HR x10, RBI x2, R x2, BB x2, HBP x2, SB x5.

Column names are BigDataBall player-feed conventions ("SO.1"/"H.1"/"BB.1" are
the pitching-side stats; "CG\\nSHO" carries the Excel newline). Pass `prefix`
when the frame's columns are prefixed (comparison.py uses "Batting "/"Pitching ").
"""


def ip_to_innings(ip) -> float:
    """BigDataBall writes 6.2 to mean 6 and 2/3 innings."""
    try:
        ip = float(ip)
        full = int(ip)
        frac = round((ip - full) * 10)
        return full + frac / 3.0
    except (TypeError, ValueError):
        return 0.0


def _g(row, col, prefix="") -> float:
    """Numeric cell getter: blank/None/NaN/garbage all come back as 0.0."""
    v = row.get(prefix + col, 0)
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f  # NaN -> 0


def dk_pitcher(row, prefix="") -> float:
    ip = ip_to_innings(row.get(prefix + "IP", 0))
    s  = ip * 3 * 0.75        # outs x 0.75
    s += _g(row, "SO.1", prefix) * 2
    s += _g(row, "W",    prefix) * 4
    s -= _g(row, "ER",   prefix) * 2
    s -= _g(row, "H.1",  prefix) * 0.6
    s -= _g(row, "BB.1", prefix) * 0.6
    s -= _g(row, "HB",   prefix) * 0.6
    s += _g(row, "CG",   prefix) * 2.5
    s += _g(row, "CG\nSHO", prefix) * 2.5
    s += _g(row, "NH",   prefix) * 5
    return s


def dk_hitter(row, prefix="") -> float:
    return (_g(row, "1B", prefix) * 3 + _g(row, "2B", prefix) * 5
            + _g(row, "3B", prefix) * 8 + _g(row, "HR", prefix) * 10
            + _g(row, "RBI", prefix) * 2 + _g(row, "R", prefix) * 2
            + _g(row, "BB", prefix) * 2 + _g(row, "HBP", prefix) * 2
            + _g(row, "SB", prefix) * 5)
