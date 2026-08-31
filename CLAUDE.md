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

**Tightening a cap is NOT removing a bad lineup. Measured and reverted
(08/30).** The idea: FILL_CAP counted only non-stack appearances, so a bat
could pass 20% by arriving as a stack member part of the time -- on 08/30
Canzone, Dubon, Romo and Peters each reached 22-27% of 60 lineups and scored
0, 5, 3 and 3. Six cheap bats appeared to be setting the floor of the whole
portfolio. The fix counted every appearance and tested the cap when picking
fills. Replayed over 4 slates against realised player scores:

    slate      min            bottom10       best           over-cap
    08/27      51.45 -> 31.45  60.31 -> 59.09  172.75 -> same  15 -> 7
    08/28      37.40 -> same   54.85 -> 55.15  152.05 -> same   7 -> 8
    08/29 day  33.65 -> 33.25  48.81 -> 47.91  195.90 -> same   5 -> 7
    08/30      31.05 -> 25.05  54.09 -> 51.33  160.45 -> 156.45  7 -> 1

(Four slates only, so provisional by the standard set below -- but it failed
on its own terms, which no wider set would rescue.) It made the FLOOR WORSE
on 3 of 4 -- the one thing it existed to fix -- was
neutral-to-negative on `best`, and did not even reduce concentration reliably
(squeezing fills just pushes it into the stack windows, which stay exempt
because a stack is a contiguous batting-order run). The only thing it improved
was the mean, positive on all four, which is exactly the proxy that shipped
`spend15`.

The mechanism was misread. Those bats do not repeat because of a loophole;
they repeat because they are the best value at their price. Blocking the
most-used fill does not delete a bad player, it forces the builder down to a
worse one -- more names, lower quality per slot. And the read itself was
selection bias: on the same slate, from the same pool, Duran at 22% scored
25.0 and Freeman at 23% scored 12.0. Six coin flips, and only the losing side
looked like a structural flaw. Do not re-propose an exposure cap as a floor
fix.

**Do not add a total-salary floor either.** On 08/30 a 47,000 cut split the
portfolio 93.45 mean / 146.85 best against 93.15 / 160.45 -- indistinguishable
on the mean, and the floor DELETES the best lineup of the day, which cost
46,500. Pooled over the older paired slates, spend>=47k lost the mean in 5 of
6 files by an average of 21.3 pts/lineup. This is the same direction as the
SP-spend finding below; unspent salary is not the defect.

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

**08/30 came within 0.50 of the objective, and placement cost it.** 60 control
lineups over 3 contests. Best lineup 160.45, rank 11 of 1,094, against a
10th-place score of 160.95. The same lineup would have finished TOP TEN in
either of the other two contests that day (bars 160.30 and 159.95) -- it went
into the one with the highest bar. Gaps by contest: -0.50, -13.45, -38.50,
against a previous project best of -5.10 and a typical -41 to -56.

The distribution moved too, not just the headline: half the entries finished
below their contest median (08/28-29 ran 59%), 10.0% landed in the top 5-10%
band (was 3.0%), and 13.3% in the top 10-20% (was 5.0%). First slate where the
SHAPE improved rather than one lineup spiking. Still 0 top-10 finishes in 8
contests.

**Why #1 is not a ranking problem, from 08/30.** The winners scored 179-191.
Our 160.45 had NO spike -- its best player was 27.6 -- and ranked 11th purely
by having no busts, worst four players summing +25 with no zeros. Meanwhile
the bottom ten lineups summed -0.6 across their worst four and averaged 3.0
zeros or negatives. `worst4` is what separates the portfolio, not `top3`.

And we DID stack the team that erupted. NYY put five men in the slate's top
eleven (Caballero 28.0, Warren 27.6, Goldschmidt 26.0, Chisholm 26.0, Ramos
25.0); we had 4 lineups with 3+ NYY bats, one with 5, and the best of them
made 146.85. Picking the right TEAM was not the problem -- you needed the
right five of nine, owned 1.2 / 3.9 / 2.7 / 2.0 / 1.7 percent. NYY also had
the second-LOWEST implied total on the card, so concentrating on high-implied
teams would have missed it entirely.

