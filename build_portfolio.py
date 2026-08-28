r"""
build_portfolio.py — DK MLB Classic 20-lineup GPP portfolio builder.

Runs entirely locally as the last step of the slate build. Reads the artifacts
the pipeline already produces and writes a DK-uploadable lineup file plus a
readable summary:

    inputs  (G:\My Drive\DK\export):
        Filtered_DKSalaries.csv     slate players, salaries, positions, DK IDs
        Filtered_Lineups.csv        confirmed batting orders + SPs
        vegas.csv                   implied team totals (from vegas_sp_adjust.py)
        pitcher_bs_cache_adj.csv    vegas-adjusted SP table (from vegas_sp_adjust.py)
        hitter_bs_cache.csv         hitter breakout scores (from base_analysis.py)

    outputs (same folder):
        DK_upload_<date>.csv            P,P,C,1B,2B,3B,SS,OF,OF,OF  (Name + ID)
        portfolio_summary_<date>.csv    one row per lineup: tier, stack, SPs, salary

Usage:
    python build_portfolio.py                # 20 GPP lineups, seed 42
    python build_portfolio.py --cash 5       # force 5 floor-first lineups
    python build_portfolio.py --lineups 20 --seed 7
    python build_portfolio.py --selftest     # audit-logic checks only, no files

Cash mode (--cash N) builds floor-maximized lineups — SP pairs from the top
vegas-adj blended arms, hitters by avg26 from top-half implied-total teams,
mini-stacks <=3, no punts below $3K, no darts — and writes them separately to
DK_upload_cash_<date>.csv. They still share uniqueness and exposure caps with
the GPP set because the whole file also enters the mass-entry contest.

Default is 0 on a normal slate (Fix #20) and ALL lineups on a <=4-game slate
(Fix #18) — those two rules come from opposite evidence and are both live.

Strategy rules encoded (from the baseball-analyzer skill's Fix Registry):
    #6/#10  hard-fade offenses facing an SP with vegas-adj BS >= 55 or in the
            bottom implied-total tier: max 1 primary stack, <=25% appearances
    #8      bring-back: stacks in games with total >= 8.0 reserve one hitter
            slot for the opposing offense BEFORE fills (slot-aware)
    #9      stacks use contiguous batting-order windows (wrap allowed)
    #12     SP exposure: below-median vegas-adj blended -> <=20%; bottom two -> <=15%
    #13     SP tiers keyed on vegas-adjusted BLENDED (adj_bs is tiebreaker only)
    #14     stack allocation = 0.6*norm(top-4 hitter BS) + 0.4*norm(implied
            total), capped at MAX_STACKS_PER_TEAM (see #21)
    #22     TESTED AND REJECTED (8/23): SHRINKING stacks to cut variance.
            5-stacks really are higher-variance (sd 34.6 vs 15-19 for 3/4
            stacks, corr(size, |pctile-50|) = +0.32) but the variance is the
            product, not the defect. Two-slate net by schedule:
            5,5,4 -1.40 | 5,4,3 -1.70 (current) | 4,4,3 -2.15 | 4,3,3 -2.35.
            On 8/21 dropping the ceiling stack 5->4 destroyed the portfolio's
            best lineup (139.45 @ rank 179 -> 112.45 @ rank 977) and took
            winnings from $0.50 to $0.00. Knob kept as --stack-sizes.
    #21     TESTED AND REJECTED (8/23): tightening the per-team stack cap from
            3 to 2 does NOT reduce bad lineups. The motivation was sound —
            69% of lineup-score variance on 8/23 came from which team was
            stacked — but rebuilding at cap 2 left the WORST lineup unchanged
            on both slates it applied to (8/21 60.60, 8/23 54.75), because the
            freed slots just went to other teams that busted too. Money was a
            wash to slightly worse (cap 3 -$1.70 vs cap 2 -$1.85 over 8/21 +
            8/23). Knob kept as --max-stacks; default stays 3.
    #15     hitter fill cap: any hitter OUTSIDE the lineup's primary stack
            appears in <=20% of lineups (8/21: Peters 7/20 fills, 0 FPTS)
    #16     same-game override: between the two sides of one game, the
            higher-implied side never gets fewer primary stacks (8/21: COL
            3 + CEILING over CLE 2 at Coors; CLE implied 6.0 won the slate)
    #17     no SP ever exceeds ABSOLUTE_SP_CAP, cap-scaling included; a thin
            arm pool builds FEWER lineups instead (8/22: scaling put Weathers
            at 55% and he scored 4.7)
    #18     slates of <=SMALL_SLATE_GAMES games build all-cash — the GPP tier
            ladder needs a wide pool to be worth its variance (8/22, 3 games:
            cash lineups averaged 89.4 and took both cashes, GPP tiers 69.7)
    #19     cash SP pairs must reach PAIR_FLOOR_PCT of the slate's best pair
    #20     normal slates build ZERO cash lineups by default — the $1 single
            entries went 0-for-9 over 8/22-8/23 at 10x the GPP entry fee, and
            on a wide slate the tiered build outscored cash 85.9 vs 78.6.
            Cash CONSTRUCTION still wins on small slates (#18); what stopped
            was paying for separate single-entry contests.

Hard constraints (audited on every lineup before anything is written):
    salary <= 50000; DK position eligibility respected; <=5 hitters per team;
    no hitter opposing a rostered SP; players span >=2 games; no duplicate
    players; no duplicate lineups (and any two differ by >=2 players).
"""
import argparse
import os
import random
import re
import shutil
import sys
import zlib
from collections import defaultdict
from datetime import datetime

import pandas as pd

from lineup_id import lineup_id

EXPORT_DIR = r"G:\My Drive\DK\export"
SNAPSHOT_DIR = r"G:\My Drive\DK\Snapshots"
ABBR_REMAP = {"OAK": "ATH", "WAS": "WSH", "CHW": "CWS"}
SALARY_CAP = 50000
HITTER_SLOTS = ["C", "1B", "2B", "3B", "SS", "OF1", "OF2", "OF3"]
ALL_SLOTS = ["SP1", "SP2"] + HITTER_SLOTS
NICKS = {"michael": "mike", "leonardo": "leo", "matthew": "matt", "christopher": "chris"}

# Exposure knobs (fractions of the portfolio)
ANCHOR_CAP, SOLID_CAP, FLIER_CAP = 0.40, 0.30, 0.15
BELOW_MEDIAN_CAP, BOTTOM2_CAP = 0.20, 0.15
ABSOLUTE_SP_CAP = 0.40      # Fix #17 — no arm ever exceeds this, scaling included
FADE_APPEAR_CAP = 0.25
FILL_CAP = 0.20             # Fix #15 — non-stack hitter appearance cap
MAX_STACKS_PER_TEAM = 3     # see Fix #21 — 2 was tested and did NOT help
HARD_AVOID_BS = 10          # SP adj_bs below this -> zero exposure
SMALL_SLATE_GAMES = 4       # Fix #18 — at or below this, build all-cash

# Cash-lineup knobs (single-entry double-ups: floor over ceiling)
CASH_HITTER_CAP_PCT = 0.60  # same hitter in at most 60% of the cash lineups
PAIR_FLOOR_PCT = 0.75       # Fix #19 — a pair's summed adj_blended must reach
                            # this share of the slate's best pair
CASH_TEAM_CAP = 3           # mini-stacks only — no 4/5-stacks in cash
CASH_MIN_SALARY = 3000      # no punt plays in cash
CASH_MIN_SPEND = 48000      # cash lineups must use the cap
FADE_SP_BS = 55             # opposing offense hard-faded above this
BRINGBACK_TOTAL = 8.0
PAIR_CAP = 3                # same SP pair at most 3 times
# MIN_FLOOR is gone. It gated on floor_target, which never once fired: across
# 177 lineups floor_target ran 92.4-144.5 against a threshold of 80, so 0 were
# ever rejected. Raising the threshold could not fix it either, because it
# ranks the bad lineups HIGH -- the worst lineup of 08/25 (4.9 pts realised)
# scored 133.3, above that slate's median of 125.2 -- and within slates it is
# anti-predictive (Spearman -0.140 over 5 contests, 1,370 lineups).
#
# Twelve candidate replacements were scored the same way. The best,
# min_hit_sal (the cheapest bat's salary), managed +0.135 clustered by contest
# (p=0.091) and then halved on the two slates it was not chosen from
# (+0.149 -> +0.062). Nothing cleared the bar, so nothing replaces it: an
# unused gate is better than a gate pointing the wrong way. floor_target is
# still COMPUTED and recorded, because metric_study.py scores replacements
# against it.
# Combined salary of the two SPs. None = unconstrained, which is what the
# builder has always done. Measured over 10,965 entries across 5 contests
# (08/23-08/25), SP spend ran the WRONG way against finishing position in
# every contest (r = -0.02, -0.25, -0.20, -0.17, -0.29; pooled slate-demeaned
# -0.161). Entries in the 14-16k bucket reached the top 1% at 2.14%, against
# 0.40% for 20k+, and the top 1% of finishers averaged $16,582 on arms while
# the bottom half averaged $17,809. Our own portfolios sat at 16.3-19.0k with
# 15 of 19 lineups in the two worst buckets on 08/24.
SP_PAIR_SALARY_CAP = 16000

