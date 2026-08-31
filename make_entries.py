r"""Write built lineups into DK's entries (late-swap) CSV.

    python make_entries.py                         # newest DKEntries in load/
    python make_entries.py --arms control,maxcorr47
    python make_entries.py --entries "G:\...\DKEntries (829pm).csv"

DK's bulk-edit export lists every entry you hold for a draft group, one row
per entry, with the roster in ten columns whose headers repeat (P,P,...,
OF,OF,OF). Rows come in three kinds and each is handled differently:

  blank    a reservation with no lineup yet -- free to fill
  open     a lineup with no locked player -- free to overwrite
  locked   contains at least one "(LOCKED)" player, meaning that game has
           started. NEVER touched: the slot cannot be changed and rewriting
           the row would corrupt the entry.

Two traps this exists to avoid, both hit by hand on 08/29:

  * pandas deduplicates the repeated headers to P.1/OF.1/OF.2 on read and
    writes those names back out, producing a file DK rejects. Everything here
    uses csv, never pandas, and the header row is copied verbatim.
  * filling a locked row silently discards a started player. Locked rows are
    compared before and after and the run fails if any changed.

Arms are spread evenly across every contest rather than one arm per contest,
so an A/B faces the same field -- the comparison the whole variant programme
depends on.
"""
import argparse
import csv
import glob
import io
import os
import sys

LOAD = r"G:\My Drive\DK\load"
EXPORT = r"G:\My Drive\DK\export"
SLOTS = list(range(4, 14))          # P,P,C,1B,2B,3B,SS,OF,OF,OF
ID_COL, CONTEST_COL = 0, 2


def newest_entries(folder=LOAD):
    """DK's download often mangles the extension ('DKEntries (1).csv (16)'),
    so match on the name rather than trusting it ends in .csv."""
    cand = [p for p in glob.glob(os.path.join(folder, "*"))
            if "dkentries" in os.path.basename(p).lower()
            and not os.path.basename(p).startswith("~$")]
    return max(cand, key=os.path.getmtime) if cand else None


def read_rows(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.reader(io.StringIO(fh.read())))


def state(r):
    if len(r) < max(SLOTS) + 1 or not r[ID_COL].strip():
        return "skip"
    cells = [r[i] for i in SLOTS]
    if not any(c.strip() for c in cells):
        return "blank"
    return "locked" if any("(LOCKED)" in c for c in cells) else "open"


