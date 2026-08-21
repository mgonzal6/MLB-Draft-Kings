"""
build_bs_cache.py  —  Breakout Score Cache Builder
===================================================
Run this once after each day's games finish (or whenever you get a new feed file).
Produces two CSVs to upload to Claude alongside your daily Lineups + DKSalaries:

    pitcher_bs_cache.csv
    hitter_bs_cache.csv

USAGE
-----
    # Default (uses Google Drive paths below — just run it):
    python build_bs_cache.py

    # Override feed file:
    python build_bs_cache.py --feed26 "path/to/new-feed.parquet"

    # Force full rebuild from scratch:
    python build_bs_cache.py --rebuild

    # Save to a specific folder:
    python build_bs_cache.py --out ./cache

REQUIREMENTS
------------
    pip install pandas pyarrow numpy

WHAT TO UPLOAD TO CLAUDE EACH SESSION
--------------------------------------
    pitcher_bs_cache.csv    ← this script produces these
    hitter_bs_cache.csv     ← this script produces these
    Lineups_YYYY_MM_DD.csv  ← download from DK each day
    DKSalaries.csv          ← download from DK each day
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

DRIVE = r"G:\My Drive\DK\export"

def parse_args():
    p = argparse.ArgumentParser(
        description="Build/update pitcher and hitter breakout score caches."
    )
    p.add_argument("--feed26",  default=rf"{DRIVE}\2026-mlb-season-player-feed.parquet",
                   help="2026 season file — parquet or csv (BigDataBall player feed)")
    p.add_argument("--feed25",  default=rf"{DRIVE}\MLB-2025-Player-BoxScore-Dataset.parquet",
                   help="2025 season file — parquet or csv (BigDataBall player feed)")
    p.add_argument("--out",     default=DRIVE,
                   help="Output directory for cache CSVs (default: Google Drive export folder)")
    p.add_argument("--rebuild", action="store_true",
                   help="Ignore existing cache and rebuild everything from scratch")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_df(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        sys.exit(f"ERROR: file not found — {path}")
    ext = p.suffix.lower()
    if ext == ".parquet":
        try:
            return pd.read_parquet(p)
        except ImportError:
            sys.exit("ERROR: pyarrow not installed. Run: pip install pyarrow")
    if ext in (".csv", ".txt"):
        return pd.read_csv(p)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(p)
    sys.exit(f"ERROR: unsupported file type '{ext}' — use parquet, csv, or xlsx")


# ─────────────────────────────────────────────────────────────────────────────
# PITCHER / HITTER SPLIT
# ─────────────────────────────────────────────────────────────────────────────

# Verify this matches your parquet: print([c for c in df.columns if "PITCH" in c.upper()])
# It will be "STARTING\nPITCHER" (newline) or "STARTING PITCHER" (space) depending on the Excel source.
SP_COL = "STARTING\nPITCHER"

def split(df: pd.DataFrame):
    """Split BigDataBall feed into (pitchers, hitters) by SP_COL presence."""
    is_sp = df[SP_COL].notna() & (df[SP_COL].astype(str).str.strip() != "")
    return df[is_sp].copy(), df[~is_sp].copy()


# ─────────────────────────────────────────────────────────────────────────────
# DK SCORING — shared with comparison.py so projections and actuals can never
# be scored with different formulas.
# ─────────────────────────────────────────────────────────────────────────────

from dk_scoring import dk_pitcher, dk_hitter, ip_to_innings


# ─────────────────────────────────────────────────────────────────────────────
# BREAKOUT SCORE — PITCHERS
# ─────────────────────────────────────────────────────────────────────────────

def compute_pitcher_bs(p26: pd.DataFrame, p25: pd.DataFrame) -> pd.DataFrame:
    """
    Returns one row per pitcher with breakout score and supporting stats.
    Formula:
        blended  = 0.6 × avg26 + 0.4 × avg25
        BS = min(blended × 1.8, 40)
           + form_bonus        (-12 cold | +8 L3>130% | +4 L3>avg | 0)
           + clamp(yoy × 3, -15, +15)
           + min(k9/12 × 15, 15)
           + bb9_penalty       (0 | -5 if >3 | -12 if >4 | -25 if >5)
    """
    p26 = p26.copy()
    p25 = p25.copy()
    p26["dk"] = p26.apply(dk_pitcher, axis=1)
    p25["dk"] = p25.apply(dk_pitcher, axis=1)

    # Season averages
    agg26 = (p26.groupby("PLAYER")["dk"]
               .agg(avg26="mean", gs26="count")
               .reset_index())
    agg25 = (p25.groupby("PLAYER")["dk"]
               .agg(avg25="mean", gs25="count")
               .reset_index())

    df = agg26.merge(agg25, on="PLAYER", how="left")
    df["avg25"]     = df["avg25"].fillna(df["avg26"])
    df["gs25"]      = df["gs25"].fillna(0).astype(int)
    df["blended"]   = 0.6 * df["avg26"] + 0.4 * df["avg25"]
    df["yoy_delta"] = df["avg26"] - df["avg25"]

    # K/9 and BB/9 from 2026
    def rate_stat(player, stat_col):
        rows = p26[p26["PLAYER"] == player]
        total = rows[stat_col].fillna(0).astype(float).sum()
        inn   = rows["IP"].apply(ip_to_innings).sum()
        return (total / inn * 9) if inn > 0 else 0.0

    df["k9"]  = df["PLAYER"].apply(lambda x: rate_stat(x, "SO.1"))
    df["bb9"] = df["PLAYER"].apply(lambda x: rate_stat(x, "BB.1"))

    # L3 form bonus
    last3_avg = (p26.sort_values("DATE")
                    .groupby("PLAYER")["dk"]
                    .apply(lambda x: x.tail(3).mean())
                    .reset_index(name="l3_avg"))
    df = df.merge(last3_avg, on="PLAYER", how="left")
    df["l3_avg"] = df["l3_avg"].fillna(df["avg26"])

    def form_bonus(row):
        if row["avg26"] == 0: return 0
        ratio = row["l3_avg"] / row["avg26"]
        if ratio > 1.30: return  8
        if ratio > 1.00: return  4
        if ratio < 0.50: return -12
        return 0

    df["form"] = df.apply(form_bonus, axis=1)

    def bb9_pen(bb9):
        if bb9 > 5: return -25
        if bb9 > 4: return -12
        if bb9 > 3: return  -5
        return 0

    df["bs"] = (
        (df["blended"] * 1.8).clip(upper=40)
        + df["form"]
        + df["yoy_delta"].clip(-5, 5) * 3
        + (df["k9"] / 12 * 15).clip(upper=15)
        + df["bb9"].apply(bb9_pen)
    ).clip(0, 99).round(2)

    return df[[
        "PLAYER", "avg26", "avg25", "gs26", "gs25",
        "blended", "yoy_delta", "k9", "bb9", "form", "l3_avg", "bs"
    ]].sort_values("bs", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# BREAKOUT SCORE — HITTERS
# ─────────────────────────────────────────────────────────────────────────────

def compute_hitter_bs(h26: pd.DataFrame, h25: pd.DataFrame) -> pd.DataFrame:
    """
    Returns one row per hitter with breakout score and supporting stats.
    Formula:
        BS = min(avg26 × 2.8, 35)
           + form_bonus        (-15 cold | +10 hot | +5 L3>avg | 0)
           + clamp(yoy × 4, -20, +20)
           + min(ceiling / 6, 10)
           + min(hr_per_game × 50, 10)
           + min(bb_pct × 25, 5)
           + min(sb_per_game × 8, 4)
    """
    h26 = h26.copy()
    h25 = h25.copy()
    h26["dk"] = h26.apply(dk_hitter, axis=1)
    h25["dk"] = h25.apply(dk_hitter, axis=1)

    # L3 average
    last3 = (h26.sort_values("DATE")
                .groupby("PLAYER")["dk"]
                .apply(lambda x: x.tail(3).mean())
                .reset_index(name="l3_avg"))

    agg26 = h26.groupby("PLAYER").agg(
        avg26    = ("dk",  "mean"),
        games26  = ("dk",  "count"),
        ceiling  = ("dk",  "max"),
        hr_pg    = ("HR",  lambda x: x.astype(float).mean()),
        sb_pg    = ("SB",  lambda x: x.astype(float).mean()),
        total_bb = ("BB",  lambda x: x.astype(float).sum()),
        total_pa = ("PA",  lambda x: x.astype(float).sum()),
    ).reset_index()

    agg26 = agg26.merge(last3, on="PLAYER", how="left")
    agg26["l3_avg"] = agg26["l3_avg"].fillna(agg26["avg26"])
    agg26["bb_pct"] = (agg26["total_bb"]
                       / agg26["total_pa"].replace(0, np.nan)).fillna(0)

    agg25 = h25.groupby("PLAYER")["dk"].agg(avg25="mean").reset_index()

    df = agg26.merge(agg25, on="PLAYER", how="left")
    df["avg25"]     = df["avg25"].fillna(df["avg26"])
    df["yoy_delta"] = df["avg26"] - df["avg25"]

    def form_bonus(row):
        if row["avg26"] == 0: return 0
        ratio = row["l3_avg"] / row["avg26"]
        if ratio > 1.50: return  10
        if ratio > 1.00: return   5
        if ratio < 0.40: return -15
        return 0

    df["form"] = df.apply(form_bonus, axis=1)

    df["bs"] = (
        (df["avg26"] * 2.8).clip(upper=35)
        + df["form"]
        + df["yoy_delta"].clip(-5, 5) * 4
        + (df["ceiling"] / 6).clip(upper=10)
        + (df["hr_pg"] * 50).clip(upper=10)
        + (df["bb_pct"] * 25).clip(upper=5)
        + (df["sb_pg"] * 8).clip(upper=4)
    ).clip(0, 99).round(2)

    return df[[
        "PLAYER", "avg26", "avg25", "games26",
        "yoy_delta", "form", "ceiling", "hr_pg", "sb_pg",
        "bb_pct", "l3_avg", "bs"
    ]].sort_values("bs", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# INCREMENTAL CACHE UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def players_needing_update(new_df: pd.DataFrame, cache_path: Path,
                           games_col: str) -> set:
    """
    Compare new feed against cache.
    Returns set of player names whose game count changed or who are brand new.
    """
    if not cache_path.exists():
        return set(new_df["PLAYER"])

    cached = pd.read_csv(cache_path)
    new_counts = new_df.groupby("PLAYER").size().reset_index(name="n_new")
    merged = cached[["PLAYER", games_col]].merge(new_counts, on="PLAYER", how="outer")
    merged["n_new"]   = merged["n_new"].fillna(0).astype(int)
    merged[games_col] = merged[games_col].fillna(0).astype(int)

    stale = merged[merged["n_new"] != merged[games_col]]["PLAYER"]
    return set(stale.dropna())


def save_cache(new_scores: pd.DataFrame, cache_path: Path, rebuild: bool) -> pd.DataFrame:
    """
    Merge new scores into existing cache file.
    New scores overwrite matching players; cache-only players are kept.
    """
    if rebuild or not cache_path.exists() or len(new_scores) == 0:
        if len(new_scores) > 0:
            new_scores.to_csv(cache_path, index=False)
        return new_scores if len(new_scores) > 0 else pd.read_csv(cache_path)

    cached = pd.read_csv(cache_path)
    merged = pd.concat([
        cached[~cached["PLAYER"].isin(new_scores["PLAYER"])],
        new_scores,
    ], ignore_index=True)
    merged.to_csv(cache_path, index=False)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    p_cache = out_dir / "pitcher_bs_cache.csv"
    h_cache = out_dir / "hitter_bs_cache.csv"

    t0 = time.time()

    # ── Load feeds ───────────────────────────────────────────────────────────
    print("Loading feed files...")
    df26 = load_df(args.feed26)
    df25 = load_df(args.feed25)
    print(f"  2026: {len(df26):,} rows  |  2025: {len(df25):,} rows")

    pitchers26, hitters26 = split(df26)
    pitchers25, hitters25 = split(df25)
    print(f"  Pitchers 2026: {len(pitchers26):,}  |  Hitters 2026: {len(hitters26):,}")

    # ── Pitcher cache ────────────────────────────────────────────────────────
    if args.rebuild:
        to_recomp_p = set(pitchers26["PLAYER"])
        print(f"\nPitchers: full rebuild ({len(to_recomp_p)} players)...")
    else:
        to_recomp_p = players_needing_update(pitchers26, p_cache, "gs26")
        if to_recomp_p:
            print(f"\nPitchers: updating {len(to_recomp_p)} changed players...")
        else:
            print("\nPitchers: cache is current — nothing to recompute.")

    if to_recomp_p:
        p_new = compute_pitcher_bs(
            pitchers26[pitchers26["PLAYER"].isin(to_recomp_p)],
            pitchers25,
        )
        p_bs = save_cache(p_new, p_cache, args.rebuild)
        print(f"  ✅  pitcher_bs_cache.csv updated  ({len(p_bs):,} total players)")
    else:
        p_bs = pd.read_csv(p_cache)

    # ── Hitter cache ─────────────────────────────────────────────────────────
    if args.rebuild:
        to_recomp_h = set(hitters26["PLAYER"])
        print(f"\nHitters: full rebuild ({len(to_recomp_h)} players)...")
    else:
        to_recomp_h = players_needing_update(hitters26, h_cache, "games26")
        if to_recomp_h:
            print(f"\nHitters: updating {len(to_recomp_h)} changed players...")
        else:
            print("\nHitters: cache is current — nothing to recompute.")

    if to_recomp_h:
        h_new = compute_hitter_bs(
            hitters26[hitters26["PLAYER"].isin(to_recomp_h)],
            hitters25,
        )
        h_bs = save_cache(h_new, h_cache, args.rebuild)
        print(f"  ✅  hitter_bs_cache.csv updated  ({len(h_bs):,} total players)")
    else:
        h_bs = pd.read_csv(h_cache)

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print(f"\n{'─'*60}")
    print(f"Done in {elapsed:.1f}s")
    print(f"\nTop 10 pitchers by BS:")
    print(p_bs.head(10)[["PLAYER","avg26","blended","k9","bb9","bs"]]
              .to_string(index=False))
    print(f"\nTop 10 hitters by BS:")
    print(h_bs.head(10)[["PLAYER","avg26","yoy_delta","form","bs"]]
              .to_string(index=False))
    print(f"\nUpload these two files to Claude each session:")
    print(f"  {p_cache.resolve()}")
    print(f"  {h_cache.resolve()}")
    print(f"{'─'*60}")


if __name__ == "__main__":
    main()