# ── A/B variants ────────────────────────────────────────────────────────────
# Scores used to CHOOSE among valid lineups. 'control' has no score: the
# builder accepts the first valid construction, which is what it has always
# done. Keep these in sync with the columns build_portfolio writes into
# portfolio_summary so metric_study.py can score the arms afterwards.
#
# 'top' is how many of the best-scoring candidates to sample from. top=1 is
# argmax, and on 08/25 argmax was actively harmful: the ceiling arm's realised
# scores had HALF the spread of control (sd 20.1 vs 40.2, range 72.5 vs 142.6)
# while the mean barely moved (60.2 vs 58.5). Its lineups also overlapped each
# other more (1.25 shared players per pair vs 1.01) -- taking the single best
# of 20 keeps landing on the same players, the portfolio correlates, and the
# tail disappears. In a GPP the tail is the entire point: control's 147.45
# finished 5th and paid, its median of 55.00 paid nothing.
def _sum_bs(lu_):
    return (sum(lu_[s]["bs"] for s in HITTER_SLOTS)
            + lu_["SP1"]["adj_bs"] + lu_["SP2"]["adj_bs"])


def _top3_bs(lu_):
    """Concentrated upside: reward a few spiky bats, not eight balanced ones.
    Summing all 8 is what averages the spikes away."""
    return (sum(sorted((lu_[s]["bs"] for s in HITTER_SLOTS), reverse=True)[:3])
            + max(lu_["SP1"]["adj_bs"], lu_["SP2"]["adj_bs"]))


VARIANTS = {
    "ceiling": {"score": _sum_bs, "top": 1},
    "proj_points": {"score": lambda lu_: (sum(lu_[s]["avg26"] for s in HITTER_SLOTS)
                                          + lu_["SP1"]["adj_blended"]
                                          + lu_["SP2"]["adj_blended"]), "top": 1},
    # the one consistent signal so far: spending closer to the cap tracked
    # with WORSE finishes on all five slates measured, so test the opposite
    "cheap_arms": {"score": lambda lu_: -(lu_["SP1"]["salary"]
                                          + lu_["SP2"]["salary"]), "top": 1},
    # ---- keep the quality tilt, drop the convergence ----
    # Same ceiling score, but sample from the best 5 instead of taking the
    # single best, so lineups stay different from each other.
    "topk": {"score": _sum_bs, "top": 5},
    # Chase spikes rather than balance, and still sample.
    "boom": {"score": _top3_bs, "top": 5},
    # ---- budget arms: cap what the two SPs may cost ----
    # score None = control's own "first valid construction", so the ONLY
    # difference from control is the salary ceiling. That isolates the cap:
    # bundling it with a selection rule would leave the two indistinguishable.
    # spend16 targets the observed-best 14-16k bucket; spend15 and spend17
    # bracket it so the backtest can see whether the level or just the
    # direction is what matters.
    "spend16": {"score": None, "top": 1, "sp_cap": 16000},
    "spend15": {"score": None, "top": 1, "sp_cap": 15000},
    "spend17": {"score": None, "top": 1, "sp_cap": 17000},
    # the cap combined with topk's quality tilt, to check they do not fight
    "spend_topk": {"score": _sum_bs, "top": 5, "sp_cap": 16000},
    # ---- weakest-spot floor: the rule three studies converged on ----
    # Not a variance play. min_hit_sal was the best of 12 floor candidates
    # (+0.135) and own_min the only ownership metric to clear significance
    # (+0.126, p=0.015, 12 contests / 27k lineups) -- both say the lineup is
    # decided by its worst roster spot, which is exactly what the deleted
    # MIN_FLOOR could not see. CASH_MIN_SALARY=3000 already does this for
    # cash; the GPP path has never had an equivalent. Fills only: a stack is a
    # contiguous batting-order run and cannot be cherry-picked without
    # breaking the correlation it exists for.
    #
    # Swept at 3000/3500/4000 over 224 builds, 7 slates x 8 seeds, paired and
    # slate-clustered. 4000 was significantly WORSE (top10 -0.72, p=0.029) and
    # 3500 was mixed; both are dropped. 3000 nudged every tail metric the
    # right way -- best +1.71, best_rank -9.44, top10 +0.02, and top-10% per
    # lineup 0.0962 vs control's 0.0916 -- and is the only arm tested all
    # session that did NOT buy it out of spread (sd -0.55, t=-0.90; every
    # other arm ran -2 to -4).
    #
    # It is NOT proven, and one check argues against it: replayed at seed 42,
    # the seed real builds use, the top lineup finished worse on 6 of 7
    # slates, turning a rank 3 of 911 into rank 41 and a rank 5 of 1,189 into
    # rank 31. Across all 8 seeds it is a coin flip (better on 30 slate-seeds,
    # worse on 25), and it wins 4-2 on MEDIAN rank -- seed 42 is simply a good
    # draw for control. Every delta has |t| < 0.7. Kept opt-in for re-testing
    # once there are more than 5 independent contests; do not ship it as the
    # default on this evidence.
    "hitfloor3000": {"score": None, "top": 1, "hitter_min_salary": 3000},
    # ---- minimum total spend ----
    # A guardrail rather than a strategy: 99.5% of the field already clears
    # 45,000 (only 143 of 29,137 priced lineups sit below), so this trims the
    # punt builds instead of reshaping the portfolio. It would have rejected
    # 47 of our 212 entries. Sub-45k lineups took top-10% at 5.59% against the
    # field's 10.44% and produced no top-1% finish, though 0 of 143 is within
    # noise at a 1.04% base rate. Note our OWN sub-45k lineups outscored our
    # dearer ones (85.3 vs 73.8) -- Simpson's paradox, they cluster on
    # high-scoring slates -- which is why this was swept rather than assumed.
    #
    # Swept at 45/46/47k over 256 builds, 8 slates x 8 seeds. All three lifted
    # the mean (+1.49/+1.56/+1.56, p=0.060/0.049/0.060) and, unlike every
    # other arm tried, none paid for it in spread (sd -0.19/-0.24/-0.14
    # against spend15's -3.67). 47k won the tail outright, so it is the one
    # kept:
    #
    #     vs control     best   gap to 10th   hit top-10   sd
    #     minspend47   +5.00        -5.00     .047 -> .094  -0.14
    #
    # i.e. 5 more max points, 5 points closer to the actual 10th-place score,
    # and double the rate at which any lineup cracked the top 10. Nothing is
    # significant (best p=0.139; the top-10 rate is 3 events against 6 out of
    # 64), so this is a directional result, not a proven one.
    "minspend47": {"score": None, "top": 1, "min_total_salary": 47000},
    # Tested and dropped: hitter_min_salary 3000 AND min_total_salary 47000
    # together ("floorspend") scored WORSE than the spend floor alone -- best
    # +1.70 vs +5.00, top-10 rate back to control's .047 -- and compressed
    # spread (-0.95, p=0.077). The two guardrails interfere rather than
    # compose; the hitter floor is what drags it down. Do not recombine.
    # ---- tested and rejected, do not rebuild ----
    # 08/26: capping the CHEAPER arm instead of the pair (7,000) was tried to
    # fix the hole where a 15,000 pair cap made Jesus Luzardo -- $10,100 and
    # the slate's top scorer at 35.4 -- impossible to roster at all. It did fix
    # that: the arm rostered him in 8 of 20 lineups, same as control. It still
    # LOST, and on the metric that matters. Over 336 builds, 7 slates x 8
    # seeds, paired and slate-clustered against control:
    #
    #     arm         best   best_rank   top10        sd
    #     value_arm  -4.24   +33.2 *     -0.69 *   -3.50
    #     value_boom -2.57   +14.5       -0.58 *   -3.29
    #     (* p < 0.05; top10 = lineups in the field's top 10%, of 20)
    #
    # The premise was that keeping aces rosterable would preserve the tail. It
    # did not -- both arms compressed spread as hard as the pair cap does
    # (-3.3 vs -3.7), and landed FEWER top-10% finishes than control's 1.82.
    # Every SP-budget rule tried so far lifts the mean by narrowing the
    # distribution, which is backwards when only the best lineup pays. The
    # field-wide SP-spend signal (r = -0.161 over 10,965 entries) is real, but
    # it is a mean/ROI signal and does not transfer to a tail objective.
}