The three best pitching scores came from arms ranked 9th, 11th and 14th of 14
by adj_blended (Mahle 28.0, Matthews 27.8, Scherzer 38.5 -- Scherzer banned
outright by HARD_AVOID_BS), while Framber Valdez, the LOWEST adj_bs in the
pool, scored a healthy 13.3. And `metric_study` over 14 paired slates now puts
every metric between -0.17 and -0.06 within-slate: floor_target, proj_points,
ceiling, max_stack, Salary. Nothing we compute ranks lineups. #1 is a lottery
over which two or three sub-3%-owned players erupt; more draws is the only
lever that touches it.

**What actually has live evidence:** entering BEFORE first pitch (on 08/29 the
pre-existing entries beat late-swap builds by ~23 pts/lineup, essentially all
of it points from games that had already locked), and volume (20 -> 40 lineups
took the top-10 hit rate 0.062 -> 0.250).

## The replay harness, and the 08/30 sweep it ran

Since the A/B channel is closed, the only test left is REPLAY: rebuild a
frozen snapshot, then score every lineup against the player FPTS in that
slate's standings export. That measures the build directly instead of through
a contest field. Nine snapshots pair to a standings file (08/23, 08/24, 08/25,
08/26, 08/27, 08/28, 08/29 evening, 08/29 day, 08/30); the pairing is by SP
name overlap. Score on `best`.

**USE ALL NINE. Four slates produced a false positive on 08/30.** A floor of
`--hitter-min-avg26 4.0` measured +12.75 on `best` over the four newest
slates and -20.50 over all nine (better on 2, worse on 6, tied 1). The two it
won were the two recent slates that made up half the small set -- the exact
failure CLAUDE.md already warns about, reproduced in an afternoon. Anything
scored on fewer than nine is provisional and should say so.

Everything tried on 08/30, all reverted, all scored on `best`:

    change                                slates   avg delta   verdict
    any-N stack (ignore batting order)      9       -20.49     confirms current
    5,5,4 bigger primary stacks             4       -26.00     no
    secondary 3-man stack                   9        -6.39     no
    window ranked by batting order          4       -16.50     no
    hitter floor avg26 >= 4.0               9        -2.28     no
    hitter floor avg26 >= 6.0               4       -21.71     no
    hitter min-salary 3000 / 3500           4    -10.03/-18.54 no
    HARD_AVOID_BS 0 / 5 / 15 / 20           9   -5.15/0/-21.05/-21.05  keep 10

**The consecutive stack is the one confirmed positive, and it is large.**
Replacing contiguous batting-order windows with "any N off the team, best bs
first" cost 20.49 points of `best` per slate, worse on 8 of 9 and better on
none. It also stopped filling the portfolio (38-57 lineups instead of 59-60),
because a team's best-rated bats keep colliding on position, and it broke the
audit until deduped -- the contiguous version gets dedup free from its
batting-order dict. Correlation, position spread and dedup out of one
construction. Do not touch `stack_windows`.

**A 6-man stack is ILLEGAL.** `--stack-sizes 6,5,4` builds and then the audit
refuses to write it: DK caps a Classic roster at 5 hitters from one team. Any
"more concentration" idea is bounded there, and 5,5,4 is worse than 5,4,3.

**The ceiling comes from maximum concentration on ONE team.** That is why the
secondary stack fails: two 3-man runs need two teams to erupt, one 5-man run
needs one. 08/30 is the illustration -- NYY put five men in the slate's top
eleven and only a heavy NYY lineup could have caught it.

**`HARD_AVOID_BS` is not what excludes weak arms.** Disabling it entirely
(-999) did NOT roster Max Scherzer on 08/30 despite his 38.5, the slate's top
score, and `best` did not move. `pick_sp_pair` prefers adj_blended >= median
for ceiling/core and 10 <= adj_bs < 40 for contrarian; at adj_bs -2.00 he
fails both bands and loses the fallback pool to 17 better-rated arms. The ban
is still mildly justified on its own terms -- banned arms average 10.91
pts/start against 13.42 for eligible ones over ~10 slates -- but a banned arm
was the single best arm on the slate 2 times in 10. Leave it at 10; 5 is
indistinguishable, 15 and 20 cost 21 points.

