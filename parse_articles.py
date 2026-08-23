r"""
parse_articles.py — read locally-saved article archives (.mht/.mhtml/.html)
and pull out whatever projection/ranking content they contain.

These are pages YOU saved from your own browser, so there is no scraping and
no bot-detection involved — this only ever reads local files.

    input   G:\My Drive\DK\Articles\<source>_<YYYY_MM_DD>.mht
            e.g. espn_2026_08_21.mht

    output  G:\My Drive\DK\Articles\parsed_<source>_<YYYY_MM_DD>.csv
            one row per player found, with any numbers on that row

Usage:
    python parse_articles.py                       # every file in ARTICLE_DIR
    python parse_articles.py --file <path>
    python parse_articles.py --dump <path>         # show extracted text only
    python parse_articles.py --selftest            # decoder checks, no files
"""
import argparse
import csv
import email
import glob
import html as htmllib
import os
import quopri
import re
import sys
import unicodedata
from email import policy

ARTICLE_DIR = r"G:\My Drive\DK\Articles"
EXPORT_DIR = r"G:\My Drive\DK\export"


# ─────────────────────────────────────────────────────────────────────────────
# MHT -> HTML
# ─────────────────────────────────────────────────────────────────────────────

def mht_to_html(path):
    """Pull the primary text/html part out of a saved MHTML archive.

    MHT is multipart/related: the page plus every image/css/font as separate
    MIME parts, usually quoted-printable encoded. We want the biggest HTML
    part (the page itself, not an embedded iframe/ad fragment).
    """
    with open(path, "rb") as fh:
        raw = fh.read()

    # A plain .html save has no MIME envelope — pass it straight through.
    head = raw[:400].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return raw.decode("utf-8", "replace")

    msg = email.message_from_bytes(raw, policy=policy.default)
    best = ""
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:                       # decode=True can fail on odd CTEs
            payload = quopri.decodestring(part.get_payload().encode("utf-8", "replace"))
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, "replace")
        if len(text) > len(best):
            best = text
    return best


