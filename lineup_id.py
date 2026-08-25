r"""Stable identifier for a DK lineup, shared by the builder and the
post-contest analyzer.

Why this exists: portfolio_summary_<date>.csv and post_entries_<id>.csv had no
common key, so there was no way to ask "did the lineups the model ranked
highest actually score highest?" -- the only shared column was salary, which
is not unique (a 19-row summary joined to a 20-row entry file produced 38
rows, and any correlation off that join is meaningless).

The id hashes player NAMES rather than DK ids, because the contest standings
export only gives names. Slot assignment is deliberately ignored: the same ten
players are the same lineup whether a guy filled OF1 or OF3.

Keep norm_name in sync with the normalizers in build_portfolio.py,
validate_upload.py and post_contest.py -- they all target DK's spelling.
"""
import hashlib


def norm_name(s):
    s = str(s).lower()
    for ch in ".,'-":
        s = s.replace(ch, "")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return " ".join(t for t in s.split() if len(t) > 1)


def lineup_id(names):
    """10 player names (any order) -> short stable hex id."""
    key = "|".join(sorted(norm_name(n) for n in names))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