def norm(s):
    s = str(s).lower()
    for ch in ".,'-":
        s = s.replace(ch, "")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    toks = [NICKS.get(t, t) for t in s.split() if len(t) > 1]  # drop middle initials
    return " ".join(toks)


def stable_rng(seed, *parts):
    """Deterministic RNG independent of PYTHONHASHSEED."""
    key = ":".join(str(p) for p in parts)
    return random.Random(seed ^ zlib.crc32(key.encode()))


# ─────────────────────────────────────────────────────────────────────────────
# Data loading / pools
# ─────────────────────────────────────────────────────────────────────────────

def load_data(export_dir):
    dk = pd.read_csv(f"{export_dir}\\Filtered_DKSalaries.csv")
    lu = pd.read_csv(f"{export_dir}\\Filtered_Lineups.csv")
    vegas = pd.read_csv(f"{export_dir}\\vegas.csv").set_index("team")
    padj = pd.read_csv(f"{export_dir}\\pitcher_bs_cache_adj.csv")
    hcache = pd.read_csv(f"{export_dir}\\hitter_bs_cache.csv")
    dk.columns = dk.columns.str.strip()
    lu.columns = lu.columns.str.strip()

    opp_map, game_map = {}, {}
    slate_date, slate_iso = None, None
    for gi in dk["Game Info"].unique():
        m = re.match(r"(\w+)@(\w+)\s+(\d{2}/\d{2}/\d{4})", str(gi))
        if m:
            a, h, d = m.groups()
            opp_map[a], opp_map[h] = h, a
            game_map[a] = game_map[h] = f"{a}@{h}"
            if slate_date is None:
                slate_date = d.replace("/", "_")
                mm, dd, yyyy = d.split("/")
                slate_iso = f"{yyyy}-{mm}-{dd}"

    # vegas_sp_adjust.py only rewrites pitcher_bs_cache_adj.csv when it
    # succeeds, so an aborted run (lineups pulled before they posted, say)
    # leaves the previous slate's SP table sitting right here. Building on it
    # would silently price today's arms off yesterday's matchups.
    stamp = ""
    if "slate_date" in padj.columns and len(padj):
        stamp = str(padj["slate_date"].iloc[0]).strip()
    if not stamp:
        print("  WARN pitcher_bs_cache_adj.csv has no slate_date stamp (written "
              "before stamping existed) — cannot verify it is today's. Rerun "
              "vegas_sp_adjust.py if the SP numbers look wrong.")
    elif slate_iso and stamp != slate_iso:
        sys.exit(f"ERROR: pitcher_bs_cache_adj.csv is stamped {stamp} but this "
                 f"slate is {slate_iso} — vegas_sp_adjust.py did not complete "
                 f"for today, so this is a stale SP table. Rerun the slate "
                 f"build rather than building on yesterday's arms.")

    dk["nname"] = dk["Name"].map(norm)
    lu["team"] = lu["team code"].astype(str).str.strip().replace(ABBR_REMAP)
    lu["bo"] = lu["batting order"].astype(str).str.strip().str.upper()
    lu["conf"] = lu["confirmed"].astype(str).str.strip().str.upper()
    lu["nname"] = lu["player name"].map(norm)
    hcache["nname"] = hcache["PLAYER"].map(norm)
    padj["nname"] = padj["PLAYER"].map(norm)
    return dk, lu, vegas, padj, hcache, opp_map, game_map, slate_date


def build_sp_pool(dk, lu, padj, opp_map, n_lineups):
    rows = []
    for _, r in lu[(lu["bo"] == "SP") & (lu["conf"] == "Y")].iterrows():
        cand = dk[(dk["nname"] == r["nname"]) & (dk["Roster Position"] == "P")]
        if cand.empty:
            print(f"  WARN SP not in DK slate file: {r['player name']}")
            continue
        d = cand.iloc[0]
        a = padj[padj["nname"] == r["nname"]]
        if a.empty:
            print(f"  WARN SP has no cache/vegas row (skipping): {r['player name']}")
            continue
        a = a.iloc[0]
        rows.append({"name": d["Name"], "id": d["Name + ID"], "team": d["TeamAbbrev"],
                     "opp": opp_map[d["TeamAbbrev"]], "salary": int(d["Salary"]),
                     "adj_bs": a["adj_bs"], "adj_blended": a["adj_blended"],
                     "blended": a["blended"]})
    sp_df = pd.DataFrame(rows)
    if sp_df.empty:
        # "Pulled too early" and "name-match bug" look identical here but need
        # opposite responses, so say which one it is.
        n_sp = int((lu["bo"] == "SP").sum())
        n_conf = int(((lu["bo"] == "SP") & (lu["conf"] == "Y")).sum())
        if n_sp and not n_conf:
            sys.exit(f"ERROR: {n_sp} SPs listed but NONE confirmed=Y — the "
                     f"lineups file was pulled too early. Re-download closer "
                     f"to lock and rerun.")
        sys.exit("ERROR: no confirmed SPs matched the cache/vegas table — "
                 "check name normalization, or rerun vegas_sp_adjust.py "
                 "(pitcher_bs_cache_adj.csv may be from a previous slate).")

    # Fix #12/#13 exposure caps
    med = sp_df["adj_blended"].median()
    bottom2 = set(sp_df.nsmallest(2, "adj_blended")["name"])
    caps = {}
    for _, s in sp_df.iterrows():
        if s["adj_bs"] < HARD_AVOID_BS:
            caps[s["name"]] = 0
        elif s["name"] in bottom2:
            caps[s["name"]] = round(BOTTOM2_CAP * n_lineups)
        elif s["adj_blended"] < med:
            caps[s["name"]] = round(BELOW_MEDIAN_CAP * n_lineups)
        elif s["adj_bs"] >= 40:
            caps[s["name"]] = round(ANCHOR_CAP * n_lineups)
        elif s["adj_bs"] >= 25:
            caps[s["name"]] = round(SOLID_CAP * n_lineups)
        else:
            caps[s["name"]] = round(FLIER_CAP * n_lineups)
    # Fix #17 — the caps must supply 2 SP slots per lineup, but no arm may
    # ever exceed ABSOLUTE_SP_CAP. 8/22: proportional scaling pushed Weathers
    # to 55% and he scored 4.7. Give the deficit to the LOWER-tier arms (the
    # anchors are already at their designed max) and, if even that can't
    # cover it, build fewer lineups rather than over-concentrate one arm.
    ceiling = max(1, round(ABSOLUTE_SP_CAP * n_lineups))
    for k in caps:
        caps[k] = min(caps[k], ceiling)
    need = 2 * n_lineups
    order = sp_df.sort_values("adj_blended", ascending=False)["name"].tolist()
    while sum(caps.values()) < need:
        room = [k for k in order if 0 < caps[k] < ceiling]
        if not room:
            break
        for k in room:
            caps[k] += 1
            if sum(caps.values()) >= need:
                break
    feasible = sum(caps.values()) // 2
    if feasible < n_lineups:
        print(f"  !! arm pool supports only {feasible} lineups at the "
              f"{ABSOLUTE_SP_CAP:.0%} per-SP ceiling (asked for {n_lineups}) — "
              f"building fewer rather than over-concentrating")
    sp_df["cap"] = sp_df["name"].map(caps)
    sp_df = sp_df.sort_values("adj_blended", ascending=False).reset_index(drop=True)
    return sp_df, caps, med, min(feasible, n_lineups)


def build_hitter_pool(dk, lu, hcache, opp_map):
    pool = []
    for _, r in lu[lu["conf"] == "Y"].iterrows():
        bo = pd.to_numeric(r["batting order"], errors="coerce")
        if not (1 <= (bo or 0) <= 9):
            continue
        cand = dk[(dk["nname"] == r["nname"]) & (dk["Roster Position"] != "P")]
        if len(cand) > 1:
            cand = cand[cand["TeamAbbrev"] == r["team"]]
        if cand.empty:
            print(f"  WARN hitter not matched in DK: {r['player name']} ({r['team']})")
            continue
        d = cand.iloc[0]
        if d["TeamAbbrev"] != r["team"]:
            print(f"  WARN team mismatch, dropping: {r['player name']} "
                  f"LU={r['team']} DK={d['TeamAbbrev']}")
            continue
        c = hcache[hcache["nname"] == r["nname"]]
        bs = float(c.iloc[0]["bs"]) if not c.empty else 5.0
        avg26 = float(c.iloc[0]["avg26"]) if not c.empty else 4.0
        slots = set()
        for p in str(d["Roster Position"]).split("/"):
            slots.update({"OF1", "OF2", "OF3"} if p == "OF" else {p})
        pool.append({"name": d["Name"], "id": d["Name + ID"], "team": d["TeamAbbrev"],
                     "opp": opp_map[d["TeamAbbrev"]], "salary": int(d["Salary"]),
                     "bo": int(bo), "bs": bs, "avg26": avg26, "slots": slots})
    return pool


