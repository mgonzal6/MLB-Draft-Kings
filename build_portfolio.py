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

import slate_io

from lineup_id import lineup_id

EXPORT_DIR = r"G:\My Drive\DK\export"
SNAPSHOT_DIR = r"G:\My Drive\DK\Snapshots"
ABBR_REMAP = {"OAK": "ATH", "WAS": "WSH", "CHW": "CWS"}
SALARY_CAP = 50000
HITTER_SLOTS = ["C", "1B", "2B", "3B", "SS", "OF1", "OF2", "OF3"]
ALL_SLOTS = ["SP1", "SP2"] + HITTER_SLOTS
# Lineups-feed spelling -> DK spelling. Applied to BOTH sides, so an entry can
# only do harm if two DIFFERENT players on one slate collapse onto the same
# name, which no pair here does.
NICKS = {"michael": "mike", "leonardo": "leo", "matthew": "matt",
         "christopher": "chris", "enrique": "kike"}

# Exposure knobs (fractions of the portfolio)
ANCHOR_CAP, SOLID_CAP, FLIER_CAP = 0.40, 0.30, 0.15
BELOW_MEDIAN_CAP, BOTTOM2_CAP = 0.20, 0.15
ABSOLUTE_SP_CAP = 0.40      # Fix #17 — no arm ever exceeds this, scaling included
FADE_APPEAR_CAP = 0.25
FILL_CAP = 0.20             # Fix #15 — non-stack hitter appearance cap
MAX_STACKS_PER_TEAM = 3     # see Fix #21 — 2 was tested and did NOT help
HARD_AVOID_BS = 10          # SP adj_bs below this -> zero exposure
# DK's Roster Position for a pitcher is usually "P", but NOT always: on
# 09/07 Jonah Tong (NYM) came through as "SP" and Derek Law (ARI) as "RP".
# Both were confirmed starters in the lineups feed -- preflight counted 12 of
# 12 -- and both were then dropped by an exact `== "P"` match, so the SP pool
# held 10 arms for 12 teams. Neither NYM nor ARI could be stacked against,
# and ARI had the slate's HIGHEST implied total (5.25). Same class of bug as
# the nickname-map miss recorded below: the pitcher is present in every input
# and silently vanishes on a string comparison.
PITCHER_POS = {"P", "SP", "RP"}
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
    # ---- the same idea at the level the winners actually sit at ----
    # 09/03, 4 contests: the field's top-10 lineups averaged 49,715 of the
    # 50,000 cap, 15 of 40 were exactly maxed, and the CHEAPEST top-10 lineup
    # on the slate (48,000) still beat our MEDIAN lineup (46,700). 47,000 was
    # never near where the winners live, so the floor is retried at 49,000.
    #
    # The floor is HARD here. minspend47 laddered down 1,000 at a time, so its
    # portfolios contained lineups below their own floor and the arm was never
    # really tested at its nominal level. This one underfills instead, and the
    # missing lineups are refilled by rebuilding under further seeds.
    #
    # Countervailing evidence, recorded before the test rather than after:
    # within our own 76 lineups on 09/03 salary correlated -0.299 with points
    # (negative in all 4 contests), the cheap half outscored the expensive half
    # 89.49 to 79.27, and the 47,000 cut deleted 08/30's best lineup, which
    # cost 46,500. If this arm loses, that is why.
    #
    # SHIPPED 09/04 as the default arm. Final-standings replay, 17 snapshots
    # x 5 seeds at equal portfolio size: better on 10 of 11 distinct slate
    # dates, worse on 1 by 0.27, mean dBest +3.08, SE 0.87, t 3.53. sd rose
    # 26.4 -> 27.2 (it does not compress spread), dMean +1.5, dN 0.0.
    #
    # "seeds" is the refill default: a hard floor underfills (45 of 60 on
    # 09/03) and without it the shipped build would quietly come up short.
    "minspend49": {"score": None, "top": 1, "min_total_salary": 49000,
                   "hard_min_salary": True, "seeds": 8},
    # The shipped arm PLUS the thin-slate coverage guarantee. Identical to
    # minspend49 on any card over 4 games -- the guarantee simply does not
    # fire -- so this is a strict superset, not a different builder.
    "minspend49cov": {"score": None, "top": 1, "min_total_salary": 49000,
                      "hard_min_salary": True, "seeds": 8,
                      "all_team_five": True},
    # Sweep levels around the shipped 49,000. The winners sit at 49,715, so
    # "closer to where they sit" is the obvious next question -- but a floor
    # this tight starves construction, and the refill can only recover what
    # the pool can actually build.
    # ---- CEILING tagged by count, not list position ----------------------
    # The tier configured for ~20% of the portfolio delivers 5.2% (4.1% over
    # 919 historical entries), because `idx < n_ceil` runs against a BLOCK
    # ordered spec list -- the first n_ceil positions are two or three teams
    # repeated and only their first spec qualifies. Counting qualifying specs
    # instead delivers 20.8%, and because 5-stacks live only in CEILING under
    # sizes=(5,4,3), the 5-stack share doubles 18.2% -> 36.4%.
    "tiercount": {"score": None, "top": 1, "min_total_salary": 49000,
                  "hard_min_salary": True, "seeds": 8,
                  "all_team_five": True, "tier_by_count": True},
    # ---- guarantee every team a 5-stack ----------------------------------
    # User's design, 09/07, after ATH was hard-faded for facing Cease and then
    # scored 4 runs off him while we held zero ATH exposure across 36 lineups.
    # Coverage first, weighting second: every team gets one 5-stack, then the
    # normal allocation fills the rest.
    "allteam5": {"score": None, "top": 1, "min_total_salary": 49000,
                 "hard_min_salary": True, "seeds": 8, "all_team_five": True},
    # Same, with the SP-based fade disabled entirely, to separate "guarantee
    # coverage" from "stop fading" -- they are different changes and the
    # guarantee already overrides the fade for its own specs.
    "allteam5nofade": {"score": None, "top": 1, "min_total_salary": 49000,
                       "hard_min_salary": True, "seeds": 8,
                       "all_team_five": True, "fade_sp_bs": 999},
    # ilv confined to the cards where the build actually underfills. On any
    # card over 4 games this is byte-identical to minspend49cov, so it is a
    # strict superset in the same way minspend49cov is of minspend49.
    "ilvthin": {"score": None, "top": 1, "min_total_salary": 49000,
                "hard_min_salary": True, "seeds": 8, "all_team_five": True,
                "interleave_attempts": True, "interleave_max_games": 4},
    # ---- same-game SP pair, 09/10 ----------------------------------------
    # See the comment in Builder.__init__. The hitter-side opposing-SP ban is
    # untouched, so a same-game pair still bans BOTH those teams' bats -- the
    # lineup has to stack somewhere else entirely, exactly as 09/10's winner
    # did (HOU x5 behind Gilbert + deGrom).
    "samegame": {"score": None, "top": 1, "min_total_salary": 49000,
                 "hard_min_salary": True, "seeds": 8, "all_team_five": True,
                 "allow_same_game_sp": True},
    # ---- secondary stack, RETESTED 09/09 ---------------------------------
    # sec3 pairs every primary with a 3-man run from a second team: CEILING
    # becomes 5+3 (all eight hitter slots), CORE 4+3, CONTRARIAN 3+3. That is
    # the exact shape of 09/09's three winning lineups.
    "sec3": {"score": None, "top": 1, "min_total_salary": 49000,
             "hard_min_salary": True, "seeds": 8, "all_team_five": True,
             "secondary_stack": 3},
    # sec3 confined to the cards where it measured positive. Byte-identical
    # to minspend49cov above 7 games, so a strict superset there.
    "secthin": {"score": None, "top": 1, "min_total_salary": 49000,
                "hard_min_salary": True, "seeds": 8, "all_team_five": True,
                "secondary_stack": 3, "secondary_max_games": 7},
    "sec2": {"score": None, "top": 1, "min_total_salary": 49000,
             "hard_min_salary": True, "seeds": 8, "all_team_five": True,
             "secondary_stack": 2},
    # ---- attempt order separated from tier assignment, 09/09 -------------
    # See the comment in make_specs. Block order does two jobs; this changes
    # only the second. Team counts identical to minspend49cov, so any
    # difference is purely WHICH specs survive when the builder exhausts.
    "ilv": {"score": None, "top": 1, "min_total_salary": 49000,
            "hard_min_salary": True, "seeds": 8, "all_team_five": True,
            "interleave_attempts": True},
    # ---- per-team 5 AND 4 stack ladder, 09/09 -----------------------------
    # User's design. The guarantee only ever emitted ONE spec per team, always
    # size 5, so a team's only 4-stack could come from the normal allocation
    # -- which is BLOCK ordered, so on a slate that underfills only the first
    # team's block is ever attempted. 09/09 (4 games) delivered 15 of 90 with
    # a FLAT allocation (SD 12, WSH 12, STL 12, TOR 12, ATH 11, SEA 11) and SD
    # took 14 of the stacks purely for sorting first. A ladder asks for each
    # team's 4-stack explicitly instead of hoping the walk reaches it.
    "team54": {"score": None, "top": 1, "min_total_salary": 49000,
               "hard_min_salary": True, "seeds": 8, "all_team_five": True,
               "all_team_sizes": "5,4"},
    # ---- FILL FLOOR BY TIER, 09/09 ---------------------------------------
    # User's idea: the SP half of a spec reads `tier` (CONTRARIAN takes a
    # different adj_bs band) but the HITTER half never does -- stack windows
    # sort by -sum(bs) and fills sort by bs/salary in every lineup, whatever
    # the tier. So there is no coupling between a high-ceiling arm and the
    # bats beside it. A flat fill floor was tested twice and lost (3000
    # -10.03, 3500 -18.54), but flat also strips the CONTRARIAN tier of the
    # punts it exists for. These arms floor the solid tiers and leave the tail
    # alone. NOTE fillfloorceil touches only ~5% of the portfolio, because
    # CEILING under the shipped positional tier test delivers 5.2%.
    "fillfloor30": {"score": None, "top": 1, "min_total_salary": 49000,
                    "hard_min_salary": True, "seeds": 8, "all_team_five": True,
                    "fill_floor": 3000, "fill_floor_exempt": "CONTRARIAN"},
    "fillfloor35": {"score": None, "top": 1, "min_total_salary": 49000,
                    "hard_min_salary": True, "seeds": 8, "all_team_five": True,
                    "fill_floor": 3500, "fill_floor_exempt": "CONTRARIAN"},
    "fillfloorceil": {"score": None, "top": 1, "min_total_salary": 49000,
                      "hard_min_salary": True, "seeds": 8, "all_team_five": True,
                      "fill_floor": 3000,
                      "fill_floor_exempt": "CORE,CONTRARIAN"},
    # ---- ISOLATE THE SALARY FLOOR, 09/09 ---------------------------------
    # 09/08's three biggest contests were won at 46,700 / 46,700 / 44,700 --
    # ALL BELOW the 49,000 hard floor, so minspend49 could not have built any
    # of them. TEX was implied 3.25, so DK priced their bats cheap (Langford
    # 4,400 / Seager 4,200 / Lopez 2,200) and then they erupted. That is the
    # second time the floor has been implicated in deleting the day's best
    # construction (08/30's best lineup cost 46,500).
    #
    # The floor cannot be scored against `control`, which also lacks the
    # seeds:8 refill and the coverage guarantee -- that comparison credits
    # the floor with extra draws. covnofloor is minspend49cov with the floor
    # and ONLY the floor removed, so dBest is the floor's own effect.
    "covnofloor": {"score": None, "top": 1, "seeds": 8,
                   "all_team_five": True},
    "minspend47cov": {"score": None, "top": 1, "min_total_salary": 47000,
                      "hard_min_salary": True, "seeds": 8,
                      "all_team_five": True},
    "minspend485cov": {"score": None, "top": 1, "min_total_salary": 48500,
                       "hard_min_salary": True, "seeds": 8,
                       "all_team_five": True},
    # ---- DEEP-slate coverage floor, built 09/08 --------------------------
    # 09/08 dropped TOR and TEX from a 10-game card entirely (their lineups
    # had not posted) and TEX then scored 9 runs by the 6th. 09/07 dropped
    # ATH by fade and ATH erupted. Two days, two unreachable teams, both of
    # which went off -- and neither was predictable: TEX was #16 of 20 by
    # implied total, ATH #12, and implied_total correlates only +0.130 with
    # team output within-slate.
    #
    # The 5-stack guarantee already fixes this on <=4-game cards (+8.42) and
    # LOSES on deep ones (-2.81), because covering 18 teams with 5-stacks
    # spends 18 lineups on the worst-rated offences. These arms buy the same
    # coverage at 2, 3 or 4 roster spots instead of 5.
    "cover2": {"score": None, "top": 1, "min_total_salary": 49000,
               "hard_min_salary": True, "seeds": 8,
               "all_team_five": True, "cover_min_size": 2},
    "cover3": {"score": None, "top": 1, "min_total_salary": 49000,
               "hard_min_salary": True, "seeds": 8,
               "all_team_five": True, "cover_min_size": 3},
    "cover4": {"score": None, "top": 1, "min_total_salary": 49000,
               "hard_min_salary": True, "seeds": 8,
               "all_team_five": True, "cover_min_size": 4},
    # cover5 is the unconditional per-team 5-stack guarantee, which measured
    # -2.81 on deep slates in the 09/07 sweep -- but that test was NOT
    # equal-size: it built 5-13 MORE lineups, because the guarantee was added
    # to the allocation instead of spent from it. With the budget fix this is
    # a clean re-test at dN 0, and the cover2/3/4 ordering (bigger loses
    # less) says it is the one worth re-running.
    "cover5": {"score": None, "top": 1, "min_total_salary": 49000,
               "hard_min_salary": True, "seeds": 8,
               "all_team_five": True, "cover_min_size": 5},
    # ---- batting order on fills, RETESTED 09/07 --------------------------
    # The block above rejected bo6 on 9 slates against minspend47, with the
    # paired verdict computed on dMean -- the same defect that made the
    # stack-size result wrong for weeks. Retested on minspend49 over the full
    # slate set.
    #
    # Measured first, 3,132 player-slates over 24 slates, realised FPTS:
    #
    #   BO    meanPts  p(>=15)  meanSal        BO 1-6  7.18  15.2%  4194
    #    1     8.81     21.6%    4315          BO 7-9  5.34  10.6%  3003
    #    2     7.56     17.1%    4744
    #    3     7.84     15.5%    4609    Top six score 34% more and spike at
    #    4     6.94     12.9%    4247    15.2% vs 10.6% -- but cost 40% more,
    #    5     6.11     14.3%    3736    so points per $1,000 is 1.71 vs 1.78,
    #    6     5.75      9.4%    3489    marginally FAVOURING the bottom. That
    #    7     4.98      8.5%    3194    is why the bs/salary fill sort keeps
    #    8     5.88     13.9%    3009    reaching for 7-9.
    #    9     5.16      9.3%    2793
    #
    # Note the ordering is NOT monotonic: BO 8 beats BO 6 and 7 on both mean
    # and spike rate -- it is the spot that turns over to the top. And BO 7
    # spikes LEAST of all nine, which contradicts the old rejection's stated
    # mechanism ("7-9 is where the cheap volatile bats that spike live").
    # Hence three arms: the natural break at 5, the user's 6, and 6-plus-8.
    "bo5": {"score": None, "top": 1, "min_total_salary": 49000,
            "hard_min_salary": True, "seeds": 8,
            "fill_bo_allow": {1, 2, 3, 4, 5}},
    "bo6": {"score": None, "top": 1, "min_total_salary": 49000,
            "hard_min_salary": True, "seeds": 8,
            "fill_bo_allow": {1, 2, 3, 4, 5, 6}},
    "bo6x8": {"score": None, "top": 1, "min_total_salary": 49000,
              "hard_min_salary": True, "seeds": 8,
              "fill_bo_allow": {1, 2, 3, 4, 5, 6, 8}},
    # ---- let the FILLS face our own SP; the stack never may --------------
    # User's idea, 09/07. The builder bans any hitter opposing either SP.
    # That is correct for the STACK -- betting on a team while betting
    # against it with your arm is incoherent -- but the fills are 1-3 slots
    # chosen after the stack, and banning two whole teams there is what runs
    # the construction space dry. On a 3-game card it is severe: the SP pair
    # bans two of six teams, so fills draw from four.
    #
    # Cost: a fill facing our own SP is negatively correlated -- his hits are
    # our pitcher's runs. Benefit: more legal constructions, which is the
    # binding constraint whenever a slate caps the portfolio below the entry
    # count. Tested both on top of minspend49 and with stack554, since the
    # 5-stack version has the least fill freedom to begin with.
    "fillopp49": {"score": None, "top": 1, "min_total_salary": 49000,
                  "hard_min_salary": True, "seeds": 8,
                  "fill_may_oppose": True},
    "fillopp554": {"score": None, "top": 1, "min_total_salary": 49000,
                   "hard_min_salary": True, "seeds": 8,
                   "stack_sizes": (5, 5, 4), "fill_may_oppose": True},
    # ---- SMALLER primary stacks, the opposite of stack554 ----------------
    # User's idea, 09/07, and better motivated than stack554 was. The team
    # study over 384 team-slates found implied_total predicts a team's top-5
    # output at only +0.130 within-slate, and the highest-implied team lands
    # in the BOTTOM HALF of realised output 54.5% of the time. When the edge
    # in picking the team is that thin, five bats on one guess is a large bet
    # on a near-coin-flip -- 09/06 morning produced a -0.10 lineup that was
    # five PIT bats against a shutout. A 4-man primary spreads the same 8
    # hitter slots over more teams, buying coverage of an unpredictable event
    # instead of doubling down on a weak prediction.
    #
    # Cuts against it: whole-field P(top10) rises with stack size (0.304% for
    # a 4-stack, 0.517% for a 5-stack). This measures which effect dominates
    # for US, given we cannot pick the team.
    "stack443": {"score": None, "top": 1, "min_total_salary": 49000,
                 "hard_min_salary": True, "seeds": 8,
                 "stack_sizes": (4, 4, 3)},
    "stack433": {"score": None, "top": 1, "min_total_salary": 49000,
                 "hard_min_salary": True, "seeds": 8,
                 "stack_sizes": (4, 3, 3)},
    # ---- rotate the RNG path as the portfolio fills ----------------------
    # User's idea, 09/06: run a different seed every N lineups rather than one
    # seed for all of them. Each spec gets exactly one attempt per seed -- the
    # first valid construction wins -- so a single-seed portfolio explores
    # every spec once and a poor RNG path on the slate's best stack is never
    # revisited. Blocking re-rolls construction without thinning the
    # allocation. Tested at 10 and 20 on top of the shipped minspend49.
    "seedblk10": {"score": None, "top": 1, "min_total_salary": 49000,
                  "hard_min_salary": True, "seeds": 8, "seed_block": 10},
    "seedblk20": {"score": None, "top": 1, "min_total_salary": 49000,
                  "hard_min_salary": True, "seeds": 8, "seed_block": 20},
    # ---- bigger primary stacks, RETESTED --------------------------------
    # Forcing 5-stacks measured -11.89 and 5,5,4 measured -26.00, but both
    # predate the 09/03 harness fixes: the paired verdict then tested dMean,
    # the proxy that shipped spend15, so a change trading mean for ceiling was
    # scored backwards. 5,5,4 was also only 4 slates, and the set is now 12
    # dates. Retested on top of minspend49 so the stack size is the only
    # difference. 70% of top-10 lineups run a 5-stack against our 12.9%, and
    # P(top10) across the 09/04 field rises 0.304% -> 0.517% from a 4- to a
    # 5-stack.
    "stack555": {"score": None, "top": 1, "min_total_salary": 49000,
                 "hard_min_salary": True, "seeds": 8,
                 "stack_sizes": (5, 5, 5)},
    "stack554": {"score": None, "top": 1, "min_total_salary": 49000,
                 "hard_min_salary": True, "seeds": 8,
                 "stack_sizes": (5, 5, 4)},
    # ---- pitcher on the stacked team ------------------------------------
    # Built 09/05 from the 09/04 field study. Tested ON TOP of the shipped
    # minspend49 so the comparison isolates the pairing, not the floor.
    "spstack": {"score": None, "top": 1, "min_total_salary": 49000,
                "hard_min_salary": True, "seeds": 8, "sp_with_stack": True},
    # The same pairing WITHOUT the salary floor, to check the two do not
    # simply overlap -- both spend up, and an effect could be double-counted.
    "spstackonly": {"score": None, "top": 1, "sp_with_stack": True},
    "minspend485": {"score": None, "top": 1, "min_total_salary": 48500,
                    "hard_min_salary": True, "seeds": 8},
    "minspend495": {"score": None, "top": 1, "min_total_salary": 49500,
                    "hard_min_salary": True, "seeds": 8},
    # ---- cut the hitters who bust more often than they produce ----
    # Criterion, stated by the user and measured directly from per-game
    # history: drop anyone whose P(DK == 0) exceeds P(DK >= 10). Over 1,219
    # player-slates with 20+ prior games, 41.9% of the pool meets it, and they
    # are exactly what you would expect -- P(0) .333 vs .196, P(>=10) .192 vs
    # .313, real mean 5.29 vs 7.61 points.
    #
    # avg26 is a near-perfect build-time proxy for it: corr -0.769, and the
    # bottom decile is 100% busts against 0% in the top. Crucially the cut is
    # CLEAN -- at every threshold up to avg26 < 5.0, 100% of the players
    # removed are true busts, so there are no false positives to trade off:
    #
    #     avg26 < 3.5  removes  4.2% of pool, 100% of them true busts
    #     avg26 < 4.0  removes  6.8%          100%
    #     avg26 < 4.5  removes 11.9%          100%
    #
    # Fills only, same reason as the salary floor: a stack is a contiguous
    # batting-order run and cannot be filtered without breaking the
    # correlation it exists for. So a bust batting 6th in a 5-stack still gets
    # rostered; this only stops us reaching for one to fill a spare slot.
    # Swept at 3.5/4.0/4.5 over 320 builds, 8 slates x 8 seeds. 3.5 won:
    #
    #     vs control          best   hit top-10      sd
    #     spend47nobust35   +7.709   .047 -> .156  +0.244
    #     spend47nobust40   +5.541   .047 -> .125  -0.227
    #     spend47nobust45   +5.878   .047 -> .109  -0.177
    #
    # hit10 at 3.5 is p=0.038, and it is the only arm all project to INCREASE
    # spread rather than compress it -- the profile a top-10 objective needs.
    #
    # Treat it with suspicion anyway, for four reasons. (1) That run tested 24
    # hypotheses, so one p<0.05 is what chance alone produces. (2) The
    # dose-response is not monotone: 3.5 > 4.5 > 4.0. (3) On 08/28, the first
    # live slate where it could be checked, lineups CONTAINING the busts
    # scored 101.6 against 82.0 for those without, and more busts meant better
    # scores. (4) This backtest methodology has now lost live four times
    # running -- spend15 read +10.60 (p=0.056) and delivered -7.17.
    #
    # Why it is nonetheless coherent: avg26 predicts realised FPTS at r=+0.104
    # against salary's +0.111, i.e. it is real information but adds nothing on
    # top of price. So this cut is "drop the cheapest, worst players", the same
    # family as minspend47 -- it removes bad lineups rather than selecting good
    # ones. That is the pattern for the whole project: every arm that helped
    # removed the bottom, every arm that tried to pick winners leaned on bs and
    # failed. Unshipped until it survives slates it was not scored against.
    "spend47nobust35": {"score": None, "top": 1, "min_total_salary": 47000,
                        "hitter_min_avg26": 3.5},
    # ---- batting order on fill hitters: TESTED AND REJECTED ----
    # The fill loop sorts on bs/salary with no batting-order term, so a number
    # 9 hitter competes with a leadoff man while getting roughly one fewer
    # plate appearance. Over 1,230 hitter-slates, P(FPTS < 5) runs 34.6%
    # batting 1st to 66.2% batting 9th, mean points 8.87 down to 4.68, and a
    # bo <= 6 filter moves pool-wide P(<5) from 50.2% to 45.7% -- about five
    # times what the avg26 >= 3.5 cut manages (49.4%).
    #
    # It did not translate. 320 builds, 9 slates x 8 seeds, paired vs control:
    #
    #     arm                best   hit top-10      sd
    #     minspend47       +4.300        0.083   -0.281
    #     spend47nobust35  +6.122        0.139   -0.009
    #     spend47bo6       +3.402        0.111   -0.604
    #     spend47bo7       +4.678        0.097   -0.269
    #
    # bo6 finished BELOW minspend47 alone. Reducing the bust rate is not the
    # same as improving the tail: the 7-9 spots are where the cheap volatile
    # bats live, and those are what spike. sd tells the story -- bo6 -0.604
    # against nobust35's -0.009, and the arm that kept its spread won. It also
    # costs portfolio size (17 lineups against 19 on 08/24), which is a bad
    # trade when a single lineup has to reach the top 10.
    #
    # fill_max_bo and --fill-max-bo remain wired for future work; no variant
    # sets them.
    # ---- maximum correlation: one game, all in ----
    # Not a coverage arm. Our portfolio is built to spread -- 40 lineups
    # sharing only 1.1-1.8 players, max stack 4.03 -- which is right for
    # cashing and wrong for winning. Reaching the 99.97th percentile of a
    # 3,500-entry field needs a lineup whose players succeed or fail TOGETHER.
    #
    # So: every stack is 5, and the bring-back is always taken, making each
    # lineup a bet on one game exploding. Three of the nine winning lineups on
    # record were 5-stacks. Expect a lower mean and a fatter right tail; the
    # mean is not the point.
    "maxcorr": {"score": None, "top": 1, "stack_sizes": (5, 5, 5),
                "force_bringback": True},
    "maxcorr47": {"score": None, "top": 1, "stack_sizes": (5, 5, 5),
                  "force_bringback": True, "min_total_salary": 47000,
                  "ignore_vegas_adj": True},
    # ---- rank pitchers on raw blended, not the Vegas-adjusted number ----
    # See build_sp_pool for the measurement. This is not a portfolio rule; it
    # changes which pitchers the builder believes are good, so it should move
    # more than any construction arm has.
    "novegas": {"score": None, "top": 1, "ignore_vegas_adj": True},
    "spend47novegas": {"score": None, "top": 1, "min_total_salary": 47000,
                       "ignore_vegas_adj": True},
    # Tested and dropped: hitter_min_salary 3000 AND min_total_salary 47000
    # together ("floorspend") scored WORSE than the spend floor alone -- best
    # +1.70 vs +5.00, top-10 rate back to control's .047 -- and compressed
    # spread (-0.95, p=0.077). The two guardrails interfere rather than
    # compose; the hitter floor is what drags it down. Do not recombine.
    # ---- "spend it, then chase spikes" was tried and lost. Do not rebuild ----
    # spendboom = min_total_salary 47000 + _top3_bs at top=5, built to WIDEN
    # the right tail rather than trim the left, on the reasoning that
    # minspend47 frees salary without compressing spread and _top3_bs would
    # spend it on concentrated upside. Run as a 2x2 so the floor and the score
    # could be told apart -- 256 builds, 8 slates x 8 seeds:
    #
    #     arm                       best   gap to 10th      sd
    #     control                 123.31         27.06   27.20
    #     minspend47 (floor)      127.69         22.68   27.05
    #     boom       (score)      123.58         26.79   25.86
    #     spendboom  (both)       122.03         28.34   25.19
    #
    # The floor does all the work. The score adds nothing alone (+0.27 on
    # best) and SUBTRACTS in combination: spendboom came in 0.96 below control
    # and ~6 below the floor by itself. Worse, it did the opposite of what it
    # was for -- sd -2.07 (p=0.043), the only significant result in the run,
    # and in the wrong direction.
    #
    # The reason is structural, and it retires this whole line of attack:
    # SELECTION COMPRESSES SPREAD. Read the sd column top to bottom -- 27.20
    # with no scoring, 27.05 with a floor but still no scoring, 25.86 with a
    # score, 25.19 with a score and a floor. Choosing the best of N candidates
    # pulls every lineup toward the same preferred players, so the portfolio
    # converges; top=5 only softens what argmax did to `ceiling`. Control's
    # "first valid construction" is therefore already the variance-MAXIMISING
    # configuration. A tail arm cannot be built by scoring harder; it has to
    # change the pool or the construction rules instead.
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
    # The lineups feed marks a two-way player's role inside the name --
    # "Shohei (H) Ohtani" is his hitter row, "(P)" would be the pitcher one --
    # and DK carries no such marker. "(h)" survives the middle-initial filter
    # because it is three characters long, so the name never matched and the
    # best bat on the slate was silently dropped from the pool. Strip any
    # parenthesised group first; this also removes DK's "(12345)" id suffix.
    s = re.sub(r"\([^)]*\)", " ", s)
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