## What top-10 lineups look like, and why we cannot copy them

190 top-10 lineups against 58,706 field lineups and 415 of ours, over 21
contests:

                        TOP-10    FIELD     OURS
    5-stack share        71.1%    53.8%    25.5%
    max stack             4.65     4.18     3.95
    teams used            4.31     4.95     5.58
    ownership sum        119.8    112.6     97.8
    sub-5%-owned          3.47     3.77     4.90
    SP salary           17,241   17,470   17,235   <- we match
    hitter salary       32,356   32,158   30,398

Stack size is not survivorship. P(top 10) across the whole field rises
monotonically with it: 0.137% / 0.193% / 0.322% / 0.436% for a 2/3/4/5-stack,
and the mean rises too.

**And yet every change pointed at that profile made things worse.** Force
5-stacks -11.89, force 5,5,4 -14.54, hitter salary floor -10.03/-18.54, rank
windows by avg26 -12.82, rank windows by batting order -16.50, allocate purely
on Vegas implied -2.59. The reason is always the same: the constraint gets
satisfied using OUR ranking, and our ranking is `bs`, which predicts nothing.
Winners have five bats on the RIGHT team. We can force five bats; we cannot
pick the team. The gap is team selection, and the only signal that predicts
team output (implied_total, +0.167) is already in `allocate_stacks`.

**Ownership is the one forecastable thing on a slate, and using it still did
not help.** `build_hitter_pool` now computes `own_pct`, a 0-100
projected-ownership percentile from batting order + implied total +
AvgPointsPerGame, z-scored within slate and equally weighted. Against realised
%Drafted over 9 slates and 1,375 player-slates: rho +0.622 mean, worst +0.493,
no sign flips. Adding salary or avg26 made it worse. For scale, every metric
in metric_study predicts realised POINTS between -0.17 and -0.06.

Both ways of spending it failed:

    --hitter-min-own 10 / 20 / 30   avg best  -8.05 / -12.21 / -9.68
    barbell, >=2 / >=3 chalk bats   avg best  -4.41 / +0.84

The floor failed because winners DO NOT avoid low-owned players. Their three
lowest-owned sum to 10.61 against the field's 10.05, and they carry 2.05
sub-3%-owned bats against the field's 2.15 -- statistically the same. Their
whole ownership edge is at the TOP: three highest-owned sum 73.39 vs 68.48.
The old `own_min` study (+0.126, p=0.015) measured a correlation, not a lever;
winners' minimum owned player is 2.13% and ours is 1.96%. 08/30 is the proof
-- the NYY eruption that won it was Caballero 1.2%, Chisholm 2.0%,
Goldschmidt 2.7%, Ramos 1.7%, and any ownership floor bans all four.

The barbell aimed at the right end and still landed at +0.84 per slate, 2-2
with 5 slates unchanged -- a null by the bar set above. `own_pct` is kept and
written into portfolio_summary as `own_mean` / `own_min` so metric_study can
score it against future results, exactly as floor_target is kept. It gates
nothing. `--hitter-min-own` exists, defaults off, and is documented here as
tested and failed.

**Every constraint tightening trades floor for ceiling.** Tighter fill caps,
bigger stacks, hitter floors, a higher SP ban -- each one lifts the bottom of
the portfolio and shortens the top. Seven ideas, seven dead, and this is the
shape of all of them. `best` is the objective, so the trade is always the
wrong way round.

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

It deals lineups ROUND-ROBIN across contests. It used to fill one contest at
a time, which handed each a contiguous block of a portfolio ordered CEILING ->
CORE -> CONTRARIAN -- so the tiers came out sorted by contest. On 08/30 that
put all twelve contrarian lineups in one contest (which finished 38.5 off its
bar) while another got 20 straight CORE. Interleaving gives every contest the
same tier mix and assumes nothing about how many contests there are or whether
they hold equal numbers of entries.

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