# ─────────────────────────────────────────────────────────────────────────────
# Allocation (Fix #14) + fades (Fix #6/#10)
# ─────────────────────────────────────────────────────────────────────────────

def allocate_stacks(hit_pool, sp_df, vegas, n_lineups, opp_map,
                    max_stacks=MAX_STACKS_PER_TEAM):
    hit_df = pd.DataFrame(hit_pool)
    top4 = hit_df.groupby("team")["bs"].apply(lambda x: x.nlargest(4).sum())
    impl = pd.Series({t: vegas.loc[t, "implied_total"] if t in vegas.index else 4.0
                      for t in top4.index})
    nb = (top4 - top4.min()) / max(top4.max() - top4.min(), 1e-9)
    ni = (impl - impl.min()) / max(impl.max() - impl.min(), 1e-9)
    stackscore = 0.6 * nb + 0.4 * ni

    fades = {s["opp"] for _, s in sp_df.iterrows() if s["adj_bs"] >= FADE_SP_BS}
    fades |= set(impl[impl <= impl.quantile(0.12)].index)

    pool = stackscore.drop(index=[t for t in fades if t in stackscore.index])
    if pool.empty:
        sys.exit("ERROR: every team is hard-faded — no stacks possible.")
    alloc = (pool / pool.sum() * n_lineups).round().astype(int).clip(upper=max_stacks)
    order = pool.sort_values(ascending=False).index.tolist()
    while alloc.sum() > n_lineups:
        for t in reversed(order):
            if alloc[t] > 0:
                alloc[t] -= 1
                break
    # Top-up order: non-faded teams to max_stacks, then faded teams to their
    # Fix #6 maximum of 1; on tiny slates where even that can't fill the count,
    # raise the non-faded cap (never the faded one) until it fits.
    fade_order = [t for t in stackscore.sort_values(ascending=False).index
                  if t in fades]
    caps_t = {t: max_stacks for t in order}
    caps_t.update({t: 1 for t in fade_order})
    alloc = alloc.reindex(order + fade_order, fill_value=0)
    while alloc.sum() < n_lineups:
        for t in order + fade_order:
            if alloc[t] < caps_t[t]:
                alloc[t] += 1
                break
        else:
            for t in order:
                caps_t[t] += 1
            print(f"  small slate: non-faded stack cap raised to {caps_t[order[0]]}")
    # Fix #16 — same-game override: the two sides of one game share the park,
    # so their BS inflation cancels and the market's implied gap is the real
    # signal between them. The higher-implied side never gets fewer stacks.
    for t in list(alloc.index):
        o = opp_map.get(t)
        if (o in alloc.index and impl.get(t, 0) > impl.get(o, 0)
                and alloc[t] < alloc[o]):
            alloc[t], alloc[o] = alloc[o], alloc[t]
    alloc = alloc[alloc > 0].sort_values(ascending=False)
    return alloc, impl, fades


def make_specs(alloc, n_lineups, sizes=(5, 4, 3)):
    """Tier assignment: ~20% ceiling, ~20% contrarian, rest core.

    `sizes` is (ceiling, core, contrarian) stack sizes.
    """
    ceil_sz, core_sz, cont_sz = sizes
    n_ceil = max(1, round(n_lineups * 0.2))
    n_cont = max(1, round(n_lineups * 0.2))
    stack_list = [t for t, n in alloc.items() for _ in range(n)]
    specs, first_seen = [], set()
    for idx, team in enumerate(stack_list):
        if idx < n_ceil and team not in first_seen:
            tier, size = "CEILING", ceil_sz
        elif idx >= len(stack_list) - n_cont:
            tier, size = "CONTRARIAN", cont_sz
        else:
            tier, size = "CORE", core_sz
        first_seen.add(team)
        specs.append({"stack": team, "tier": tier, "size": size})
    return specs


# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────

def stack_windows(hit_pool, team, size):
    tp = [h for h in hit_pool if h["team"] == team]
    by_bo = {h["bo"]: h for h in tp}
    wins = []
    for start in range(1, 10):
        win = [by_bo.get(((start - 1 + k) % 9) + 1) for k in range(size)]
        win = [w for w in win if w]
        if len(win) == size:
            wins.append(win)
    wins.sort(key=lambda w: -sum(x["bs"] for x in w))
    fb = sorted(tp, key=lambda x: -x["bs"])[:size]
    if len(fb) == size:
        wins.append(fb)
    return wins


def assign_slots(players, open_slots):
    placed = {}
    players = sorted(players, key=lambda p: len(p["slots"] & set(open_slots)))
    for p in players:
        avail = [s for s in open_slots if s in p["slots"] and s not in placed]
        if not avail:
            return None
        placed[avail[0]] = p
    return placed