def build_sp_pool(dk, lu, padj, opp_map, n_lineups,
                  ignore_vegas_adj=False):
    rows = []
    sp_rows = lu[(lu["bo"] == "SP") & slate_io.confirmed_mask(lu["conf"])]
    slate_io.unconfirmed_banner(int((sp_rows["conf"] != "Y").sum()),
                                "STARTING PITCHERS")
    for _, r in sp_rows.iterrows():
        cand = dk[(dk["nname"] == r["nname"])
                  & (dk["Roster Position"].isin(PITCHER_POS))]
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
                     # Measured 08/28: the Vegas adjustment makes pitcher
                     # ranking WORSE on all nine stored slates. Paired on the
                     # same slate, blended -> adj_blended costs -0.179
                     # Spearman (t=-3.57, p=0.0073), taking +0.188 down to
                     # +0.009, and vegas_adj itself correlates -0.190 with the
                     # residual blended could not explain -- i.e. the arms it
                     # marks down outperform. Setting this flag ranks on the
                     # raw numbers instead, everywhere at once: the median
                     # split, the cap tiers, the pair sort and the anchor all
                     # read these two fields.
                     "adj_bs": a["bs"] if ignore_vegas_adj else a["adj_bs"],
                     "adj_blended": (a["blended"] if ignore_vegas_adj
                                     else a["adj_blended"]),
                     "blended": a["blended"]})
    sp_df = pd.DataFrame(rows)
    if sp_df.empty:
        # "Pulled too early" and "name-match bug" look identical here but need
        # opposite responses, so say which one it is.
        n_sp = int((lu["bo"] == "SP").sum())
        n_conf = int(((lu["bo"] == "SP")
                      & slate_io.confirmed_mask(lu["conf"])).sum())
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