def load_arm(name, slate_date):
    """Newest matching upload wins.

    Two slates can share a calendar date -- an afternoon card and a late one,
    or a rebuild after lineups post -- so several DK_upload files exist for
    the same slate_date. Preferring a fixed suffix picked a STALE build on
    08/29: the _live file was the afternoon 10-game late-swap portfolio, and
    it would have been written into the evening contests with players whose
    games had already finished. Sort by mtime instead.
    """
    pats = ["DK_upload_%s_%s.csv" % (slate_date, name),
            "DK_upload_%s_%s_*.csv" % (slate_date, name)]
    cand = []
    for pat in pats:
        cand += [p for p in glob.glob(os.path.join(EXPORT, pat))
                 if "_cash_" not in os.path.basename(p)]
    if not cand:
        return None, []
    p = max(set(cand), key=os.path.getmtime)
    rows = read_rows(p)[1:]
    return p, [r for r in rows if len(r) >= 10 and r[0].strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", default=None,
                    help="DKEntries csv (default: newest in the load folder)")
    ap.add_argument("--arms", default="control",
                    help="comma-separated variant names to fill from")
    ap.add_argument("--slate-date", default=None,
                    help="e.g. 08_29_2026 (default: infer from the newest "
                         "DK_upload in export)")
    ap.add_argument("--duplicates", action="store_true",
                    help="once distinct lineups run out, repeat them to fill "
                         "every entry. Each entry scores independently so a "
                         "duplicate pays independently, but it adds no "
                         "coverage")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = args.entries or newest_entries()
    if not src or not os.path.exists(src):
        sys.exit("no DKEntries file found in %s -- download your entries from "
                 "DK first" % LOAD)

    slate = args.slate_date
    if not slate:
        ups = sorted(glob.glob(os.path.join(EXPORT, "DK_upload_*_*.csv")),
                     key=os.path.getmtime)
        if not ups:
            sys.exit("no DK_upload files in export to infer the slate date from")
        base = os.path.basename(ups[-1])
        slate = "_".join(base.split("_")[2:5])

    pool, order = {}, []
    for name in [a.strip() for a in args.arms.split(",") if a.strip()]:
        path, rows = load_arm(name, slate)
        if not rows:
            print("  WARN no upload found for arm '%s' (slate %s)" % (name, slate))
            continue
        pool[name] = rows
        order.append(name)
        print("  %-12s %2d lineups  <- %s" % (name, len(rows), os.path.basename(path)))
    if not pool:
        sys.exit("no lineups to place")

    rows = read_rows(src)
    hdr, body = rows[0], rows[1:]

    by_contest = {}
    for i, r in enumerate(body):
        if state(r) in ("blank", "open"):
            by_contest.setdefault(r[CONTEST_COL], []).append(i)
    fillable = sum(len(v) for v in by_contest.values())
    supply = sum(len(v) for v in pool.values())
    # DK appends a player-reference block after the entries: rows with an
    # empty Entry ID and data out in columns 14+. They are not entries and
    # must be counted separately, or the row total is nonsense (297 lines for
    # a 40-entry file). state() already skips them; this is just the report.
    entries = [r for r in body if r and r[ID_COL].strip().isdigit()]
    print("\n%s\n  %d entries across %d contests (+%d player-reference rows)"
          % (os.path.basename(src), len(entries),
             len({r[CONTEST_COL] for r in entries}), len(body) - len(entries)))
    print("  %d fillable, %d lineups available" % (fillable, supply))
    if supply < fillable:
        print("  NOTE only %d of %d fillable rows can be filled" % (supply, fillable))

    # Deal round-robin ACROSS contests instead of filling one contest at a
    # time. Sequential filling gave each contest a contiguous block of the
    # portfolio, and the portfolio is ordered CEILING -> CORE -> CONTRARIAN,
    # so the tiers ended up sorted by contest. On 08/30 that put all twelve
    # contrarian lineups into one contest, which finished 38.5 points off its
    # 10th-place score, while the best lineup of the day landed in the
    # contest with the HIGHEST bar and missed the top ten by 0.50 -- it would
    # have finished top ten in either of the other two. Interleaving gives
    # every contest the same mix of tiers.
    #
    # Nothing here assumes a contest count or equal entries per contest: the
    # deal takes the k-th free row of each contest in turn and stops when a
    # contest runs out, so 2 contests or 9, evenly or unevenly filled, all
    # deal the same way.
    contests = sorted(by_contest)
    slots = []
    for k in range(max((len(v) for v in by_contest.values()), default=0)):
        for cid in contests:
            if k < len(by_contest[cid]):
                slots.append(by_contest[cid][k])

    used = {k: 0 for k in pool}
    dupes = 0
    assigned = {}
    for k, i in enumerate(slots):
        placed = False
        for arm in order[k % len(order):] + order[:k % len(order)]:
            if used[arm] < len(pool[arm]):
                assigned[i] = (arm, pool[arm][used[arm]])
                used[arm] += 1
                placed = True
                break
        if placed or not args.duplicates:
            continue
        # Reuse lineups round-robin once the distinct ones run out. Each
        # entry scores independently, so a duplicate pays independently --
        # but it buys no extra coverage: the same lineup cannot reach the
        # top ten twice by being entered twice. Only worth it when the
        # lineups are +EV on their own.
        arm = order[dupes % len(order)]
        assigned[i] = (arm, pool[arm][dupes // len(order) % len(pool[arm])])
        dupes += 1

    out, filled = [hdr], 0
    for i, r in enumerate(body):
        if i in assigned:
            r = list(r)
            for slot, player in zip(SLOTS, assigned[i][1]):
                r[slot] = player
            filled += 1
        out.append(r)

    dest = args.out or os.path.join(EXPORT, "DKEntries_upload_%s.csv" % slate)
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(out)

    # verification: locked rows untouched, no half-filled row, header verbatim
    chk = read_rows(dest)
    bad = sum(1 for a, b in zip(body, chk[1:])
              if state(a) == "locked" and a != b)
    half = sum(1 for r in chk[1:] if state(r) in ("open", "locked")
               and not all(r[i].strip() for i in SLOTS))
    if chk[0] != hdr or bad or half:
        sys.exit("VERIFICATION FAILED: header ok=%s, locked altered=%d, "
                 "half-filled=%d" % (chk[0] == hdr, bad, half))
    print("  filled %d rows (%s)%s"
          % (filled, ", ".join("%s %d" % (k, v) for k, v in used.items()),
             ", %d duplicates" % dupes if dupes else ""))
    print("  locked rows untouched, header verbatim -- verified")
    print("  wrote %s" % dest)


if __name__ == "__main__":
    main()