class Builder:
    def __init__(self, sp_df, caps, med, hit_pool, vegas, impl, fades,
                 opp_map, game_map, n_lineups, seed, fade_reserved=None):
        self.sp_df, self.caps, self.med = sp_df, caps, med
        self.hit_pool, self.vegas, self.impl, self.fades = hit_pool, vegas, impl, fades
        self.opp_map, self.game_map = opp_map, game_map
        self.n_lineups, self.seed = n_lineups, seed
        # Appearances a faded team's own primary stacks will consume — fills
        # must leave room for them or the 25% cap breaks (Fix #6/#10).
        self.fade_reserved = fade_reserved or {}
        self.sp_use = defaultdict(int)
        self.pair_use = defaultdict(int)
        self.fade_appear = defaultdict(int)
        self.fill_appear = defaultdict(int)   # Fix #15 — non-stack appearances
        self.cash_use = defaultdict(int)      # hitter appearances across cash set
        self.n_cash, self.n_pairs = 0, 1      # set properly by build_cash
        self.cash_hitter_cap, self.best_pair_blended = 3, 0.0
        # None keeps the historical behaviour; set by a variant or --sp-cap.
        self.sp_salary_cap = None
        self.hitter_min_salary = None
        self.min_total_salary = None
        self.reject = defaultdict(int)        # why attempts were thrown away
        self.seen_sigs = set()
        self.lineups = []

    @staticmethod
    def sig(lu_):
        return tuple(sorted(p["name"] for p in lu_.values()))

    def pick_sp_pair(self, spec, rng):
        stack_t = spec["stack"]
        avoid = {stack_t}
        if spec.get("bringback"):
            avoid.add(self.opp_map[stack_t])
        elig = [s for _, s in self.sp_df.iterrows()
                if self.sp_use[s["name"]] < self.caps[s["name"]] and s["opp"] not in avoid]
        if spec["tier"] == "CONTRARIAN":
            pref = [s for s in elig if 10 <= s["adj_bs"] < 40]
        else:
            pref = [s for s in elig if s["adj_blended"] >= self.med]
        cap = self.sp_salary_cap
        # A salary cap thins the legal pair pool badly: on 08/24, PAIR_CAP=3
        # against the capped pool filled 7 of 20 lineups and silently dropped
        # the rest. Scale per-pair reuse to what the capped pool can actually
        # support -- the same trick cash_sp_pair uses when the cash set
        # outnumbers the legal pairs.
        pair_cap = PAIR_CAP
        if cap:
            legal = sum(1 for i, a in enumerate(elig) for b in elig[i + 1:]
                        if self.game_map[a["team"]] != self.game_map[b["team"]]
                        and a["salary"] + b["salary"] <= cap)
            pair_cap = max(PAIR_CAP, -(-self.n_lineups // max(1, legal)))
        # Pass 1 honours the cap. Pass 2 exists only when a cap is set: rather
        # than drop the lineup, spend over budget by as little as possible,
        # so the cap degrades into a strong preference on thin slates instead
        # of shrinking the portfolio.
        passes = [(cap, False)] if not cap else [(cap, False), (None, True)]
        for pass_cap, cheapest_first in passes:
            for pool in (pref, elig):
                if not pool:
                    continue
                pool = list(pool)
                rng.shuffle(pool)
                if cheapest_first:
                    pool.sort(key=lambda s: s["salary"])
                else:
                    pool.sort(key=lambda s: -(s["adj_blended"] + rng.uniform(0, 5)))
                pairs = self._form_pairs(pool, pair_cap, pass_cap)
                if pairs:
                    return pairs
        return []

    def _form_pairs(self, pool, pair_cap, cap):
        """Up to 6 legal (a, b) SP pairs from `pool`.

        `cap` None means unconstrained, which is the historical behaviour and
        the only path `control` ever takes.
        """
        pairs = []
        for a in pool:
            for b in pool:
                if a["name"] == b["name"]:
                    continue
                if self.game_map[a["team"]] == self.game_map[b["team"]]:
                    continue
                if cap is not None and a["salary"] + b["salary"] > cap:
                    self.reject["SP pair over salary cap"] += 1
                    continue
                key = tuple(sorted((a["name"], b["name"])))
                if self.pair_use[key] >= pair_cap:
                    continue
                if (b["name"], a["name"]) not in [(y["name"], x["name"])
                                                  for x, y in pairs]:
                    pairs.append((a, b))
                if len(pairs) >= 6:
                    return pairs
        return pairs

    def cash_sp_pair(self, rng):
        """Best available pair by summed vegas-adj blended — floor arms first.

        On a thin slate the above-median pool can be too small to form any
        legal pair (8/22: 3 above-median arms, two of them in the same game),
        so fall back to the full pool. The per-pair cap also scales with the
        cash set: 20 cash lineups cannot be spread over 3 pairs at PAIR_CAP=3.
        """
        avail = [s for _, s in self.sp_df.iterrows()
                 if self.caps[s["name"]] > 0
                 and self.sp_use[s["name"]] < self.caps[s["name"]]]
        pair_cap = max(PAIR_CAP, -(-self.n_cash // max(1, self.n_pairs)))
        for elig in ([s for s in avail if s["adj_blended"] >= self.med], avail):
            pairs = []
            for a in elig:
                for b in elig:
                    if (a["name"] >= b["name"]
                            or self.game_map[a["team"]] == self.game_map[b["team"]]):
                        continue
                    key = tuple(sorted((a["name"], b["name"])))
                    if self.pair_use[key] < pair_cap:
                        pairs.append((a, b))
            # Fix #19 — a cash lineup lives on its arms; refuse pairs whose
            # combined projection is far off the slate's best. 8/22: the two
            # lineups on the weakest legal pair scored 33.5 and 36.5.
            pairs = [p for p in pairs
                     if p[0]["adj_blended"] + p[1]["adj_blended"]
                     >= PAIR_FLOOR_PCT * self.best_pair_blended]
            if pairs:
                pairs.sort(key=lambda p: -(p[0]["adj_blended"] + p[1]["adj_blended"]))
                return pairs[: max(1, min(3, len(pairs)))]
        return []

    def try_build_cash(self, rng):
        pairs = self.cash_sp_pair(rng)
        if not pairs:
            return None
        sp1, sp2 = pairs[rng.randrange(0, len(pairs))]
        banned = {sp1["opp"], sp2["opp"]}
        salary = sp1["salary"] + sp2["salary"]

        med_impl = self.impl.median()
        ok_teams = {t for t in self.impl.index
                    if t not in banned and t not in self.fades}
        top_teams = {t for t in ok_teams if self.impl.get(t, 0) >= med_impl}
        if len(top_teams) * CASH_TEAM_CAP < len(HITTER_SLOTS):
            top_teams = ok_teams    # small slate: implied filter too strict
        placed, used = {}, {sp1["name"], sp2["name"]}
        tcount = defaultdict(int)
        open_slots = list(HITTER_SLOTS)
        rng.shuffle(open_slots)
        for slot in open_slots:
            rem = sum(1 for s in HITTER_SLOTS if s not in placed) - 1
            budget = SALARY_CAP - salary - rem * CASH_MIN_SALARY
            cands = [h for h in self.hit_pool
                     if slot in h["slots"] and h["name"] not in used
                     and h["team"] in top_teams
                     and tcount[h["team"]] < CASH_TEAM_CAP
                     and CASH_MIN_SALARY <= h["salary"] <= budget
                     and self.cash_use[h["name"]] < self.cash_hitter_cap]
            if not cands:
                return None
            cands.sort(key=lambda x: -(x["avg26"] + rng.uniform(0, 0.5)))
            placed[slot] = pick = cands[min(rng.randrange(0, 2), len(cands) - 1)]
            salary += pick["salary"]
            used.add(pick["name"])
            tcount[pick["team"]] += 1

        if not (CASH_MIN_SPEND <= salary <= SALARY_CAP):
            return None
        lu_ = {"SP1": sp1, "SP2": sp2, **placed}
        if self.sig(lu_) in self.seen_sigs:
            return None
        if len({self.game_map[p["team"]] for p in lu_.values()}) < 2:
            return None
        return lu_, salary

    def build_cash(self, n_cash):
        self.n_cash = n_cash
        self.cash_hitter_cap = max(2, round(CASH_HITTER_CAP_PCT * n_cash))
        arms = [s for _, s in self.sp_df.iterrows() if self.caps[s["name"]] > 0]
        legal = [(a, b) for i, a in enumerate(arms) for b in arms[i + 1:]
                 if self.game_map[a["team"]] != self.game_map[b["team"]]]
        self.n_pairs = len(legal)
        self.best_pair_blended = max(
            (a["adj_blended"] + b["adj_blended"] for a, b in legal), default=0.0)
        spec = {"stack": "CASH", "tier": "CASH", "size": 0, "bringback": False}
        for i in range(n_cash):
            built = False
            for attempt in range(500):
                rng = stable_rng(self.seed, "CASH", i, attempt)
                res = self.try_build_cash(rng)
                if not res:
                    self.reject["no valid construction"] += 1
                    continue
                lu_, salary = res
                s_new = set(self.sig(lu_))
                if any(len(s_new - set(s0)) < 2 for s0 in self.seen_sigs):
                    self.reject["too similar to an existing lineup"] += 1
                    continue
                floor = (lu_["SP1"]["blended"] + lu_["SP2"]["blended"]
                         + sum(sorted((lu_[s]["avg26"] for s in HITTER_SLOTS),
                                      reverse=True)[:5]) * 2.5)
                self.seen_sigs.add(self.sig(lu_))
                self.sp_use[lu_["SP1"]["name"]] += 1
                self.sp_use[lu_["SP2"]["name"]] += 1
                self.pair_use[tuple(sorted((lu_["SP1"]["name"],
                                            lu_["SP2"]["name"])))] += 1
                for s in HITTER_SLOTS:
                    self.cash_use[lu_[s]["name"]] += 1
                self.lineups.append({"spec": dict(spec), "lineup": lu_,
                                     "salary": salary, "floor": floor})
                built = True
                break
            if not built:
                print(f"  !! could not build unique cash lineup {i + 1} — skipping")
        return self.lineups

    def try_build(self, spec, rng):
        stack_t = spec["stack"]
        spec["bringback"] = bool(
            stack_t in self.vegas.index
            and self.vegas.loc[stack_t, "game_total"] >= BRINGBACK_TOTAL)
        pairs = self.pick_sp_pair(spec, rng)
        if not pairs:
            return None
        # sample among the top valid pairs — keeps blended-first ordering but
        # lets expensive stacks reach cheaper arms and spreads exposure
        sp1, sp2 = pairs[rng.randrange(0, len(pairs))]
        banned = {sp1["opp"], sp2["opp"]}
        if stack_t in banned:
            return None
        salary = sp1["salary"] + sp2["salary"]

        wins = stack_windows(self.hit_pool, stack_t, spec["size"])
        if not wins:
            return None
        picked = list(wins[rng.randrange(0, min(len(wins), 4))])

        fill_cap = round(FILL_CAP * self.n_lineups)

        # Fix #8 — reserve bring-back slot BEFORE fills
        if spec["bringback"]:
            opp_t = self.opp_map[stack_t]
            if opp_t not in banned:
                for bb in sorted((h for h in self.hit_pool if h["team"] == opp_t
                                  and self.fill_appear[h["name"]] < fill_cap),
                                 key=lambda x: -x["bs"])[:4]:
                    if assign_slots(picked + [bb], HITTER_SLOTS):
                        picked.append(bb)
                        break

        placed = assign_slots(picked, HITTER_SLOTS)
        if placed is None:
            return None
        salary += sum(p["salary"] for p in picked)
        used = {p["name"] for p in picked} | {sp1["name"], sp2["name"]}
        tcount = defaultdict(int)
        for p in picked:
            tcount[p["team"]] += 1

        fade_cap = round(FADE_APPEAR_CAP * self.n_lineups)
        local_fade = defaultdict(int)   # count this lineup's picks too, or a
        open_slots = [s for s in HITTER_SLOTS if s not in placed]  # single
        rng.shuffle(open_slots)         # lineup can blow through the cap
        # The fill sort below is bs/salary with the denominator clamped at
        # 2000, so a minimum-priced bat wins on ties by construction. That is
        # where our punts come from: we roster 3.62 sub-5%-owned players per
        # lineup against the field's 2.25. A floor here is the only rule that
        # three separate studies pointed at -- min_hit_sal (+0.135), and
        # own_min (+0.126, p=0.015 over 12 contests and 27k lineups) both say
        # the WEAKEST roster spot decides the lineup. It applies to fills
        # only: a stack is a contiguous batting-order run, so its members
        # cannot be cherry-picked without breaking the correlation it exists
        # for.
        floor = self.hitter_min_salary or 0
        for slot in open_slots:
            rem = sum(1 for s in HITTER_SLOTS if s not in placed) - 1
            # reserve the floor, not 2000, or the last slots price themselves
            # out and the lineup dies late
            budget = SALARY_CAP - salary - rem * max(2000, floor)

            def eligible(min_sal):
                return [h for h in self.hit_pool
                        if slot in h["slots"] and h["name"] not in used
                        and h["team"] not in banned and tcount[h["team"]] < 5
                        and h["salary"] <= budget and h["salary"] >= min_sal
                        and (h["team"] == stack_t                   # Fix #15
                             or self.fill_appear[h["name"]] < fill_cap)
                        and not (h["team"] in self.fades
                                 and self.fade_appear[h["team"]]
                                 + local_fade[h["team"]]
                                 + self.fade_reserved.get(h["team"], 0) >= fade_cap)]

            cands = eligible(floor)
            if not cands and floor:
                # Thin slate or tight budget: take the best available under
                # the floor rather than dropping the lineup. Same degradation
                # the SP caps use -- a floor that silently shrinks the
                # portfolio is worse than one that bends.
                self.reject["no fill hitter at or above floor"] += 1
                cands = eligible(0)
            if not cands:
                return None
            cands.sort(key=lambda x: -(x["bs"] / max(x["salary"], 2000) * 1000
                                       + rng.uniform(0, 2)))
            pick = cands[min(rng.randrange(0, 3), len(cands) - 1)]
            placed[slot] = pick
            salary += pick["salary"]
            used.add(pick["name"])
            tcount[pick["team"]] += 1
            if pick["team"] in self.fades:
                local_fade[pick["team"]] += 1

        # Minimum spend. Checked last, on the finished lineup, because the
        # fill loop sorts by bs/salary and only the completed roster tells you
        # what was actually spent. 99.5% of the field is already above 45,000
        # (143 of 29,137 priced lineups sit below), so this trims our punt
        # builds rather than reshaping the portfolio -- it would have rejected
        # 47 of our 212 entries. Sub-45k lineups took top-10% at 5.59% against
        # the field's 10.44%, and produced no top-1% finish at all, though at
        # n=143 that last figure is within noise.
        if self.min_total_salary and salary < self.min_total_salary:
            self.reject["under minimum total salary"] += 1
            return None
        if salary > SALARY_CAP:
            return None
        lu_ = {"SP1": sp1, "SP2": sp2, **placed}
        if self.sig(lu_) in self.seen_sigs:
            return None
        if len({self.game_map[p["team"]] for p in lu_.values()}) < 2:
            return None
        return lu_, salary

    def build_all(self, specs, n_candidates=1, score=None, top=1):
        r"""Fill each spec with a lineup.

        With n_candidates=1 (the default) this accepts the FIRST valid
        construction, exactly as it always has -- there is no selection step
        anywhere in the builder, which is why `floor` correlated with nothing:
        it is computed and reported, never used to choose between lineups.
        It no longer gates anything either -- see the MIN_FLOOR note up top.

        With n_candidates>1 and a score function, collect that many valid
        lineups for the spec and keep the best. Same stacks, same caps, same
        constraints -- the only difference is choosing among valid lineups
        instead of taking whichever the RNG produced first.
        """
        for spec in specs:
            cands = []
            # A minimum spend that blocks every attempt must bend, or the
            # portfolio silently shrinks -- the failure the SP cap taught us
            # to avoid. Bend it in 1,000 steps rather than dropping it: an
            # all-or-nothing fallback made minspend47 build a 36,600 lineup on
            # 08/23, below what control managed unconstrained, because the
            # relaxed pass had no floor at all.
            base = self.min_total_salary
            ladder = ([None] if not base
                      else [base, base - 1000, base - 2000, None])
            for step, level in enumerate(ladder):
                if step and cands:
                    break
                self.min_total_salary = level
                for attempt in range(500):
                    rng = stable_rng(self.seed, spec["stack"], spec["tier"],
                                     attempt)
                    res = self.try_build(spec, rng)
                    if not res:
                        self.reject["no valid construction"] += 1
                        continue
                    lu_, salary = res
                    s_new = set(self.sig(lu_))
                    if any(len(s_new - set(s0)) < 2 for s0 in self.seen_sigs):
                        self.reject["too similar to an existing lineup"] += 1
                        continue
                    floor = (lu_["SP1"]["blended"] + lu_["SP2"]["blended"]
                             + sum(sorted((lu_[s]["avg26"] for s in HITTER_SLOTS),
                                          reverse=True)[:5]) * 2.5)
                    cands.append((lu_, salary, floor))
                    if len(cands) >= n_candidates:
                        break
                if step and cands:
                    self.reject["min spend relaxed to %s" % (level or "none")] += 1
            self.min_total_salary = base
            if not cands:
                print(f"  !! no unique valid lineup for {spec['stack']} "
                      f"({spec['tier']}) — skipping, never duplicating")
                continue
            if score is not None and len(cands) > 1:
                cands.sort(key=lambda c: -score(c[0]))
                if top > 1:
                    # Sample the best few rather than always taking #1.
                    # Deterministic in the seed, so a rerun reproduces exactly.
                    pool = cands[:min(top, len(cands))]
                    pick = stable_rng(self.seed, spec["stack"], spec["tier"],
                                      "select").randrange(len(pool))
                    cands = [pool[pick]] + [c for i, c in enumerate(pool)
                                            if i != pick]
            lu_, salary, floor = cands[0]
            self.seen_sigs.add(self.sig(lu_))
            self.sp_use[lu_["SP1"]["name"]] += 1
            self.sp_use[lu_["SP2"]["name"]] += 1
            self.pair_use[tuple(sorted((lu_["SP1"]["name"], lu_["SP2"]["name"])))] += 1
            for s in HITTER_SLOTS:
                if (lu_[s]["team"] in self.fades
                        and lu_[s]["team"] != spec["stack"]):
                    self.fade_appear[lu_[s]["team"]] += 1
                if lu_[s]["team"] != spec["stack"]:                  # Fix #15
                    self.fill_appear[lu_[s]["name"]] += 1
            self.lineups.append({"spec": spec, "lineup": lu_,
                                 "salary": salary, "floor": floor})
        return self.lineups


# ─────────────────────────────────────────────────────────────────────────────
# Audit — the 7 hard checks; nothing is written unless every lineup passes
# ─────────────────────────────────────────────────────────────────────────────

def audit(lineups, game_map):
    failures = 0
    for i, L in enumerate(lineups):
        lu_ = L["lineup"]
        errs = []
        names = [lu_[s]["name"] for s in ALL_SLOTS]
        if len(set(names)) != 10:
            errs.append("duplicate player")
        if L["salary"] > SALARY_CAP:
            errs.append(f"salary {L['salary']}")
        for s in HITTER_SLOTS:
            if s not in lu_[s]["slots"]:
                errs.append(f"{lu_[s]['name']} ineligible for {s}")
        tc = defaultdict(int)
        for s in HITTER_SLOTS:
            tc[lu_[s]["team"]] += 1
        if tc and max(tc.values()) > 5:
            errs.append(">5 hitters from one team")
        for sp in ("SP1", "SP2"):
            for s in HITTER_SLOTS:
                if lu_[s]["team"] == lu_[sp]["opp"]:
                    errs.append(f"{lu_[s]['name']} opposes {lu_[sp]['name']}")
        if len({game_map[lu_[s]["team"]] for s in ALL_SLOTS}) < 2:
            errs.append("single game")
        if errs:
            failures += 1
            print(f"  AUDIT FAIL lineup {i + 1}: {errs}")
    sigs = [Builder.sig(L["lineup"]) for L in lineups]
    if len(set(sigs)) != len(sigs):
        failures += 1
        print("  AUDIT FAIL: duplicate lineups in portfolio")
    return failures


def selftest():
    """Audit-logic must catch each violation type."""
    gm = {"AAA": "AAA@BBB", "BBB": "AAA@BBB", "CCC": "CCC@DDD", "DDD": "CCC@DDD"}
    def player(name, team, slots, opp):
        return {"name": name, "team": team, "slots": slots, "opp": opp, "salary": 0}
    good = {"SP1": player("p1", "AAA", set(), "BBB"),
            "SP2": player("p2", "CCC", set(), "DDD")}
    for i, s in enumerate(HITTER_SLOTS):
        good[s] = player(f"h{i}", "AAA" if i < 5 else "CCC", {s}, "x")
    assert audit([{"lineup": good, "salary": 49000}], gm) == 0
    bad = dict(good)
    bad["C"] = player("h9", "BBB", {"C"}, "AAA")     # opposes SP1
    assert audit([{"lineup": bad, "salary": 49000}], gm) == 1
    bad2 = dict(good)
    bad2["C"] = player("h0", "AAA", {"1B"}, "x")     # dup name + wrong slot
    assert audit([{"lineup": bad2, "salary": 51000}], gm) == 1
    print("SELFTEST PASSED")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Build DK MLB Classic GPP portfolio.")
    ap.add_argument("--lineups", type=int, default=20,
                    help="total lineups, cash included")
    ap.add_argument("--cash", type=int, default=None,
                    help="floor-maximized lineups (default: 0 on a normal "
                         "slate, ALL on a <=4-game slate)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--variant", default=None,
                    choices=["control"] + sorted(VARIANTS),
                    help="A/B arm. Omit for today's behaviour and today's "
                         "filenames; 'control' is identical but writes "
                         "_control-suffixed files for a paired experiment.")
    ap.add_argument("--candidates", type=int, default=20,
                    help="valid lineups to generate per slot before picking "
                         "the best (ignored by control; default 20)")
    ap.add_argument("--export", default=EXPORT_DIR)
    ap.add_argument("--stack-sizes", default="5,4,3",
                    help="ceiling,core,contrarian stack sizes (default 5,4,3)")
    ap.add_argument("--max-stacks", type=int, default=MAX_STACKS_PER_TEAM,
                    help=f"primary stacks per team (default {MAX_STACKS_PER_TEAM})")
    ap.add_argument("--min-total-salary", type=int, default=None,
                    help="reject lineups spending less than this in total "
                         "(e.g. 45000). Overrides the variant's own; "
                         "0 disables. Bends on a slate that cannot meet it")
    ap.add_argument("--hitter-min-salary", type=int, default=None,
                    help="floor the salary of every NON-STACK hitter (e.g. "
                         "3000). Overrides the variant's own; 0 disables")
    ap.add_argument("--sp-cap", type=int, default=None,
                    help="cap the two SPs' combined salary (e.g. 16000). "
                         "Overrides the variant's own cap; 0 disables it. "
                         "Omit for the historical unconstrained behaviour")
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return

    dk, lu, vegas, padj, hcache, opp_map, game_map, slate_date = load_data(args.export)
    n_games = dk["Game Info"].nunique()
    print(f"Slate {slate_date}: {n_games} games, {len(dk)} DK players")

    # Fix #18 — on a thin slate the GPP tier ladder has nothing to
    # differentiate into. 8/22 (3 games): cash lineups averaged 89.4 and took
    # both cashes; ceiling/core/contrarian averaged 69.7. Build all-cash.
    if args.cash is None:
        if n_games <= SMALL_SLATE_GAMES:
            args.cash = args.lineups
            print(f"  small slate ({n_games} games <= {SMALL_SLATE_GAMES}): "
                  f"building the whole portfolio cash-style (Fix #18)")
        else:
            args.cash = 0
            print(f"  {n_games} games: full GPP build, no cash lineups "
                  f"(Fix #20 — pass --cash N to build them anyway)")

    sp_df, caps, med, feasible = build_sp_pool(dk, lu, padj, opp_map, args.lineups)
    if feasible < args.lineups:
        # Recompute the caps against the reduced count once so the ceiling is
        # honest for the portfolio we actually build. One pass only — a very
        # thin arm pool would otherwise ratchet the count down to nothing.
        args.lineups = max(1, feasible)
        args.cash = min(args.cash, args.lineups)
        sp_df, caps, med, _ = build_sp_pool(dk, lu, padj, opp_map, args.lineups)
    print(f"\nSP pool ({len(sp_df)}), tiered by vegas-adj blended (median {med:.2f}):")
    print(sp_df[["name", "team", "opp", "salary", "adj_bs", "adj_blended", "cap"]]
          .to_string(index=False))

    hit_pool = build_hitter_pool(dk, lu, hcache, opp_map)
    print(f"\nHitter pool: {len(hit_pool)} confirmed batters")

    n_gpp = args.lineups - args.cash
    if n_gpp < 0:
        sys.exit("ERROR: --cash cannot exceed --lineups")
    alloc, impl, fades = allocate_stacks(hit_pool, sp_df, vegas, n_gpp, opp_map,
                                         max_stacks=args.max_stacks)
    print(f"\nHard fades (≤25% appearances): {sorted(fades)}")
    print(f"Primary-stack allocation ({n_gpp} GPP lineups):")
    for t, n in alloc.items():
        print(f"  {t}: {n}  (implied {impl.get(t, float('nan')):.2f})")

    sizes = tuple(int(x) for x in args.stack_sizes.split(","))
    if len(sizes) != 3:
        sys.exit("--stack-sizes needs three numbers: ceiling,core,contrarian")
    specs = make_specs(alloc, n_gpp, sizes=sizes)
    fade_reserved = defaultdict(int)
    for spec in specs:
        if spec["stack"] in fades:
            fade_reserved[spec["stack"]] += spec["size"]
    b = Builder(sp_df, caps, med, hit_pool, vegas, impl, fades,
                opp_map, game_map, args.lineups, args.seed,
                fade_reserved=fade_reserved)
    if args.cash:
        print(f"\nBuilding {args.cash} cash lineups (floor-first)...")
        b.build_cash(args.cash)
    # A/B arms: 'control' is the shipping builder, byte-for-byte. Any other
    # variant adds the selection step the builder has never had, so the two
    # can be entered in the SAME contest -- which controls for slate
    # difficulty, the thing that made sequential day-to-day comparison
    # useless (floor read +0.55 one day and -0.72 the next).
    cfg = VARIANTS[args.variant] if args.variant not in (None, "control") else {}
    # --sp-cap wins over the variant's own so one arm can be swept across
    # levels without editing VARIANTS; 0 means "explicitly uncapped".
    cap = cfg.get("sp_cap") if args.sp_cap is None else (args.sp_cap or None)
    b.sp_salary_cap = cap
    hfloor = (cfg.get("hitter_min_salary") if args.hitter_min_salary is None
              else (args.hitter_min_salary or None))
    b.hitter_min_salary = hfloor
    mspend = (cfg.get("min_total_salary") if args.min_total_salary is None
              else (args.min_total_salary or None))
    b.min_total_salary = mspend
    if mspend:
        print(f"\nminimum total salary {mspend:,}")
    if hfloor:
        print(f"\nnon-stack hitters floored at {hfloor:,}")
    if cap:
        # The tier caps key off adj_blended, so the below-median arms a salary
        # cap forces us onto are limited to BOTTOM2/BELOW_MEDIAN (15-20%) and
        # run dry mid-build -- 08/23 then fell back over the cap on 10 of 20
        # lineups, leaving the arm barely distinguishable from control. Let
        # every usable arm run to the same ABSOLUTE_SP_CAP ceiling the rest of
        # the builder already respects (0 stays 0: HARD_AVOID_BS still bans).
        # This widens WHO may be used; the salary filter still decides who is.
        ceiling = max(1, round(ABSOLUTE_SP_CAP * args.lineups))
        for name in list(caps):
            if caps[name] > 0:
                caps[name] = ceiling
        print(f"\nSP pair salary capped at {cap:,}; per-arm exposure "
              f"levelled to {ceiling}/{args.lineups}")
    if args.variant in (None, "control"):
        lineups = b.build_all(specs)
    elif cfg["score"] is None:
        # Cap-only arm: control's own selection, so the cap is the sole
        # difference. Going through build_all(specs) keeps that exact.
        print(f"\nvariant '{args.variant}': control selection, salary cap only")
        lineups = b.build_all(specs)
    else:
        how = ("best" if cfg["top"] == 1
               else f"a random one of the top {cfg['top']}")
        print(f"\nvariant '{args.variant}': choosing {how} of "
              f"{args.candidates} valid candidates per lineup")
        lineups = b.build_all(specs, n_candidates=args.candidates,
                              score=cfg["score"], top=cfg["top"])

    # Cash lineups go out best-armed first: SP pair blended is the strongest
    # single predictor we have (Fix #13), so "enter the top N" is meaningful
    # when the slate supports fewer lineups than requested.
    cash_l = [L for L in lineups if L["spec"]["tier"] == "CASH"]
    cash_l.sort(key=lambda L: -(L["lineup"]["SP1"]["adj_blended"]
                                + L["lineup"]["SP2"]["adj_blended"]))
    lineups = cash_l + [L for L in lineups if L["spec"]["tier"] != "CASH"]

    if b.reject:
        print("\nrejected attempts by reason:")
        for why, n in sorted(b.reject.items(), key=lambda x: -x[1]):
            print(f"  {n:>7,}  {why}")

    print(f"\nBuilt {len(lineups)} lineups; auditing...")
    if audit(lineups, game_map):
        sys.exit("AUDIT FAILURES — nothing written.")
    print("ALL AUDITS PASSED")

    rows, srows = [], []
    for i, L in enumerate(lineups):
        lu_ = L["lineup"]
        rows.append([lu_["SP1"]["id"], lu_["SP2"]["id"]]
                    + [lu_[s]["id"] for s in HITTER_SLOTS])
        tc = defaultdict(int)
        for s in HITTER_SLOTS:
            tc[lu_[s]["team"]] += 1
        # CANDIDATE RANKING METRICS — recorded, NOT used to build anything.
        #
        # Measured over 5 slates (78 lineups joined by lineup_id), floor_target
        # showed no consistent relationship with realised scores: +0.63 and
        # +0.67 on 08/21-08/22, 0.00 on 08/23, -0.66 and -0.75 on 08/24, and
        # +0.015 pooled on within-slate ranks. It spreads only ~2.6x narrower
        # than actual scores do, so it cannot separate lineups that finish 90
        # points apart. Rather than refit the objective against five slates of
        # noise, record the alternatives on every build and let the evidence
        # accumulate; metric_study.py scores them once there is enough of it.
        hit = [lu_[s] for s in HITTER_SLOTS]
        sps = [lu_["SP1"], lu_["SP2"]]
        srows.append({"#": i + 1,
                      "lineup_id": lineup_id([lu_[s]["name"] for s in ALL_SLOTS]),
                      "variant": args.variant or "control",
                      "tier": L["spec"]["tier"], "stack": L["spec"]["stack"],
                      "SP1": lu_["SP1"]["name"], "SP2": lu_["SP2"]["name"],
                      "teams": " ".join(f"{t}x{n}" for t, n in
                                        sorted(tc.items(), key=lambda x: -x[1])),
                      "salary": L["salary"],
                      # renamed from "floor": it is a construction target, not
                      # a points projection, and reading it as one is why a
                      # 142 was ever expected to beat a 124.
                      "floor_target": round(L["floor"], 1),
                      # straight expected points, all 8 bats (floor_target uses
                      # only the top 5, and weights them 2.5x)
                      "proj_points": round(sum(h["avg26"] for h in hit)
                                           + sum(p["adj_blended"] for p in sps), 1),
                      # upside rather than downside: breakout scores
                      "ceiling": round(sum(h["bs"] for h in hit)
                                       + sum(p["adj_bs"] for p in sps), 1),
                      # tests the "stop buying arms, spend on bats" hypothesis
                      "hitter_salary": sum(h["salary"] for h in hit),
                      # correlation proxy — bigger stacks swing harder
                      "max_stack": max(tc.values()) if tc else 0})
    # Omitting --variant keeps today's filenames untouched; naming an arm
    # suffixes them so both can sit side by side for the same slate.
    tagged = f"{slate_date}_{args.variant}" if args.variant else slate_date
    cols = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
    up = pd.DataFrame(rows)
    up.columns = cols
    up_path = f"{args.export}\\DK_upload_{tagged}.csv"
    archive_if_different(up_path, up)
    up.to_csv(up_path, index=False)
    summary = pd.DataFrame(srows)
    sum_path = f"{args.export}\\portfolio_summary_{tagged}.csv"
    archive_if_different(sum_path, summary)
    summary.to_csv(sum_path, index=False)

    cash_idx = [i for i, L in enumerate(lineups) if L["spec"]["tier"] == "CASH"]
    if cash_idx:
        cash_up = pd.DataFrame([rows[i] for i in cash_idx])
        cash_up.columns = cols
        cash_path = f"{args.export}\\DK_upload_cash_{tagged}.csv"
        cash_up.to_csv(cash_path, index=False)

    # Printed view stays readable; the CSV carries the candidate metrics too.
    shown = ["#", "tier", "stack", "SP1", "SP2", "teams", "salary", "floor_target"]
    print(f"\n{summary[shown].to_string(index=False)}")
    print(f"  (+ proj_points, ceiling, hitter_salary, max_stack recorded in "
          f"{os.path.basename(sum_path)} for metric_study.py)")
    print("\nSP exposure:")
    for n, c in sorted(b.sp_use.items(), key=lambda x: -x[1]):
        print(f"  {n}: {c}/{caps[n]}")
    print(f"\nwrote {up_path}")
    print(f"wrote {sum_path}")
    if cash_idx:
        print(f"wrote {cash_path}  ({len(cash_idx)} single-entry cash lineups)")

    if not args.no_snapshot:
        snap = snapshot(args.export, slate_date, [up_path, sum_path]
                        + ([cash_path] if cash_idx else []))
        print(f"wrote snapshot -> {snap}")


def archive_if_different(path, new_df):
    r"""Preserve an existing same-date file before overwriting it.

    Both output names key on slate_date alone, so a SECOND slate on the same
    calendar day (an early card plus a late card) silently destroyed the first
    one's record -- including lineups already entered. Rerunning the same slate
    is common and harmless, so only archive when the content actually differs.
    """
    if not os.path.exists(path):
        return None
    try:
        old = pd.read_csv(path)
        new = new_df.reset_index(drop=True)
        # Compare VALUES, not frames: the upload file has duplicate column
        # names (P, P, OF, OF, OF) which read_csv mangles to P.1/OF.1, so
        # .equals() would report a difference on every single rerun.
        if (old.shape == new.shape
                and (old.astype(str).values == new.astype(str).values).all()):
            return None                      # same slate rebuilt, nothing lost
    except Exception:
        pass                                 # unreadable -> archive to be safe
    stem, ext = os.path.splitext(path)
    stamp = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%H%M")
    dest = f"{stem}_prev{stamp}{ext}"
    n = 2
    while os.path.exists(dest):
        dest = f"{stem}_prev{stamp}_{n}{ext}"
        n += 1
    shutil.move(path, dest)
    print(f"  NOTE: existing {os.path.basename(path)} was for a different "
          f"slate -- archived to {os.path.basename(dest)}")
    return dest


def snapshot(export, slate_date, built_files):
    r"""Freeze this slate's inputs + outputs so it can be calibrated later.

    The caches are overwritten every morning at 6 AM, so without this there is
    no record of what the model believed BEFORE a slate — every post-hoc
    accuracy test is either contaminated or impossible. Cheap insurance:
    a few hundred KB per slate.
    """
    dest = os.path.join(SNAPSHOT_DIR, slate_date)
    # A second slate on the same date would overwrite the first slate's frozen
    # inputs, which is exactly the record this function exists to protect.
    # Test the SLATE, not the filenames: two A/B arms write different upload
    # names for the same slate and must share one snapshot, while a genuinely
    # new slate brings a different Filtered_DKSalaries.csv.
    same_slate = False
    prior_dk = os.path.join(dest, "Filtered_DKSalaries.csv")
    if os.path.exists(prior_dk):
        try:
            same_slate = (pd.read_csv(prior_dk)["Name + ID"].tolist()
                          == pd.read_csv(os.path.join(export,
                                                      "Filtered_DKSalaries.csv"))
                          ["Name + ID"].tolist())
        except Exception:
            same_slate = False
    if os.path.isdir(dest) and not same_slate:
        stamp = datetime.fromtimestamp(os.path.getmtime(dest)).strftime("%H%M")
        moved = f"{dest}_prev{stamp}"
        n = 2
        while os.path.exists(moved):
            moved = f"{dest}_prev{stamp}_{n}"
            n += 1
        shutil.move(dest, moved)
        print(f"  NOTE: snapshot for {slate_date} was a different slate -- "
              f"archived to {os.path.basename(moved)}")
    os.makedirs(dest, exist_ok=True)
    inputs = ["hitter_bs_cache.csv", "pitcher_bs_cache.csv",
              "pitcher_bs_cache_adj.csv", "vegas.csv", "mlb_odds.csv",
              "Filtered_DKSalaries.csv", "Filtered_Lineups.csv"]
    copied, missing = 0, []
    for f in inputs:
        src = os.path.join(export, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, f))
            copied += 1
        else:
            missing.append(f)
    for f in built_files:
        if os.path.exists(f):
            shutil.copy2(f, os.path.join(dest, os.path.basename(f)))
            copied += 1
    if missing:
        print(f"  snapshot: {len(missing)} input(s) not found: {', '.join(missing)}")
    return f"{dest}  ({copied} files)"


if __name__ == "__main__":
    main()