def report_missing_teams(dk, hit_pool, vegas=None):
    """Name every slate team that contributed ZERO hitters to the pool.

    DIAGNOSTIC ONLY -- prints, changes no construction, touches no counter.

    It exists because the builder is otherwise silent about this and the cost
    is not small. On 09/08 the 15:43 lineups feed had 18 of 20 teams posted,
    so TOR and TEX were dropped from the hitter pool entirely and appeared in
    none of the 77 entered lineups. TOR had the slate's HIGHEST implied total
    (5.50) and faced its WEAKEST starter (Jack Perkins, adj_bs -7.21). The
    only warning printed was "only 18 of 20 SPs confirmed", which reads like
    two missing arms rather than two missing offences.

    preflight.py gates on the same condition and stops the run, but the
    replay harness and any direct builder call skip preflight, so the warning
    is repeated at the one place the pool actually exists.
    """
    try:
        on_slate = set(dk["TeamAbbrev"].astype(str).str.strip().str.upper())
        have = {str(h["team"]).strip().upper() for h in hit_pool}
    except Exception:
        return
    missing = sorted(on_slate - have)
    if not missing:
        return
    order = []
    if vegas is not None:
        try:
            order = list(vegas["implied_total"].astype(float)
                         .sort_values(ascending=False).index)
        except Exception:
            order = []
    print()
    print("  " + "!" * 60)
    print(f"  !! {len(missing)} of {len(on_slate)} slate teams contributed NO "
          f"hitters to the pool:")
    for t in missing:
        note = ""
        if t in order:
            try:
                note = (f"  implied {float(vegas.loc[t, 'implied_total']):.2f}"
                        f"  (#{order.index(t) + 1} of {len(order)} on the slate)")
            except Exception:
                note = ""
        print(f"  !!   {t}{note}")
    print("  !! Not a fade -- their lineups were unposted when the feed was")
    print("  !! pulled. They cannot appear in ANY lineup. If those games have")
    print("  !! not locked, rebuild once the lineups post.")
    print("  " + "!" * 60)