def strip_tags(fragment):
    fragment = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", fragment)
    fragment = re.sub(r"(?i)</t[dh]>", "\t", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = htmllib.unescape(fragment)
    fragment = re.sub(r"[ \t\xa0]+", " ", fragment)
    fragment = re.sub(r" *\n *", "\n", fragment)
    return re.sub(r"\n{3,}", "\n\n", fragment).strip()


def html_tables(doc):
    """Return each <table> as a list of rows, each row a list of cell strings."""
    out = []
    for tbl in re.findall(r"(?is)<table[^>]*>.*?</table>", doc):
        rows = []
        for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", tbl):
            cells = [strip_tags(c).replace("\n", " ").strip()
                     for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
            if any(cells):
                rows.append(cells)
        if len(rows) > 1:
            out.append(rows)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Content extraction
# ─────────────────────────────────────────────────────────────────────────────

def norm_name(s):
    """Fold accents, punctuation and suffixes so 'Cristopher S\u00E1nchez' and
    'Cristopher Sanchez' compare equal."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for ch in ".,'\u2019-":
        s = s.replace(ch, "")
    s = re.sub(r"\s+(jr|sr|ii|iii|iv)$", "", s.strip())
    return " ".join(s.split())


def load_roster(slate_dir):
    """Player names for the slate, from the DK salary file."""
    path = os.path.join(slate_dir, "Filtered_DKSalaries.csv")
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            nm = (row.get("Name") or "").strip()
            if nm:
                out[norm_name(nm)] = nm
    return out


def find_players(text, roster):
    """Find rostered players in the article, with numbers from the same line.

    Matching a KNOWN roster rather than a generic name pattern: prose like
    "Stream Ryan Weathers for..." makes a generic pattern grab "Stream Ryan",
    and there is no reason to guess when the slate's player list is on disk.
    """
    found = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) > 600:
            continue
        nline = " " + norm_name(line) + " "
        nums = re.findall(r"(?<![\w.])(\d+\.\d+|\d+)(?![\w%])", line)
        for key, display in roster.items():
            if f" {key} " not in nline:
                continue
            rec = found.setdefault(display, {"name": display, "numbers": [],
                                             "lines": []})
            if nums and len(rec["numbers"]) < 12:
                rec["numbers"].extend(nums)
            if len(rec["lines"]) < 3:
                rec["lines"].append(line[:220])
    return found


def parse_file(path, roster, verbose=False):
    doc = mht_to_html(path)
    if not doc:
        print(f"  !! no HTML part found in {os.path.basename(path)}")
        return None, None, None
    text = strip_tags(doc)
    tables = html_tables(doc)
    players = find_players(text, roster)
    if verbose:
        print(text[:6000])
    return text, tables, players


# ─────────────────────────────────────────────────────────────────────────────

def selftest():
    """Round-trip a synthetic MHT through the decoder."""
    import tempfile
    body = (
        "<html><body><h1>Pitcher Rankings</h1>"
        "<table><tr><th>Rank</th><th>Pitcher</th><th>FPTS</th></tr>"
        "<tr><td>1</td><td>Tarik Skubal</td><td>16.0</td></tr>"
        "<tr><td>2</td><td>Dylan Cease</td><td>14.2</td></tr></table>"
        "<p>Stream Ryan Weathers for another strong effort.</p>"
        "<script>var x = 'ignore me';</script></body></html>")
    mht = ("From: <Saved by Blink>\r\n"
           "Subject: test\r\n"
           "MIME-Version: 1.0\r\n"
           'Content-Type: multipart/related; boundary="----B"\r\n\r\n'
           "------B\r\n"
           "Content-Type: text/html; charset=utf-8\r\n"
           "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
           + quopri.encodestring(body.encode()).decode() + "\r\n"
           "------B\r\n"
           "Content-Type: image/png\r\n"
           "Content-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n"
           "------B--\r\n")
    with tempfile.NamedTemporaryFile("w", suffix=".mht", delete=False,
                                     newline="") as fh:
        fh.write(mht)
        tmp = fh.name
    roster = {norm_name(n): n for n in
              ("Tarik Skubal", "Dylan Cease", "Ryan Weathers",
               "Cristopher Sánchez", "Nobody Here")}
    try:
        text, tables, players = parse_file(tmp, roster)
        assert "Pitcher Rankings" in text, "header lost"
        assert "ignore me" not in text, "script not stripped"
        assert tables and ["2", "Dylan Cease", "14.2"] in tables[0], tables
        assert "Dylan Cease" in players, sorted(players)
        assert "14.2" in players["Dylan Cease"]["numbers"], players["Dylan Cease"]
        assert "Ryan Weathers" in players, "prose name missed"
        assert "Nobody Here" not in players, "matched a player not in the text"
        # accent folding both directions
        assert norm_name("Cristopher Sánchez") == norm_name("Cristopher Sanchez")
        assert norm_name("Luis Garcia Jr.") == norm_name("Luis Garcia")
        print("SELFTEST PASSED — MHT decode, tag strip, tables, "
              "roster matching, accent/suffix folding")
    finally:
        os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser(description="Parse locally saved articles.")
    ap.add_argument("--dir", default=ARTICLE_DIR)
    ap.add_argument("--file")
    ap.add_argument("--slate", default=EXPORT_DIR,
                    help="dir holding Filtered_DKSalaries.csv (the roster)")
    ap.add_argument("--dump", help="print extracted text of one file and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    roster = load_roster(args.slate)
    if not roster:
        sys.exit(f"no Filtered_DKSalaries.csv in {args.slate} — the parser "
                 f"matches article text against the slate's player list, so "
                 f"point --slate at the folder holding that day's file.")
    print(f"roster: {len(roster)} players from {args.slate}")

    if args.dump:
        parse_file(args.dump, roster, verbose=True)
        return

    files = [args.file] if args.file else sorted(
        f for ext in ("mht", "mhtml", "html")
        for f in glob.glob(os.path.join(args.dir, f"*.{ext}"))
        if not os.path.basename(f).startswith("parsed_"))
    if not files:
        sys.exit(f"no .mht/.mhtml/.html files in {args.dir}\n"
                 f"save articles there as <source>_<YYYY_MM_DD>.mht")

    for path in files:
        base = os.path.basename(path)
        print(f"\n=== {base}")
        text, tables, players = parse_file(path, roster)
        if text is None:
            continue
        print(f"  text {len(text):,} chars | {len(tables)} tables | "
              f"{len(players)} candidate players")
        for i, rows in enumerate(tables[:3]):
            print(f"  table {i}: {len(rows)} rows, header {rows[0][:6]}")

        out = os.path.join(os.path.dirname(path),
                           "parsed_" + os.path.splitext(base)[0] + ".csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["player", "numbers_on_line", "context"])
            for rec in sorted(players.values(), key=lambda r: r["name"]):
                w.writerow([rec["name"], " ".join(rec["numbers"][:12]),
                            " | ".join(rec["lines"])])
        print(f"  wrote {out}")


if __name__ == "__main__":
    main()
