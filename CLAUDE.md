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

**Dollars are not tracked and post_contest.py does not compute them.** The
goal is the best lineup, whichever contest it happened to land in. ROI
measures the contest rather than the build -- the same lineup pays differently
in two fields on the same slate, and the min-cash tail pays for exactly the
mid-pack finishes the portfolio is not built for. 08/29 evening made the point:
-5% ROI against -90% on 08/28 was the contest being small and soft, while the
thing that actually differed was a best lineup 5.1 points off the bar instead
of 48. Report where lineups LANDED: best rank, gap to 10th, top-10 count.

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

**Control has won every live head-to-head as entered. Six arms.**

    slate      control   experiment            winner
    08/26 am      86.8   spend15      77.3     control
    08/26 pm      80.9   spend15      80.8     tie
    08/27        128.8   minspend47  125.3     control
    08/28        127.5   maxcorr47   120.3     control
    08/29 x2      77.1   maxcorr47    71.2     control   (194616721)
                  86.5                82.6     control   (194618891)

The A/B programme is CLOSED -- no more split-arm entries. Build one arm.
Control is that arm: it has the record, and there is now no mechanism to
retire it for anything else. maxcorr47 measured +7.60 on best and better on 8
of 9 slates -- the strongest backtest result of the project -- and lost every
live contest it entered. That does not reopen.

The 08/29 numbers were restated on 08/30 against the full standings exports;
the old ones (71.4/69.2 vs 66.0/67.4) were means of `control_live` (2 entries)
and `maxcorr47_live` (1 entry), sub-slices rather than contests. The winner
column did not move. Both 08/29 head-to-heads were on the 12-game DAY slate;
the evening 3-game contest (194617697) was control-only and is not a
comparison.

**But control wins 08/29 on hand swaps, not on builder output.** Split the
entries by whether they hash exactly to a build or were late-swapped:

    contest      control (exact)   maxcorr47 (exact)   builder winner
    194616721    71.27 / 90.55     67.79 / 85.75       control
    194618891    69.42 / 73.75     83.86 / 113.30      maxcorr47

Control's two best entries of the day -- 135.75 and 154.95, the only two
things all week that finished top 10% -- were BOTH swapped by hand after the
build. The 154.95 was `control_prev0928` with Petey Halpin -> Pete
Crow-Armstrong, who scored 53.0. On what the builder actually emitted, 08/29
is 1-1. As entered it is 2-0 control, and as entered is what scored, so the
table above stands -- but do not read it as the builder beating maxcorr47.

**Score arms from the standings export, never from as-built subsets.** Match
entries back with `lineup_id`; an exact 10/10 hash is the builder's own work
and an 8-or-9-of-10 nearest-neighbour match is a hand-swapped entry. Report
both -- collapsing them is what made 08/29 look like a builder win.

**Process every standings export you download.** On 08/30 the folder held five
unprocessed contests (08/28 x2, 08/29 x3), not the two that had just been
added, and the two oldest of those were carrying the 08/28 row above. All 40
of the 08/28 entries hash exactly to `DK_upload_08_28_2026_minspend47.csv` and
there is no 08/28 control build anywhere on disk -- so whatever that row
describes, it is not contests 194551192 or 194552963. Treat it as unverified.

**What actually has live evidence:** entering BEFORE first pitch (on 08/29 the
pre-existing entries beat late-swap builds by ~23 pts/lineup, essentially all
of it points from games that had already locked), and volume (20 -> 40 lineups
took the top-10 hit rate 0.062 -> 0.250).

## Daily flow

    python preflight.py                  # gate: are lineups posted?
    python filtered_DK_Salaries.py       # match the two load/ downloads
    python mlb_odds.py --csv mlb_odds.csv
    python vegas_sp_adjust.py
    python build_portfolio.py --variant control --lineups 60
    python make_entries.py --arms control [--duplicates]

60 fills on a real slate; verified against frozen snapshots rather than
assumed. 08/28 (12 games) built 59 of 60 -- one ceiling lineup could not be
made unique -- and 08/27 (5 games) built 60 of 60. Both passed the audit with
no duplicate lineups and PAIR_CAP respected. The exposure caps are fractions
of `--lineups`, so 60 concentrates no more than 20 did: 08/28's top arm sat at
24/60, the same 40% ABSOLUTE_SP_CAP binds at any size.

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

**On a thin slate every fractional cap stops binding, and re-capping does not
fix it.** The caps are shares of the REQUESTED count, so when the slate
delivers a fraction of the request they are sized to a portfolio that never
existed. On 08/29 evening the ask reduced to 21, `cash_hitter_cap` came out at
`round(0.60 * 21) = 13`, ten lineups survived, and 13 > 10 means no hitter
could reach it. Three players ran at 10 of 10 -- Ryan Waldschmidt was one and
scored 0.0, in a contest whose 10th place was 5.1 points above our best entry.

Sizing the cap to the delivered count was implemented and measured. It does
not work: rebuilding 08/29 evening against 10 gives a cap of 6, only 5 lineups
survive, 6 > 5, and the same players are still at 100% on half the portfolio.
The change was reverted. The cap is not the mechanism.

The mechanism is `try_build_cash`. With 3 games it has 4 eligible teams after
banning the two its own SPs oppose, and it fills each slot by sorting
candidates on `avg26` and taking one of the top TWO. That is near-argmax, so
it lands on the same bats every lineup: 54 confirmed batters in the pool, 16
hitters in the shipped portfolio. Widening that sampling is the only lever
that would work -- and there is no longer an A/B channel to test it on, so it
stays untouched. Treat 100% exposure on a <=4-game card as a property of the
slate, and size the entry accordingly.

Asking for MORE lineups delivers more on a thin slate, not fewer: 08/29
evening built 2 at `--lineups 10`, 5 at 20, and 10 at 60, because both the
feasibility reduction and the caps scale off the request.

## Scripts are Linux-clean

`build_portfolio.py` is stdlib + pandas + `lineup_id`; nothing Windows-only.
The `G:\` paths are argparse defaults, overridable with `--export`. Keep it
that way so the harness can drive it from anywhere.