def build_hitter_pool(dk, lu, hcache, opp_map, vegas=None):
    r"""...plus `own_pct`, a projected-ownership percentile.

    Ownership is the one thing about a slate that is genuinely PREDICTABLE.
    Measured over 2,084 hitter-slates against the %Drafted column of 14
    standings exports, within-slate Spearman runs: batting order -0.506,
    team implied total +0.407, avg26 +0.405, AvgPointsPerGame +0.394, salary
    +0.351. Every metric in metric_study predicts realised POINTS between
    -0.17 and -0.06, so ownership is roughly four times more forecastable
    than scoring -- which follows, since ownership is a human consensus built
    from exactly these visible inputs while points are mostly noise.

    The blend below is batting order + implied total + AvgPointsPerGame,
    z-scored within the slate and equally weighted: rho +0.632 mean over 14
    slates, worst slate +0.504, no sign flips. Adding salary or avg26 made it
    WORSE (+0.563, +0.543), so this stays at three features and no fitted
    weights -- there is nothing here to overfit.

    Reported as a 0-100 percentile within the slate so a threshold reads the
    way DK's own %Drafted column does.
    """
    pool, seen = [], {}          # seen: nname -> index in pool
    hit_rows = lu[slate_io.confirmed_mask(lu["conf"])]
    slate_io.unconfirmed_banner(int((hit_rows["conf"] != "Y").sum()),
                                "HITTERS")
    for _, r in hit_rows.iterrows():
        bo = pd.to_numeric(r["batting order"], errors="coerce")
        if not (1 <= (bo or 0) <= 9):
            continue
        cand = dk[(dk["nname"] == r["nname"])
                  & (~dk["Roster Position"].isin(PITCHER_POS))]
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
        # DOUBLEHEADERS put the same player in the feed twice, once per
        # game_number, at DIFFERENT batting orders -- on 08/29 that was 27
        # players, e.g. Ceddanne Rafaela batting 2nd in game 1 (confirmed)
        # and 4th in game 2 (a projection). With --allow-unconfirmed both
        # rows pass, so he entered the pool twice and could occupy two slots
        # of one "contiguous" batting-order window. The audit's duplicate
        # check was the only thing catching it, and only sometimes: the
        # window list is normally sampled from its first four entries, and
        # the offending construction sits at the end.
        #
        # Keep one row per player, preferring the CONFIRMED one -- a posted
        # lineup beats a projection for the other half of a twin bill.
        prev = seen.get(r["nname"])
        if prev is not None:
            if pool[prev]["conf"] == "Y" or r["conf"] != "Y":
                continue                      # keep what we already have
            pool.pop(prev)                    # replace projection with posted
            seen = {k: (v - 1 if v > prev else v)
                    for k, v in seen.items() if v != prev}
        seen[r["nname"]] = len(pool)
        apg = pd.to_numeric(d.get("AvgPointsPerGame"), errors="coerce")
        pool.append({"name": d["Name"], "id": d["Name + ID"], "team": d["TeamAbbrev"],
                     "conf": r["conf"],
                     "opp": opp_map[d["TeamAbbrev"]], "salary": int(d["Salary"]),
                     "bo": int(bo), "bs": bs, "avg26": avg26, "slots": slots,
                     "apg": float(apg) if pd.notna(apg) else 0.0})

    # ── projected ownership (see the docstring for the measurements)
    def _z(vals):
        s = pd.Series(vals, dtype="float64")
        sd = s.std(ddof=0)
        return ((s - s.mean()) / sd).tolist() if sd and sd > 0 else [0.0] * len(s)

    if pool:
        imp = [(vegas.loc[h["team"], "implied_total"]
                if vegas is not None and h["team"] in vegas.index else 4.0)
               for h in pool]
        # batting order enters NEGATIVE: leading off is the most-owned slot.
        blend = [a + b + c for a, b, c in zip(_z([-h["bo"] for h in pool]),
                                              _z(imp),
                                              _z([h["apg"] for h in pool]))]
        order = sorted(range(len(pool)), key=lambda i: blend[i])
        for rank, i in enumerate(order):
            pool[i]["own_pct"] = round(100.0 * rank / max(1, len(pool) - 1), 1)
    return pool


# ─────────────────────────────────────────────────────────────────────────────
# Allocation (Fix #14) + fades (Fix #6/#10)
# ─────────────────────────────────────────────────────────────────────────────

def allocate_stacks(hit_pool, sp_df, vegas, n_lineups, opp_map,
                    max_stacks=MAX_STACKS_PER_TEAM, fade_sp_bs=FADE_SP_BS):
    hit_df = pd.DataFrame(hit_pool)
    top4 = hit_df.groupby("team")["bs"].apply(lambda x: x.nlargest(4).sum())
    impl = pd.Series({t: vegas.loc[t, "implied_total"] if t in vegas.index else 4.0
                      for t in top4.index})
    nb = (top4 - top4.min()) / max(top4.max() - top4.min(), 1e-9)
    ni = (impl - impl.min()) / max(impl.max() - impl.min(), 1e-9)
    stackscore = 0.6 * nb + 0.4 * ni

    # A hard fade removes a whole offense from every lineup -- there is no
    # relief valve in cash mode and only FADE_APPEAR_CAP in GPP. On a thin
    # slate it also SHRINKS THE POOL: 09/07 (3 games) faded ATH for facing
    # Cease (adj_bs 63.47) and built 36 distinct lineups against 67 entries,
    # so the same rule that zeroed one team also forced 31 duplicates.
    fades = {s["opp"] for _, s in sp_df.iterrows() if s["adj_bs"] >= fade_sp_bs}
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


