r"""
mlb_odds.py — Pull MLB odds (moneyline, run line, totals) from The Odds API.

Usage:
    python mlb_odds.py                          # moneyline + run line + totals
    python mlb_odds.py --markets h2h            # just moneyline (cheapest)
    python mlb_odds.py --csv                    # auto-named CSV into the export folder
    python mlb_odds.py --csv odds.csv           # named CSV into the export folder
    python mlb_odds.py --upcoming               # /upcoming endpoint, filtered to MLB

CSV exports default to:  G:\My Drive\DK\export

Quota note: The Odds API charges 1 request per market per region. So the default
3 markets x 1 region (us) = 3 requests per run. Use --markets h2h to spend less.

The API key is read from the ODDS_API_KEY environment variable if set,
otherwise it falls back to the key hard-coded below.
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

import requests

# Fallback key (override anytime with:  setx ODDS_API_KEY "yourkey")
DEFAULT_API_KEY = "3542b587371c7371fa1786db3ca09e8b"

BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

# CSV exports land here unless --csv is given an absolute path of its own.
EXPORT_DIR = r"G:\My Drive\DK\export"

MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Run line", "totals": "Total (O/U)"}


def fetch_odds(api_key, sport="baseball_mlb", regions="us",
               markets="h2h,spreads,totals", odds_format="american"):
    """Call The Odds API and return parsed JSON (a list of events)."""
    params = {
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
        "apiKey": api_key,
    }
    url = BASE_URL.format(sport=sport)
    resp = requests.get(url, params=params, timeout=30)

    # Quota headers are handy to keep an eye on your monthly request budget.
    remaining = resp.headers.get("x-requests-remaining")
    used = resp.headers.get("x-requests-used")
    last_cost = resp.headers.get("x-requests-last")

    if resp.status_code != 200:
        sys.exit(
            f"Request failed [{resp.status_code}]: {resp.text}\n"
            "Check your API key, sport key, markets, or remaining quota."
        )

    if remaining is not None:
        print(f"API quota - this call cost {last_cost}, "
              f"used: {used}, remaining: {remaining}\n")

    return resp.json()


def to_local(iso_ts):
    """Convert an ISO8601 UTC timestamp to a readable local time string."""
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return dt.astimezone().strftime("%Y-%m-%d %I:%M %p")


def fmt_price(price):
    """Format an American odds value with an explicit + sign."""
    if price is None:
        return "n/a"
    return f"+{price}" if price > 0 else str(price)


def fmt_point(point):
    """Format a spread/total line value with an explicit + sign."""
    if point is None:
        return ""
    return f"+{point}" if point > 0 else str(point)


def get_outcomes(book, market_key):
    """Return the outcomes list for a given market within a bookmaker, or None."""
    for market in book.get("markets", []):
        if market.get("key") == market_key:
            return market.get("outcomes", [])
    return None


def markets_present(bookmakers):
    """Return the markets (in display order) that at least one book offers."""
    present = []
    for key in ("h2h", "spreads", "totals"):
        if any(get_outcomes(b, key) for b in bookmakers):
            present.append(key)
    return present


def format_line(market_key, outcomes, away, home):
    """Build a readable 'away side | home side' string for one book/market."""
    by_name = {o.get("name"): o for o in outcomes}

    if market_key == "h2h":
        a = by_name.get(away, {})
        h = by_name.get(home, {})
        return (f"{away} {fmt_price(a.get('price')):>6}   |   "
                f"{home} {fmt_price(h.get('price')):>6}")

    if market_key == "spreads":
        a = by_name.get(away, {})
        h = by_name.get(home, {})
        return (f"{away} {fmt_point(a.get('point')):>5} ({fmt_price(a.get('price'))})"
                f"   |   {home} {fmt_point(h.get('point')):>5} ({fmt_price(h.get('price'))})")

    if market_key == "totals":
        over = by_name.get("Over", {})
        under = by_name.get("Under", {})
        return (f"Over {over.get('point')} ({fmt_price(over.get('price'))})"
                f"   |   Under {under.get('point')} ({fmt_price(under.get('price'))})")

    return ""


def best_prices(bookmakers, market_key):
    """Best (highest-payout) American price per outcome across all books.

    For American odds the higher numeric value is always the better price for
    the bettor (+150 beats +140; -140 beats -150), so max() does the job.
    Returns {outcome_name: (price, point, book_title)}.
    """
    best = {}
    for book in bookmakers:
        outcomes = get_outcomes(book, market_key)
        if not outcomes:
            continue
        for o in outcomes:
            name, price = o.get("name"), o.get("price")
            if price is None:
                continue
            if name not in best or price > best[name][0]:
                best[name] = (price, o.get("point"), book.get("title"))
    return best


def print_best(market_key, best, away, home):
    """Print the >> Best line for a market, ordered away then home."""
    def cell(name):
        if name not in best:
            return f"{name} n/a"
        price, point, book = best[name]
        if point is None:
            pt = ""
        elif market_key == "totals":
            pt = f"{point} "          # totals: plain line, no +/- sign
        else:
            pt = f"{fmt_point(point)} "  # spreads: signed run line
        return f"{name} {pt}{fmt_price(price)} ({book})"

    if market_key == "totals":
        order = ["Over", "Under"]
    else:
        order = [away, home]
    print(f"      >> Best:  {cell(order[0])}   |   {cell(order[1])}")


def print_odds(events):
    """Pretty-print each game, grouped by market, with a best-price line."""
    if not events:
        print("No games returned. (MLB may be off-day, or the slate isn't posted yet.)")
        return

    for ev in events:
        home = ev.get("home_team", "?")
        away = ev.get("away_team", "?")
        start = to_local(ev["commence_time"]) if ev.get("commence_time") else "TBD"
        books = ev.get("bookmakers", [])

        print(f"{away} @ {home}   ({start})")

        for market_key in markets_present(books):
            print(f"  {MARKET_LABELS[market_key]}:")
            for book in books:
                outcomes = get_outcomes(book, market_key)
                if not outcomes:
                    continue
                title = book.get("title", book.get("key", "?"))
                print(f"    {title:<14} {format_line(market_key, outcomes, away, home)}")
            print_best(market_key, best_prices(books, market_key), away, home)
        print()


def resolve_export_path(name):
    """Resolve a CSV target: absolute paths are used as-is, bare names go to
    EXPORT_DIR. Creates the destination folder if it doesn't exist."""
    if os.path.isabs(name):
        path = name
    else:
        path = os.path.join(EXPORT_DIR, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def write_csv(events, path):
    """Flatten every bookmaker/market/outcome into rows and save as CSV."""
    rows = []
    pulled = datetime.now(timezone.utc).isoformat()
    for ev in events:
        home = ev.get("home_team")
        away = ev.get("away_team")
        start = ev.get("commence_time")
        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                for o in market.get("outcomes", []):
                    rows.append({
                        "pulled_at_utc": pulled,
                        "commence_time": start,
                        "away_team": away,
                        "home_team": home,
                        "bookmaker": book.get("title"),
                        "market": market.get("key"),
                        "outcome": o.get("name"),
                        "point": o.get("point"),
                        "american_odds": o.get("price"),
                    })

    if not rows:
        print("Nothing to write — no odds found.")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def main():
    parser = argparse.ArgumentParser(description="Pull MLB odds from The Odds API.")
    parser.add_argument("--markets", default="h2h,spreads,totals",
                        help="comma-separated markets: h2h, spreads, totals (default: all three)")
    parser.add_argument("--csv", nargs="?", const="AUTO", metavar="PATH",
                        help=f"also save a CSV. Bare filenames go to {EXPORT_DIR}; "
                             "pass --csv alone for an auto-named timestamped file.")
    parser.add_argument("--upcoming", action="store_true",
                        help="use the /upcoming endpoint (mixed sports) instead of baseball_mlb")
    parser.add_argument("--regions", default="us", help="comma-separated regions (default: us)")
    args = parser.parse_args()

    api_key = os.environ.get("ODDS_API_KEY", DEFAULT_API_KEY)
    sport = "upcoming" if args.upcoming else "baseball_mlb"

    events = fetch_odds(api_key, sport=sport, regions=args.regions, markets=args.markets)

    # The /upcoming feed mixes sports — keep only MLB.
    if args.upcoming:
        events = [e for e in events if e.get("sport_key") == "baseball_mlb"]

    print_odds(events)

    if args.csv:
        name = args.csv
        if name == "AUTO":
            name = f"mlb_odds_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        write_csv(events, resolve_export_path(name))


if __name__ == "__main__":
    main()
