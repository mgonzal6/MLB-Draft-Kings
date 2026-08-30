# Working notes

## Do the work locally

Prefer running things in-session over handing back instructions. If a script
can be executed, a schema checked, or a result verified here, do it rather
than describing how. Reach for connectors, uploads, or "run this on your
machine" only after the local route is actually exhausted -- and say which
route failed and why.

`build_portfolio.py --selftest` runs the audit logic with no slate data and no
file writes. It is the cheapest local check that the builder still works;
run it after touching the builder or the audit rules.

## Where the data lives, and what that means in a cloud session

The pipeline reads and writes `G:\My Drive\DK\...` (Google Drive File Stream):

    export/     current slate inputs and outputs
    Snapshots/  frozen per-slate inputs, one dir per slate date
    Post Contest/  contest-standings exports, post_entries, backtests

None of it is in the repo -- `.gitignore` covers the CSVs deliberately, and
none has ever been committed. A Claude Code web/cloud session runs in a Linux
container with no line to that drive, and (as of 2026-08) `drive.google.com`
is refused by the environment's egress policy, while the Google Drive
connector exposes only write tools (`update_file`, `share_file`,
`trash_file`) -- no search or read.

So anything data-dependent -- `backtest_variants.py`, `metric_study.py`,
`post_contest.py` -- cannot run in a cloud session unless the files are
attached to the conversation. Code changes, selftests, and synthetic-fixture
tests all run fine. Say which of those two categories a task falls into
before starting it, rather than discovering it at the end.

## What has been measured, so it is not re-derived

Roughly 2,000 backtest builds over 9 slates (08/23-08/28) and 12 contests.
Read this before proposing a variant.

**The objective is a top-10 FINISH, not top 10% and not mean.** One lineup
reaching the top handful is what pays. Score arms on `best`, the gap to the
real 10th-place score, and the top-10 hit rate. Mean points per lineup is a
proxy that has actively misled -- `spend15` looked like +10.60 (p=0.056) on
the mean and lost on all three live slates.

**Selection compresses spread, always.** Per-portfolio sd: 27.20 with no
scoring, 25.86 with a score at top=5, 25.19 with score plus floor. Choosing
the best of N pulls lineups toward the same players. So `n_candidates=1` is
already the variance-MAXIMISING setting and no scoring function can lengthen
the right tail. Four arms died this way: ceiling, boom, value_boom,
spendboom. Do not add a fifth.

**Arms that remove bad lineups help; arms that select good ones do not.**
That is the whole pattern. `minspend47` (a floor on total salary) is the only
arm to lift `best` without costing spread. Every arm that leaned on `bs` to
pick winners failed -- because:

**`bs` does not predict anything.** Against realised FPTS, hitters: Salary
+0.111, AvgPointsPerGame +0.104, avg26 +0.104, `bs` +0.047 (p=0.26, sign
flips across slates). Residualised on salary, `bs` adds **-0.033** for
hitters and **-0.198** for pitchers. Our custom ranking carries nothing the
price tag does not already contain. Everything downstream of it is
rearrangement.

**Fixing a broken input was worth about a point.** vegas_adj demonstrably
corrupts pitcher ranking (-0.179 paired, negative on 9 of 9 slates), and
removing it moved portfolio results +1. Do not assume better projections will
fix the results; daily baseball is mostly noise and r ~ 0.11 is what the
market itself achieves.

**Vegas is good on the hitter side, bad on the pitcher side.** implied_total
predicts team hitter output at +0.167, beating avg26 and salary, and the fade
/ stack / bring-back rules all fire correctly. Leave that alone.

**Portfolio size beats arm choice.** 20 -> 40 lineups took the top-10 hit rate
0.062 -> 0.250 and best rank 76 -> 29, improving on every slate tested. That
is roughly twice what the best arm does. It does not fill on thin slates
though -- 34 of 40 on a 10-team card, where PAIR_CAP=3 and the SP exposure
caps bind.

**Slate size moves the bar more than anything we build.** Team count against
the 10th-place score is strongly negative (a 10-team card needed 205.9 on
08/27; 24-team cards have needed 134.7). Slate selection dominates
construction. The relationship is looser than it first looked -- 08/28 was a
24-team card and still needed 160 -- so treat it as directional.

**Backtests here systematically flatter variants.** Arms get tuned on the same
handful of contests they are then scored against, and each run tests ~24
hypotheses so one p<0.05 is what chance produces. Nothing should ship on a
backtest alone; new contests are the only real holdout.

**Control has won every live head-to-head. Six arms and counting.**

    slate      control   experiment            winner
    08/26 am      86.8   spend15      77.3     control
    08/26 pm      80.9   spend15      80.8     tie
    08/27        128.8   minspend47  125.3     control
    08/28        127.5   maxcorr47   120.3     control
    08/29 x2      71.4   maxcorr47    66.0     control
                  69.2                67.4     control

maxcorr47 measured +7.60 on best and better on 8 of 9 slates -- the strongest
backtest result of the project -- and lost all three live contests. Default to
running control until something survives on slates it was not scored against.

**What actually has live evidence:** entering BEFORE first pitch (on 08/29 the
pre-existing entries beat late-swap builds by ~23 pts/lineup, essentially all
of it points from games that had already locked), and volume (20 -> 40 lineups
took the top-10 hit rate 0.062 -> 0.250).

## Daily flow

    python preflight.py                  # gate: are lineups posted?
    python filtered_DK_Salaries.py       # match the two load/ downloads
    python mlb_odds.py --csv mlb_odds.csv
    python vegas_sp_adjust.py
    python build_portfolio.py --variant control --lineups 40
    python make_entries.py --arms control [--duplicates]

`make_entries.py` writes lineups into DK's entries (late-swap) CSV. It never
touches a row containing a (LOCKED) player, takes the NEWEST matching upload
(two slates can share a date -- picking a stale one silently entered an
afternoon portfolio into evening contests), and verifies before writing.

Add `--allow-unconfirmed` (build_portfolio, vegas_sp_adjust) or
DK_ALLOW_UNCONFIRMED=1 (preflight, validate_upload) when the later games have
not posted. Check whether it is actually needed first: on 08/29 the slate was
already fully confirmed via the OAK->ATH remap and the flag made the build
worse, dropping it from 10 lineups to 4.

**Thin slates cap the portfolio.** A 3-game card had 3 of 8 pitchers clear
HARD_AVOID_BS=10, covering 2 of 3 games, so the builder capped itself at 10
lineups rather than over-concentrate. That is correct behaviour. Filling 40
entries there means duplicating, which pays independently but adds no
coverage.

## Scripts are Linux-clean

`build_portfolio.py` is stdlib + pandas + `lineup_id`; nothing Windows-only.
The `G:\` paths are argparse defaults, overridable with `--export`. Keep it
that way so the harness can drive it from anywhere.