def make_specs(alloc, n_lineups, sizes=(5, 4, 3), all_teams=None, per_team=1,
                tier_by_count=False, guar_size=5, guar_sizes=None,
                interleave=False):
    """Tier assignment: ~20% ceiling, ~20% contrarian, rest core.

    `sizes` is (ceiling, core, contrarian) stack sizes.

    `all_teams`, when given, guarantees EVERY team on the slate one 5-stack
    before the normal allocation runs. Motivation, measured 09/07 over 384
    team-slates: implied_total predicts a team's top-5 output at only +0.130
    within-slate, and the highest-implied team lands in the BOTTOM HALF of
    realised output 54.5% of the time. We cannot pick the team, so guarantee
    coverage of all of them and let the existing allocation weight the rest.

    This deliberately OVERRIDES the fade. 09/07: ATH was hard-faded for
    facing Cease (adj_bs 63.47), so they appeared in zero of 36 lineups --
    and then scored 4 runs off him. A fade is a prior on a signal we have
    measured as barely better than chance; coverage is not.

    Distinct from stack554, which forced big stacks but still allocated teams
    by stackscore -- it concentrated on the teams we already liked. This
    guarantees a coverage floor first.

    `guar_size` is the stack size of those guaranteed specs (default 5). A
    SMALLER guarantee is the deep-slate variant: covering 18 teams with
    5-stacks costs 18 lineups on offences the allocator rates last and
    measured negative (-2.81 on 8+ games), but covering them with 3-stacks
    costs the same lineup count at a fraction of the roster commitment. See
    the cover* variants.
    """
    ceil_sz, core_sz, cont_sz = sizes
    guaranteed = []
    if all_teams:
        # `per` lineups per team, ROUND-ROBIN rather than team-by-team. When a
        # slate underfills -- 09/07 delivered 30 of 67 -- the builder walks the
        # spec list in order and whatever sits at the back is never attempted.
        # Blocking by team would give the first team all `per` of its lineups
        # and the last team none, which is the same silent-tail problem that
        # left SF and ATH at zero in the first place.
        seen, uniq = set(), []
        for t in all_teams:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        # `guar_sizes` is a LADDER: one pass per size, every team served in
        # each pass. (5, 4) gives every team a 5-stack first and then every
        # team a 4-stack, so an underfilling slate still covers all teams at
        # the bigger size before any team gets a second look.
        #
        # Tier follows SIZE, matching sizes=(ceiling, core, contrarian): a
        # 5-stack is CEILING, a 4-stack is CORE. That matters -- CORE is where
        # 75% of historical winners came from, and before this the guarantee
        # emitted CEILING only, so every non-primary team's coverage sat in
        # the tier that has produced the fewest.
        _ladder = ([int(guar_size)] * max(1, per_team) if not guar_sizes
                   else [int(z) for z in guar_sizes])
        _tier_for = {sizes[0]: "CEILING", sizes[1]: "CORE",
                     sizes[2]: "CONTRARIAN"}
        for sz in _ladder:
            for t in uniq:
                guaranteed.append({"stack": t,
                                   "tier": _tier_for.get(sz, "CORE"),
                                   "size": int(sz)})
        guaranteed = guaranteed[:max(0, n_lineups)]
        n_lineups = max(0, n_lineups - len(guaranteed))
        if n_lineups == 0:
            return guaranteed
    n_ceil = max(1, round(n_lineups * 0.2))
    n_cont = max(1, round(n_lineups * 0.2))
    # REVERTED 09/07. Round-robin interleaving of the allocation looked like a
    # fix for the silent tail (SF got 0 stacks on a slate that underfilled),
    # but it is emitted in BLOCK order for a reason: the tier ladder below
    # assigns CEILING to the first n_ceil specs and CONTRARIAN to the last
    # n_cont, so interleaving scatters the tiers across teams instead of
    # giving the best offences the ceiling slots. Measured cost to the SHIPPED
    # arm: dBest vs control fell +5.2 (t 3.09) to -1.1 (t -0.67), bestAvg
    # 148.2 -> 141.5, across 23 slates. Do not re-apply without re-working the
    # tier assignment at the same time.
    stack_list = [t for t, n in alloc.items() for _ in range(n)]
    # CEILING is tagged by COUNT, not by index. It used to be `idx < n_ceil
    # and team not in first_seen`, but the list is in block order -- all of
    # MIL's ten lineups, then TOR's five -- so the first n_ceil positions are
    # two or three teams repeated and only their first spec qualified. The
    # tier configured for ~20% of the portfolio was delivering 5.2% (and
    # 4.1% over 919 historical entries). Counting qualifying specs instead
    # keeps the intent -- one ceiling lineup per team, spread across the best
    # offences -- while actually producing n_ceil of them.
    #
    # This matters beyond the label: in the default sizes=(5,4,3), 5-stacks
    # live ONLY in CEILING, so the tier under-delivering is most of why the
    # portfolio runs ~18% 5-stacks against a field of 50.3%.
    specs, first_seen, n_tagged = [], set(), 0
    for idx, team in enumerate(stack_list):
        _ceil_ok = (n_tagged < n_ceil if tier_by_count else idx < n_ceil)
        if _ceil_ok and team not in first_seen:
            tier, size = "CEILING", ceil_sz
            n_tagged += 1
        elif idx >= len(stack_list) - n_cont:
            tier, size = "CONTRARIAN", cont_sz
        else:
            tier, size = "CORE", core_sz
        first_seen.add(team)
        specs.append({"stack": team, "tier": tier, "size": size})
    # ---- attempt order, separately from tier assignment -------------------
    # ONE LIST WAS DOING TWO JOBS. Spec position decides which teams get the
    # CEILING and CONTRARIAN tiers (the loop above), AND the order in which
    # the builder attempts them. Those are different questions and only the
    # first wants block order.
    #
    # When a slate UNDERFILLS, the second job dominates: 09/09 delivered 15 of
    # 90 from a FLAT allocation (SD 12, WSH 12, STL 12, TOR 12) and SD took 14
    # of the stacks purely because its block sorted first. The builder never
    # reached anyone else.
    #
    # Interleaving the whole list was tried 09/07 and cost 6.7 points, because
    # it scattered the tiers -- it broke job one to fix job two. This reorders
    # AFTER tagging, so every spec keeps the tier it was assigned on the
    # block-ordered list and only the walk order changes. Team COUNTS are
    # untouched, so unlike the 5,4 ladder (-5.62 on thin slates) this moves no
    # lineups between teams.
    if interleave and specs:
        byteam = {}
        order = []
        for sp in specs:
            if sp["stack"] not in byteam:
                byteam[sp["stack"]] = []
                order.append(sp["stack"])
            byteam[sp["stack"]].append(sp)
        woven = []
        while any(byteam[t] for t in order):
            for t in order:
                if byteam[t]:
                    woven.append(byteam[t].pop(0))
        specs = woven
    return guaranteed + specs


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
        # Per-TIER fill floor. The global hitter_min_salary applies to every
        # lineup; this one applies only to tiers NOT in fill_floor_exempt, so
        # the contrarian tail can keep punting while the rest of the
        # portfolio gets a floor under its weakest bat.
        self.fill_floor = 0
        self.fill_floor_exempt = frozenset()
        # Contiguous run of N bats from a SECOND team, placed before the fill
        # loop. 0 = off (historical behaviour).
        self.secondary_size = 0
        # Allow both starters of the SAME game in one lineup.
        #
        # 09/10 is why this exists. First place in the 1,189-entry contest
        # scored 119.05 against our 110.70, and it was NOT a lottery lineup --
        # its eight hitters totalled 50 points with a zero among them. It won
        # on TWO PITCHERS: Logan Gilbert 32.1 (41.4% owned) and Jacob deGrom
        # 36.9 (59.0% owned), 69.0 of the 119.05.
        #
        # We rostered both. deGrom 20 of 47, Gilbert 16 of 47 -- the two
        # highest adj_blended on the card (23.79 and 23.48), bought heavily and
        # correctly. We never PAIRED them, because they start against each
        # other in TEX@SEA and _form_pairs bans same-game pairs outright.
        #
        # The ban is sound on average: opposing starters cancel, since one
        # team's runs are the other pitcher's damage. It has never been
        # measured. On a 3-game card it removes a third of all possible pairs.
        self.allow_same_game_sp = False
        self.hitter_min_own = None
        self.min_total_salary = None
        # When set, the min-spend ladder does NOT bend. The ladder exists so a
        # floor cannot silently shrink the portfolio, but it also means a
        # "49,000 floor" portfolio can contain 47,000 lineups, which makes the
        # floor untestable. A hard floor underfills instead; multi-seed
        # merging is what refills it.
        self.hard_min_salary = False
        # Prefer an SP who plays FOR the stacked team (see pick_sp_pair).
        self.sp_with_stack = False
        # Advance the RNG path every N lineups instead of holding one seed for
        # the whole portfolio. See block_seed().
        self.seed_block = 0
        # Let FILL hitters face our own SP. The stack never may.
        self.fill_may_oppose = False
        # Restrict FILL hitters to these batting-order slots (None = 1-9).
        self.fill_bo_allow = None
        self.hitter_min_avg26 = None
        self.fill_max_bo = None
        self.force_bringback = False
        self.reject = defaultdict(int)        # why attempts were thrown away
        self.seen_sigs = set()
        self.lineups = []

    @staticmethod
    def sig(lu_):
        return tuple(sorted(p["name"] for p in lu_.values()))

    def block_seed(self):
        """The seed to build the NEXT lineup under.

        One seed per portfolio means every spec is explored exactly once --
        the first valid construction the RNG produces wins, and a poor path on
        the slate's best stack is never revisited. Advancing the seed every
        `seed_block` lineups re-rolls construction as the portfolio fills,
        WITHOUT thinning the allocation: the spec list is untouched, so every
        team still gets its lineups, they are just built under different RNG
        paths.

        Motivated by 09/06: the same arm on the same snapshot gave `best` of
        131.20 / 151.60 / 161.20 across three seeds -- a 30-point swing that a
        single-seed portfolio is fully exposed to.
        """
        if not self.seed_block:
            return self.seed
        return self.seed + len(self.lineups) // self.seed_block

    def pick_sp_pair(self, spec, rng):
        stack_t = spec["stack"]
        avoid = {stack_t}
        if spec.get("bringback"):
            avoid.add(self.opp_map[stack_t])
        elig = [s for _, s in self.sp_df.iterrows()
                if self.sp_use[s["name"]] < self.caps[s["name"]] and s["opp"] not in avoid]
        # ---- SP on the stack's OWN team -------------------------------
        # Measured on 09/04 across the full 18,007-lineup field: lineups
        # whose SP plays for the team they stack hit the top 10 at 0.801%
        # against 0.309%, a 2.6x rate, while mean points are flat (93.72 vs
        # 93.10). It buys nothing in the middle and only lengthens the right
        # tail, which is the shape this objective wants. The mechanism is
        # real rather than correlational: DK pays a pitcher a Win bonus, and
        # his team scoring runs is what produces the win, so "the stack
        # erupts" and "the pitcher wins" are the same event.
        #
        # 26.8% of top-10 lineups put six players on one team this way (five
        # hitters plus that team's starter, legal because the >5 cap counts
        # hitters only). We had built it ZERO times in 139 entries -- not by
        # choice but by construction: the bring-back adds a bat from the
        # opposing side, and `avoid` then bans every arm whose opp is that
        # team, which is exactly the stack team's own starter. The two ideas
        # are mutually exclusive per lineup, so this arm drops the bring-back
        # rather than trying to hold both.
        if self.sp_with_stack and not spec.get("bringback"):
            own = [s for s in elig if s["team"] == stack_t]
            if own:
                others = [s for s in elig if s["team"] != stack_t]
                rng.shuffle(own)
                rng.shuffle(others)
                key = lambda s: -(s["adj_blended"] + rng.uniform(0, 5))
                own.sort(key=key)
                others.sort(key=key)
                pairs = self._form_pairs(own + others, PAIR_CAP,
                                         self.sp_salary_cap)
                pairs = [(a, b) for a, b in pairs
                         if stack_t in (a["team"], b["team"])]
                if pairs:
                    return pairs
                self.reject["no legal pair using the stack team's own SP"] += 1
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
                        if (self.allow_same_game_sp
                            or self.game_map[a["team"]] != self.game_map[b["team"]])
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
                if (not self.allow_same_game_sp
                        and self.game_map[a["team"]] == self.game_map[b["team"]]):
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
        # A cash lineup has no stack -- every hitter is a fill -- so
        # fill_may_oppose lifts the opposing-SP ban for all eight. This is a
        # bigger relaxation in character than the GPP version and it is where
        # the constraint actually bites: on a 3-game card the SP pair bans
        # two of six teams, leaving four to fill eight slots.
        ok_teams = {t for t in self.impl.index
                    if (self.fill_may_oppose or t not in banned)
                    and t not in self.fades}
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
                 if (self.allow_same_game_sp
                     or self.game_map[a["team"]] != self.game_map[b["team"]])]
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
        # max-correlation arms always take the bring-back: the point is to
        # bet the whole GAME, not just one side of it.
        spec["bringback"] = bool(
            not spec.get("no_bringback")
            and (self.force_bringback
                 or (stack_t in self.vegas.index
                     and self.vegas.loc[stack_t, "game_total"] >= BRINGBACK_TOTAL)))
        # A bring-back and an own-team SP cannot coexist: the bring-back bat
        # comes from the opposing side, and pick_sp_pair then bans every arm
        # whose opponent is that side -- which is the stack team's own
        # starter. Give up the bring-back only when this team actually HAS a
        # usable arm, so teams without one keep the existing behaviour.
        if self.sp_with_stack and spec["bringback"]:
            if any(s["team"] == stack_t
                   and self.sp_use[s["name"]] < self.caps[s["name"]]
                   for _, s in self.sp_df.iterrows()):
                spec["bringback"] = False
                self.reject["bring-back dropped for an own-team SP"] += 1
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
                # The bring-back has to leave the remaining slots affordable.
                # It ranks purely on bs, so it reaches for the dearest bat on
                # the opposing side, and on an expensive stack that bankrupts
                # the lineup before the fill loop starts.
                #
                # 09/02 evening: MIL had the joint-highest implied total, a
                # game total of 8.5 that triggers the bring-back, and CHC
                # opposite. A 5-man MIL window costs 23,200, the top CHC bat
                # by bs is Pete Crow-Armstrong at 7,000, a median SP pair is
                # 16,900 -- 47,100 with two slots left and 2,900 to fill them,
                # against a 2,000 minimum apiece. Those attempts all died:
                # 4,114 "no valid construction" for MIL, 9 of 17 built in
                # isolation and 0 of 17 in the real build. Jackson Chourio
                # (27.0 pts, in 73% of the top 1%) reached none of our 120
                # lineups because the team he leads off for never stacked.
                spent = salary + sum(p["salary"] for p in picked)
                rem = len(HITTER_SLOTS) - len(picked) - 1
                room = SALARY_CAP - spent - rem * 2000
                for bb in sorted((h for h in self.hit_pool if h["team"] == opp_t
                                  and self.fill_appear[h["name"]] < fill_cap
                                  and h["salary"] <= room),
                                 key=lambda x: -x["bs"])[:4]:
                    if assign_slots(picked + [bb], HITTER_SLOTS):
                        picked.append(bb)
                        break

        # ---- SECONDARY STACK -------------------------------------------
        # 09/09 (4 games): all three winning lineups were TWO-team splits --
        # STL 4 + SD 3, STL 4 + SD 3, SD 4 + STL 3 -- while 17 of our 24
        # lineups were single 5-stacks. Zero of our 24 held SD 3+ AND STL 3+.
        # We finished 11th, 1.10 off the bar, holding the right primary team
        # (SD was in 27 of 30 top-10 lineups) and the slate's top scorer
        # (Merrill 47.0, 9 of 24 lineups).
        #
        # A secondary 3-man stack measured -6.39 on 08/30 over NINE slates
        # against control, and the reasoning recorded then still stands on its
        # own terms: two 3-man runs need two teams to erupt, one 5-man run
        # needs one. This retests it because the set is now 25 slates, the
        # harness scores placement rather than dMean, and the baseline is
        # minspend49cov rather than control -- and because CLAUDE.md flags it
        # as one of the three closest calls a larger set could move.
        #
        # The second team is ranked by implied total and sampled from the top
        # few, never the primary team, never a team our SPs oppose, never a
        # faded team. It is a contiguous batting-order run like the primary,
        # so it carries the same correlation the fill loop cannot.
        if (self.secondary_size
                and len(picked) + self.secondary_size <= len(HITTER_SLOTS)):
            _used = {p["name"] for p in picked}
            _cands = [t for t in self.impl.index
                      if t != stack_t and t not in banned
                      and t not in self.fades]
            _cands.sort(key=lambda t: -float(self.impl.get(t, 0)))
            _cands = _cands[:4]
            rng.shuffle(_cands)
            _spent = salary + sum(p["salary"] for p in picked)
            for _t in _cands:
                _wins = stack_windows(self.hit_pool, _t, self.secondary_size)
                _hit = False
                for _w in _wins[:4]:
                    if any(x["name"] in _used for x in _w):
                        continue
                    _trial = picked + list(_w)
                    _rem = len(HITTER_SLOTS) - len(_trial)
                    _cost = _spent + sum(x["salary"] for x in _w)
                    if _cost + _rem * 2000 > SALARY_CAP:
                        continue
                    if assign_slots(_trial, HITTER_SLOTS) is None:
                        continue
                    picked = _trial
                    _hit = True
                    break
                if _hit:
                    break
            else:
                self.reject["no legal secondary stack"] += 1

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
        # Tier-conditional floor. Every decomposition of a losing slate says
        # worst4 separates and top3 does not -- 09/08 top-10 lineups ran
        # worst4 23.20 and 0.36 zeros against our 7.46 and 2.03. The fill sort
        # is bs/salary with the denominator clamped at 2000, which
        # MANUFACTURES punts by construction. A flat floor was tried twice and
        # lost (-10.03 at 3000, -18.54 at 3500), but flat means it also
        # removed the contrarian tail's reason to exist. This floors the tiers
        # that are supposed to be solid and leaves CONTRARIAN alone.
        if self.fill_floor and spec.get("tier") not in self.fill_floor_exempt:
            floor = max(floor, self.fill_floor)
        for slot in open_slots:
            rem = sum(1 for s in HITTER_SLOTS if s not in placed) - 1
            # reserve the floor, not 2000, or the last slots price themselves
            # out and the lineup dies late
            budget = SALARY_CAP - salary - rem * max(2000, floor)

            def eligible(min_sal):
                return [h for h in self.hit_pool
                        if slot in h["slots"] and h["name"] not in used
                        # FILL slots may face our own SP when the arm allows
                        # it; the STACK never may (it is chosen before this
                        # loop and `banned` already excluded it there).
                        and (self.fill_may_oppose or h["team"] not in banned)
                        and tcount[h["team"]] < 5
                        and h["salary"] <= budget and h["salary"] >= min_sal
                        and h["avg26"] >= (self.hitter_min_avg26 or 0)
                        and h.get("own_pct", 100.0) >= (self.hitter_min_own or 0)
                        and h["bo"] <= (self.fill_max_bo or 9)
                        and (not self.fill_bo_allow
                             or h["bo"] in self.fill_bo_allow)
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
            # This ranks by POINTS PER DOLLAR with the denominator clamped
            # at 2000, so the cheapest bat wins ties by construction, and a
            # roster's BEST hitters rank worst precisely because they are
            # expensive. Seen live on 08/29: Bryce Harper (bs 50.7) and Kyle
            # Schwarber (49.2), the two best PHI bats, were skipped entirely
            # while Alec Bohm (30.4) took 7 of 10 lineups.
            #
            # Tested and NOT adopted. Ranking on bs itself, with the existing
            # budget reserve handling affordability, was measured over 256
            # builds across 9 slates:
            #
            #     vs control     best   hit top-10      sd
            #     bsrank       +2.174        0.188   -0.762
            #     bsrank47     +3.747        0.175   -0.443
            #     minspend47   +3.822        0.175   -0.265
            #
            # Real but small, and it does not beat the salary floor alone --
            # both push toward spending on better players, so they are the
            # same intervention by different routes and do not add. The 08/29
            # miss was a 3-team slate where short candidate lists left the
            # value sort nowhere to hide; on a full card it washes out.
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

        Before adding a scored arm: SELECTION COMPRESSES SPREAD, always.
        Measured over 256 builds, 8 slates x 8 seeds, per-portfolio sd was
        27.20 with no scoring, 25.86 with a score at top=5, and 25.19 with a
        score plus a salary floor. Choosing the best of N pulls every lineup
        toward the same preferred players and the portfolio converges -- the
        same effect argmax had on `ceiling` (sd 20.1 against control's 40.2 on
        08/25), just milder. So n_candidates=1 is the variance-MAXIMISING
        setting, and a score can only help an objective that wants the middle
        moved, never one that wants a longer right tail. Four arms have now
        failed that way: ceiling, boom, value_boom, spendboom.
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
            if not base:
                ladder = [None]
            elif self.hard_min_salary:
                ladder = [base]
            else:
                ladder = [base, base - 1000, base - 2000, None]
            for step, level in enumerate(ladder):
                if step and cands:
                    break
                self.min_total_salary = level
                for attempt in range(500):
                    rng = stable_rng(self.block_seed(), spec["stack"], spec["tier"],
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
            if not cands and not spec.get("no_bringback"):
                # Bend the bring-back rather than drop the lineup, the same way
                # the min-spend ladder above bends. A 5-man stack plus a
                # bring-back leaves two slots, and when those cannot be filled
                # at the remaining positions and budget the spec dies silently.
                # On 09/02 evening that cost MIL -- joint highest implied total
                # on the slate -- all 17 of its allocated lineups, and with them
                # Jackson Chourio, who scored 27.0 and appeared in 73% of the
                # top 1%. Without the bring-back MIL builds 17 of 17.
                spec = dict(spec, no_bringback=True)
                self.reject["bring-back dropped to build the lineup"] += 1
                for attempt in range(500):
                    rng = stable_rng(self.block_seed(), spec["stack"], spec["tier"],
                                     "nobb", attempt)
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
                    break
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
                    pick = stable_rng(self.block_seed(), spec["stack"], spec["tier"],
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

def audit(lineups, game_map, allow_fill_oppose=False):
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
        # The opposing-hitter ban is absolute for the STACK and optional for
        # FILLS. A fill facing our own SP is negatively correlated -- his
        # hits are our pitcher's runs -- but it is 1-3 slots, and relaxing it
        # widens the construction space, which is what runs out on thin
        # slates. `stack` is the spec's team, so anything else is a fill.
        stack_t = L.get("spec", {}).get("stack")
        for sp in ("SP1", "SP2"):
            for s in HITTER_SLOTS:
                if lu_[s]["team"] != lu_[sp]["opp"]:
                    continue
                if allow_fill_oppose and lu_[s]["team"] != stack_t:
                    continue
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
                    help="floor-maximized lineups (default 0 at every slate "
                         "size since 09/07). These are built for 50/50s and "
                         "double-ups: CASH_TEAM_CAP=3 forbids 4- and "
                         "5-stacks, so they cannot make the shape that wins "
                         "a tournament")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--variant", default="minspend49cov",
                    choices=["control", "none"] + sorted(VARIANTS),
                    help="Build arm. Defaults to minspend49cov, shipped "
                         "09/07: minspend49 plus a per-team 5-stack guarantee "
                         "that fires only at <=4 games (+8.42 dBest there, "
                         "byte-identical above). Pass 'minspend49' for the "
                         "previous default, 'control' for the older builder, "
                         "or 'none' for the unsuffixed legacy filenames.")
    ap.add_argument("--candidates", type=int, default=20,
                    help="valid lineups to generate per slot before picking "
                         "the best (ignored by control; default 20)")
    ap.add_argument("--export", default=EXPORT_DIR)
    ap.add_argument("--stack-sizes", default="5,4,3",
                    help="ceiling,core,contrarian stack sizes (default 5,4,3)")
    ap.add_argument("--max-stacks", type=int, default=MAX_STACKS_PER_TEAM,
                    help=f"primary stacks per team (default {MAX_STACKS_PER_TEAM})")
    ap.add_argument("--allow-unconfirmed", action="store_true",
                    help="accept confirmed=N rows from the lineups feed. For a late slate whose later games have not posted; batting orders are then projections, not posted lineups")
    ap.add_argument("--fill-max-bo", type=int, default=None,
                    help="fill hitters must bat this high or better (e.g. 6). "
                         "Overrides the variant's own; 0 disables")
    ap.add_argument("--ignore-vegas-adj", action="store_true",
                    help="rank SPs on raw blended/bs instead of the "
                         "Vegas-adjusted adj_blended/adj_bs")
    ap.add_argument("--hitter-min-avg26", type=float, default=None,
                    help="drop fill hitters below this DK points-per-game "
                         "average (e.g. 4.0), i.e. the players more likely to "
                         "score 0 than 10. Overrides the variant's; 0 disables")
    ap.add_argument("--min-total-salary", type=int, default=None,
                    help="reject lineups spending less than this in total "
                         "(e.g. 45000). Overrides the variant's own; "
                         "0 disables. Bends on a slate that cannot meet it")
    ap.add_argument("--seeds", type=int, default=1,
                    help="if the build comes up short of --lineups, rebuild "
                         "under this many consecutive seeds and merge the "
                         "distinct lineups. Dedup and every exposure cap "
                         "carry across the merge")
    ap.add_argument("--fill-floor", type=int, default=None,
                    help="minimum salary for FILL hitters, applied per tier. "
                         "Unlike --hitter-min-salary this exempts the tiers "
                         "named by --fill-floor-exempt (default CONTRARIAN), "
                         "so the contrarian tail keeps its punts")
    ap.add_argument("--fill-floor-exempt", default=None,
                    help="comma-separated tiers exempt from --fill-floor "
                         "(default CONTRARIAN; pass an empty string for none)")
    ap.add_argument("--tier-by-count", action="store_true",
                    help="tag CEILING by COUNT of qualifying specs rather "
                         "than by list position. The positional test delivers "
                         "5.2%% CEILING against a configured 20%%, which also "
                         "halves the 5-stack share")
    ap.add_argument("--all-team-five-max-games", type=int, default=None,
                    help="only guarantee per-team 5-stacks at or below this "
                         "many games (default 4). It measured +8.42 on thin "
                         "cards and NEGATIVE on 5+ game slates")
    ap.add_argument("--all-team-five-per", type=int, default=None,
                    help="how many guaranteed 5-stacks EACH team gets "
                         "(default 1). They are emitted round-robin, so a "
                         "slate that underfills still covers every team")
    ap.add_argument("--cover-min-size", type=int, default=None,
                    help="deep-slate coverage floor: guarantee every team a "
                         "stack of THIS size when the slate is bigger than "
                         "--all-team-five-max-games. 0 disables (default). "
                         "Built 09/08 after TOR and TEX were dropped from a "
                         "10-game card entirely and TEX put up 9 runs")
    ap.add_argument("--cover-min-per", type=int, default=None,
                    help="how many guaranteed coverage stacks each team gets "
                         "on a deep slate (default 1), emitted round-robin")
    ap.add_argument("--allow-same-game-sp", action="store_true",
                    help="let both starters of one game appear in the same "
                         "lineup. Normally banned because opposing arms "
                         "cancel; on 09/10 the winning lineup was exactly "
                         "that pair and we held both arms separately")
    ap.add_argument("--secondary-max-games", type=int, default=None,
                    help="only build a secondary stack at or below this many "
                         "games (0 = always). It measures +2.70 on thin cards "
                         "and negative on deep ones")
    ap.add_argument("--secondary-stack", type=int, default=None,
                    help="contiguous run of N bats from a SECOND team, placed "
                         "before the fill loop. 09/09's three winning lineups "
                         "were all two-team splits (4+3). Measured -6.39 over "
                         "9 slates on 08/30; retested 09/09")
    ap.add_argument("--interleave-max-games", type=int, default=None,
                    help="only interleave attempt order at or below this many "
                         "games (0 = always). Interleaving matters only when "
                         "the build underfills, which is a thin-slate "
                         "condition")
    ap.add_argument("--interleave-attempts", action="store_true",
                    help="walk the spec list round-robin by team instead of "
                         "block by block, WITHOUT changing tier assignment. "
                         "Matters only when the build underfills and the tail "
                         "of the list is never attempted")
    ap.add_argument("--all-team-sizes", default=None,
                    help="comma-separated ladder of guaranteed stack sizes per "
                         "team, e.g. 5,4 -- every team gets a 5-stack, then "
                         "every team gets a 4-stack. Tier follows size "
                         "(5=CEILING, 4=CORE). Overrides --all-team-five-per")
    ap.add_argument("--all-team-five", action="store_true",
                    help="guarantee EVERY team on the slate one 5-stack "
                         "before the normal allocation, overriding the fade. "
                         "Buys coverage of a team-eruption we cannot predict "
                         "(implied_total correlates +0.130 with team output)")
    ap.add_argument("--fade-sp-bs", type=float, default=None,
                    help="hard-fade the offense facing any SP at or above "
                         "this adj_bs (default 55). A fade removes the team "
                         "from EVERY lineup and shrinks the pool, which on a "
                         "thin slate also costs distinct lineups. Raise it to "
                         "fade less; 999 disables the SP-based fade")
    ap.add_argument("--fill-may-oppose", action="store_true",
                    help="allow FILL hitters to face our own SP (the stack "
                         "never may). Widens the construction space at the "
                         "cost of negative correlation in 1-3 slots")
    ap.add_argument("--seed-block", type=int, default=None,
                    help="advance the RNG seed every N lineups instead of "
                         "holding one seed for the whole portfolio (0 = off, "
                         "the historical behaviour). The spec list is "
                         "unchanged, so the team allocation is not thinned")
    ap.add_argument("--hard-avoid-bs", type=float, default=None,
                    help="SP adj_bs below this gets zero exposure (default "
                         "10). Swept over 9 normal slates: 0 cost -5.15 and "
                         "15/20 cost -21.05, so DO NOT change it on a normal "
                         "card. It exists for thin slates, where the ban can "
                         "remove the only legal SP pairs -- on the 2-game "
                         "09/05 night card it cut the portfolio from 7 "
                         "lineups to 2")
    ap.add_argument("--sp-with-stack", action="store_true",
                    help="prefer an SP who plays for the stacked team, "
                         "dropping that lineup's bring-back to allow it")
    ap.add_argument("--hard-min-salary", action="store_true",
                    help="do NOT bend --min-total-salary. The portfolio "
                         "underfills instead of quietly emitting lineups "
                         "below the floor; refill with more seeds")
    ap.add_argument("--hitter-min-own", type=float, default=None,
                    help="drop fill hitters below this PROJECTED-ownership "
                         "percentile (0-100). The blend is batting order + "
                         "implied total + AvgPointsPerGame, rho +0.63 against "
                         "realised %%Drafted over 14 slates. 0 disables")
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
    # "none" is the spelling for the old default of omitting --variant: the
    # unsuffixed filenames and no arm config. Every downstream branch already
    # tests `args.variant` for None, so normalise here and nothing else moves.
    if args.variant == "none":
        args.variant = None
    # build_sp_pool is module-level, so the ban level is a global rather than
    # builder state. Only main() ever writes it.
    if args.hard_avoid_bs is not None:
        global HARD_AVOID_BS
        HARD_AVOID_BS = args.hard_avoid_bs
        print(f"HARD_AVOID_BS overridden to {HARD_AVOID_BS} "
              f"(default 10 -- only justified on a thin slate)")
    if getattr(args, "allow_unconfirmed", False):
        slate_io.set_allow_unconfirmed(True)
    if args.selftest:
        selftest()
        return

    dk, lu, vegas, padj, hcache, opp_map, game_map, slate_date = load_data(args.export)
    n_games = dk["Game Info"].nunique()
    print(f"Slate {slate_date}: {n_games} games, {len(dk)} DK players")

    # Fix #18 RETIRED 09/07. It used to route a <=SMALL_SLATE_GAMES slate
    # entirely through try_build_cash, on evidence from 8/22 that cash lineups
    # "took both cashes" -- i.e. it was justified by 50/50 and double-up
    # results. The user stopped entering those, and the objective has been a
    # top-10 FINISH for weeks. Every design choice in the cash builder trades
    # ceiling for floor: CASH_TEAM_CAP=3 forbids 4- and 5-stacks outright,
    # CASH_MIN_SALARY=3000 bans punts, and the fill takes one of the TOP TWO
    # by avg26, which is near-argmax.
    #
    # Measured on the thin slates in the replay set:
    #   * 09/05 early and night (2 games): cash mode builds ZERO lineups --
    #     CASH_MIN_SPEND=48000 is unreachable once the SP pair bans two of
    #     four teams. Not suboptimal, broken.
    #   * 09/07 (3 games): 30 lineups cash vs 31 GPP, but ALL 36 of the live
    #     cash portfolio were 3-stacks, while top-10 lineups on thin slates
    #     ran 4- and 5-stacks 36% / 50% / 70% / 100% of the time.
    #   * The three top-10 finishes on 09/05 came from portfolios built with
    #     --cash 0, which was passed to work around the 2-game crash rather
    #     than as strategy.
    #
    # Cash lineups are now opt-in only: pass --cash N to build them.
    if args.cash is None:
        args.cash = 0
        if n_games <= SMALL_SLATE_GAMES:
            print(f"  small slate ({n_games} games): GPP build. Cash-style "
                  f"routing retired 09/07 -- pass --cash N to restore it")
        else:
            print(f"  {n_games} games: full GPP build, no cash lineups "
                  f"(Fix #20 — pass --cash N to build them anyway)")

    _cfg0 = VARIANTS.get(args.variant, {}) if args.variant else {}
    no_vegas = bool(args.ignore_vegas_adj or _cfg0.get("ignore_vegas_adj"))
    sp_df, caps, med, feasible = build_sp_pool(dk, lu, padj, opp_map,
                                               args.lineups, no_vegas)
    if no_vegas:
        print("ranking SPs on RAW blended/bs (vegas_adj ignored)")
    if feasible < args.lineups:
        # Recompute the caps against the reduced count once so the ceiling is
        # honest for the portfolio we actually build. One pass only — a very
        # thin arm pool would otherwise ratchet the count down to nothing.
        args.lineups = max(1, feasible)
        args.cash = min(args.cash, args.lineups)
        sp_df, caps, med, _ = build_sp_pool(dk, lu, padj, opp_map,
                                            args.lineups, no_vegas)
    print(f"\nSP pool ({len(sp_df)}), tiered by vegas-adj blended (median {med:.2f}):")
    print(sp_df[["name", "team", "opp", "salary", "adj_bs", "adj_blended", "cap"]]
          .to_string(index=False))

    hit_pool = build_hitter_pool(dk, lu, hcache, opp_map, vegas)
    print(f"\nHitter pool: {len(hit_pool)} confirmed batters")
    report_missing_teams(dk, hit_pool, vegas)

    n_gpp = args.lineups - args.cash
    if n_gpp < 0:
        sys.exit("ERROR: --cash cannot exceed --lineups")
    # _cfg0 is the same dict as `cfg` below; `cfg` is not bound until after
    # the pool is built, and both of these are needed before that.
    _fsb = (_cfg0.get("fade_sp_bs", FADE_SP_BS) if args.fade_sp_bs is None
            else args.fade_sp_bs)
    # Guarantee every team one 5-stack, best offences first so that a slate
    # with fewer entries than teams still covers the likeliest ones.
    _allteams = None
    _tierbycount = bool(_cfg0.get("tier_by_count") or args.tier_by_count)
    _perteam = (_cfg0.get("all_team_five_per", 1) if args.all_team_five_per
                is None else args.all_team_five_per)
    # Guarantee every team a 5-stack ONLY on a thin card. Measured 09/07,
    # 23 slates x 3 seeds, against the shipped minspend49:
    #
    #     depth        pairs   dBest     t     better/worse
    #     <=4 games        9   +8.42  +3.73      7 / 0
    #     5-7 games       24   -4.99  -1.83      9 / 15
    #     8+ games        36   -2.81  -1.18     13 / 20
    #
    # The mechanism is the cost of the guarantee, not its benefit: on a
    # 6-team card covering every team costs 6 lineups and insures the whole
    # slate, while on an 18-team card it costs 18 and spends most of them on
    # offences the allocator rates near the bottom. It loses on deep slates
    # even though it builds 5-13 MORE lineups there.
    _guar_max_games = (_cfg0.get("all_team_five_max_games", SMALL_SLATE_GAMES)
                       if args.all_team_five_max_games is None
                       else args.all_team_five_max_games)
    _guar_size = 5
    _guar_ladder = None
    _interleave = bool(_cfg0.get("interleave_attempts")
                       or args.interleave_attempts)
    # Gate it to thin cards. Interleaving only matters when the build
    # UNDERFILLS -- a deep slate attempts every spec regardless, so reordering
    # changes only who gets first pick of players and exposure headroom. And
    # the measurement says the benefit is confined there: dBest by depth reads
    # thin +1.84 (3 of 4 dates, 0 losses), mid -0.78 (3/3), deep +1.05 (4/4).
    # Same conditional shape as the per-team coverage guarantee, and it makes
    # the arm a strict superset of minspend49cov on any card over N games.
    _ilv_max_games = int(_cfg0.get("interleave_max_games", 0)
                         if args.interleave_max_games is None
                         else args.interleave_max_games)
    if _interleave and _ilv_max_games and n_games > _ilv_max_games:
        _interleave = False
    if _interleave:
        print("attempt order: teams interleaved (tiers still assigned on "
              "block order)")
    _deepcover = False
    _covmin = int(_cfg0.get("cover_min_size", 0) if args.cover_min_size is None
                  else args.cover_min_size)
    if ((_cfg0.get("all_team_five") or args.all_team_five)
            and n_games <= _guar_max_games):
        _byimpl = sorted({h["team"] for h in hit_pool},
                         key=lambda t: -(vegas.loc[t, "implied_total"]
                                         if t in vegas.index else 0.0))
        _allteams = _byimpl
        _gl = (args.all_team_sizes if args.all_team_sizes is not None
               else _cfg0.get("all_team_sizes"))
        if _gl:
            _guar_ladder = [int(x) for x in str(_gl).split(",") if x.strip()]
            print(f"per-team guaranteed stack ladder: {_guar_ladder} "
                  f"x {len(_byimpl)} teams")
    elif _covmin > 0 and n_games > _guar_max_games:
        # DEEP-slate coverage floor. The 5-stack guarantee loses here (-2.81
        # on 8+ games) because it spends one whole lineup per team on the
        # offences the allocator rates last. A 3-stack costs the same lineup
        # but commits three roster spots instead of five, leaving the fill
        # loop free -- the question this sweep exists to answer is whether
        # that is enough to be worth the coverage.
        _byimpl = sorted({h["team"] for h in hit_pool},
                         key=lambda t: -(vegas.loc[t, "implied_total"]
                                         if t in vegas.index else 0.0))
        _allteams = _byimpl
        _guar_size = _covmin
        _deepcover = True
        _perteam = int(_cfg0.get("cover_min_per", 1)
                       if args.cover_min_per is None else args.cover_min_per)
        print(f"\ndeep-slate coverage floor: every team gets "
              f"{_perteam} {_guar_size}-stack ({len(_byimpl)} teams)")
    # The coverage floor SPENDS part of the lineup budget rather than adding
    # to it. make_specs emits one spec per allocated stack regardless of its
    # n_lineups argument, so prepending a guarantee without shrinking the
    # allocation delivers n_teams EXTRA lineups -- which is how the
    # unconditional 5-stack guarantee came to build "5-13 more lineups" on
    # mid and deep slates. Free draws are the single strongest lever in this
    # file (20 -> 40 took the top-10 rate 0.062 -> 0.250), so an arm that
    # quietly buys some cannot be compared against one that does not.
    _alloc_n = n_gpp
    if _deepcover and _allteams:
        _alloc_n = max(1, n_gpp - min(n_gpp, len(_allteams) * _perteam))
    alloc, impl, fades = allocate_stacks(hit_pool, sp_df, vegas, _alloc_n, opp_map,
                                         max_stacks=args.max_stacks,
                                         fade_sp_bs=_fsb)
    print(f"\nHard fades (≤25% appearances): {sorted(fades)}")
    print(f"Primary-stack allocation ({n_gpp} GPP lineups):")
    for t, n in alloc.items():
        print(f"  {t}: {n}  (implied {impl.get(t, float('nan')):.2f})")

    sizes = tuple(int(x) for x in args.stack_sizes.split(","))
    _vcfg = VARIANTS.get(args.variant, {}) if args.variant else {}
    if _vcfg.get("stack_sizes"):
        sizes = _vcfg["stack_sizes"]
        print("max-correlation: stack sizes %s" % (sizes,))
    if len(sizes) != 3:
        sys.exit("--stack-sizes needs three numbers: ceiling,core,contrarian")
    specs = make_specs(alloc, n_gpp, sizes=sizes, all_teams=_allteams, per_team=_perteam,
                       tier_by_count=_tierbycount, guar_size=_guar_size,
                       guar_sizes=_guar_ladder, interleave=_interleave)
    # The DELIVERED tier mix can drift from this, because a spec that cannot
    # build is dropped and the seed refill pulls a different slice of the
    # list. Print both so the two are never confused again: CEILING read 5.2%
    # of the delivered portfolio on 09/08 while configured for 20%.
    _tc = defaultdict(int)
    for s in specs:
        _tc[s["tier"]] += 1
    print("  specs by tier: " + "  ".join(
        f"{k} {v} ({100.0 * v / max(len(specs), 1):.0f}%)"
        for k, v in sorted(_tc.items())))
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
    b.allow_same_game_sp = bool(cfg.get("allow_same_game_sp")
                                or args.allow_same_game_sp)
    if b.allow_same_game_sp:
        print("SP pairs may come from the SAME game (opposing starters)")
    b.secondary_size = int(cfg.get("secondary_stack", 0)
                           if args.secondary_stack is None
                           else args.secondary_stack)
    # Confine it to short cards. Measured 09/09 over 27 slates against
    # minspend49cov, collapsed to dates:
    #
    #     thin <=4   +2.70   4 better / 0 worse
    #     mid  5-7   +1.61   5 / 1
    #     deep 8+    -4.88   3 / 5   (and -30.7 of that is one date, 09_06)
    #
    # The sign flip has the mechanism the 08/30 note already predicted: two
    # 3-man runs need two teams to erupt where one 5-man run needs one, so a
    # split only pays where the pool is too thin to build another good single
    # stack. The old -6.39 verdict was nine slates weighted toward deep cards
    # -- aggregated over the wrong axis, exactly like the per-team guarantee
    # that read -2.81 unconditionally and +8.42 once confined to thin cards.
    _sec_max_games = int(cfg.get("secondary_max_games", 0)
                         if args.secondary_max_games is None
                         else args.secondary_max_games)
    if b.secondary_size and _sec_max_games and n_games > _sec_max_games:
        b.secondary_size = 0
    if b.secondary_size:
        print(f"secondary stack: {b.secondary_size} bats from a second team, "
              f"placed before fills")
    b.fill_floor = int(cfg.get("fill_floor", 0) if args.fill_floor is None
                       else args.fill_floor)
    _ex = (args.fill_floor_exempt if args.fill_floor_exempt is not None
           else cfg.get("fill_floor_exempt", "CONTRARIAN"))
    b.fill_floor_exempt = frozenset(
        t.strip().upper() for t in str(_ex).split(",") if t.strip())
    if b.fill_floor:
        print(f"fill floor {b.fill_floor} on every tier except "
              f"{sorted(b.fill_floor_exempt) or 'none'}")
    mspend = (cfg.get("min_total_salary") if args.min_total_salary is None
              else (args.min_total_salary or None))
    b.min_total_salary = mspend
    b.hard_min_salary = bool(cfg.get("hard_min_salary") or args.hard_min_salary)
    b.sp_with_stack = bool(cfg.get("sp_with_stack") or args.sp_with_stack)
    b.seed_block = (cfg.get("seed_block", 0) if args.seed_block is None
                    else args.seed_block)
    b.fill_may_oppose = bool(cfg.get("fill_may_oppose")
                             or args.fill_may_oppose)
    b.fill_bo_allow = cfg.get("fill_bo_allow")
    havg = (cfg.get("hitter_min_avg26") if args.hitter_min_avg26 is None
            else (args.hitter_min_avg26 or None))
    b.hitter_min_avg26 = havg
    bown = (cfg.get("hitter_min_own") if args.hitter_min_own is None
            else (args.hitter_min_own or None))
    b.hitter_min_own = bown
    fbo = (cfg.get("fill_max_bo") if args.fill_max_bo is None
           else (args.fill_max_bo or None))
    b.fill_max_bo = fbo
    if cfg.get("force_bringback"):
        b.force_bringback = True
        print("max-correlation: bring-back forced on every lineup")
    if fbo:
        print("fill hitters must bat %d or better" % fbo)
    if havg:
        print(f"\nfill hitters need avg26 >= {havg}")
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

    # ---- refill the shortfall under further seeds ----------------------
    # A hard constraint (minspend49) or a thin slate leaves the portfolio
    # short. Every spec has already exhausted 500 attempts on this seed, so
    # retrying the same seed cannot help -- but a different seed walks a
    # different path through the same pool and finds lineups the first pass
    # never constructed. Verified on 09/03 morning: a 3-game card gave 16
    # lineups on one seed and 67 distinct across six.
    #
    # The SAME builder object is reused on purpose, so seen_sigs keeps the
    # portfolio deduped and the exposure counters keep every cap binding
    # across the merge. Caps are fractions of the ORIGINAL --lineups, so a
    # refilled portfolio concentrates no more than a first-pass one.
    # An arm that constrains construction has to say how many seeds it needs
    # to fill, or shipping it means remembering a flag at 6pm.
    n_seeds = args.seeds if args.seeds > 1 else cfg.get("seeds", 1)
    if n_seeds > 1:
        for k in range(1, n_seeds):
            if len(b.lineups) >= args.lineups:
                break
            short = args.lineups - len(b.lineups)
            b.seed = args.seed + k
            # make_specs emits one spec per ALLOCATED stack -- its n_lineups
            # argument only sets the tier proportions -- so it must be sliced
            # to the shortfall or the refill builds a second full portfolio.
            # Rotate the start each pass so successive seeds refill different
            # teams rather than hammering the top of the allocation.
            full = make_specs(alloc, args.lineups, sizes=sizes, all_teams=_allteams, per_team=_perteam,
                              guar_size=_guar_size, guar_sizes=_guar_ladder,
                              interleave=_interleave,
                       tier_by_count=_tierbycount)
            off = (k * short) % max(1, len(full))
            more = (full + full)[off:off + short]
            before = len(b.lineups)
            if cfg.get("score") is None and cfg.get("top", 1) == 1:
                b.build_all(more)
            else:
                b.build_all(more, n_candidates=args.candidates,
                            score=cfg.get("score"), top=cfg.get("top", 1))
            print(f"  seed {args.seed + k}: +{len(b.lineups) - before} "
                  f"lineups (now {len(b.lineups)} of {args.lineups})")
        lineups = b.lineups

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
    if audit(lineups, game_map, b.fill_may_oppose):
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
                      "max_stack": max(tc.values()) if tc else 0,
                      # Projected ownership, the only genuinely forecastable
                      # thing on a slate (rho +0.62 against realised
                      # %Drafted). Recorded, not used to choose anything --
                      # both ways of spending it were measured and failed.
                      # metric_study can score these against future results.
                      "own_mean": round(sum(h.get("own_pct", 0.0)
                                            for h in hit) / len(hit), 1),
                      "own_min": round(min((h.get("own_pct", 0.0)
                                            for h in hit), default=0.0), 1)})
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
