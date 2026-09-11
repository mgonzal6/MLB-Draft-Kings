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

**`bs` is worse than its own ingredients, and 57% of it is noise.** Rerun on
09/02 over 18 slate-files, confirmed hitters, within-slate Spearman against
realised FPTS:

    metric     mean    worst    positive on
    Salary    +0.152   +0.020   18 of 18
    avg26     +0.127   +0.036   18 of 18
    hr_pg     +0.103   -0.032   16 of 18
    ceiling   +0.091   -0.053   15 of 18
    l3_avg    +0.090   -0.058   12 of 18
    bs        +0.079   -0.075   13 of 18

Every component beats the composite, and only Salary and avg26 never go
negative. Decomposing `bs` on 08/31's 108 confirmed hitters, by share of
spread: form (+-15) 33%, yoy x4 (+-20) 24%, the avg26 base 20%, hr_pg 12%,
ceiling 6%, bb% 3%, sb 2%. The two noisiest terms -- a three-game hot/cold
flag and a year-over-year delta -- are 57% of the score and the season
average is a fifth of it. The caps are not the problem: 0 of 108 hitters
exceeded `avg26 * 2.8 = 35`.

**Replacing it in the fill sort still loses.** The fill sort is `bs / salary`
and it picks every non-stack hitter, so it is the one place per-player
accuracy should matter. Swapping to `avg26 / salary` over the ten-slate
replay: +18.00, +4.00, +3.40, +1.05, 0.00, -13.00, -15.40, -16.50, -20.45,
-39.00 -- better on 4, worse on 5, mean **-7.79**.

And not because it concentrated the portfolio; it did the opposite (distinct
players 116->119, 79->82, 148->161, 111->116). The better metric picked more
different bats and still scored less. The reason is in the residuals above:
once salary is controlled, avg26 adds -0.014 and bs -0.001, so both are only
re-expressing price -- and the sort divides by salary, cancelling the one real
signal either carries. What remains is arbitrary, and `bs`'s arbitrariness
happens to suit these ten slates.

So: `bs` is a bad metric AND replacing it does not help. Do not read the first
half as a reason to try the second. This slot is not where the leverage is.

**Fixing a broken input was worth about a point.** vegas_adj demonstrably
corrupts pitcher ranking (-0.179 paired, negative on 9 of 9 slates), and
removing it moved portfolio results +1. Do not assume better projections will
fix the results; daily baseball is mostly noise and r ~ 0.11 is what the
market itself achieves.

**Vegas is good on the hitter side, bad on the pitcher side.** implied_total
predicts team hitter output at +0.167, beating avg26 and salary, and the fade
/ stack / bring-back rules all fire correctly. Leave that alone. (The
bring-back's TARGETING was broken until 09/02 -- see below -- but the rule
about when to take one is sound.)

**The bring-back could kill a whole team's allocation silently. Fixed
09/02.** `BRINGBACK_TOTAL` is 8.0, and the bring-back picks the opposing
team's best bat by `bs` ALONE -- no salary check, no feasibility check. On an
expensive stack that bankrupts the lineup before the fill loop starts. 09/02
evening: MIL had the joint-highest implied total, a game total of 8.5, and CHC
opposite. A 5-man MIL window costs 23,200, the top CHC bat by bs is Pete
Crow-Armstrong at 7,000, a median SP pair is 16,900 -- 47,100 spent with two
slots left and 2,900 to fill them against a 2,000 minimum apiece.

    MIL specs alone            built  9 of 17   4,114 no-valid-construction
    MIL, bring-back disabled   built 17 of 17     145
    MIN alone (control)        built 17 of 17     169
    HOU alone (control)        built 17 of 17     220

MIL runs SECOND in spec order, so this was never exhaustion. In the real build
MIL delivered 0 of 17 and the portfolio contained no MIL stack at all -- and
because a team's bats can otherwise only arrive through the fill loop's
`bs/salary` sort, which ranks expensive bats worst, Jackson Chourio (leadoff,
2nd-best bs on the team, 99th percentile projected ownership, 27.0 points, in
73% of the top 1%) appeared in NONE of our 120 lineups.

Two changes: the bring-back now has to leave the remaining slots affordable,
and if a spec still cannot build, it retries once without the bring-back
rather than dropping the lineup -- the same bend-do-not-drop pattern the
min-spend ladder already uses. Tonight that took the build from 103 to 113
lineups, 17 skips to 7, MIL from 0 stacks to 17.

**It does NOT improve `best`, and was shipped anyway.** Over the ten-slate
replay: 08/24 +12.00, 08/28 -17.10, 08/31 -13.70, seven slates unchanged, mean
-1.88. It fires on 3 of 10 slates and is a coin flip when it does, because the
recovered lineups shift the RNG path and exposure counters for every later
spec, so the whole portfolio changes rather than baseline-plus-extras -- the
measurement is portfolio churn, not the fix's own effect. It ships because it
is a correctness defect, not a theory about what wins: the builder was
allocating 17 lineups to the best offence on the slate, delivering none, and
reporting it in a log line ("no unique valid lineup for MIL") that reads like
exhaustion. Do not re-litigate it on `best` alone.

**Weather is already inside the Vegas number. Do not add it.** The lineups
feed carries a weather string per team ("65 0 OUT CFLF 3-5 0% H71%outdoor" =
temp, wind direction and speed, rain %, humidity, dome flag) and nothing reads
it. It looks powerful if you cut it at the tails -- over 185 outdoor
team-slates, strong wind OUT (>=8mph) averaged 73.17 top-9 team FPTS against
60.74 for strong wind IN, and games at 80F+ averaged 69.74 against 56.04 at
65F or below. Both gaps are artifacts of comparing extremes. On the
continuum:

    metric                raw    after removing implied_total
    implied_total       +0.099        --
    temperature         +0.027      +0.016
    wind (OUT +, IN -)  +0.017      +0.013

Noise, and what little is there the market has already priced. This is the
same shape as the ownership detour: a real physical mechanism that the line
absorbs before we see it.

**DK's own `Starting` column is a second opinion on who is playing, and it is
not read.** Filtered_DKSalaries.csv carries `Starting` (the batting order per
DK, or blank) and `Status` (blank on every slate checked -- no injury data).
`Starting` disagrees with the lineups feed often enough to matter: on 09/02
evening Jose Fermin was confirmed=Y in the feed and blank in `Starting`. He
stayed out of the portfolio only because LAA happened to be faded. Cross-
checking the two would automate the scratch calls that have been made by hand
(Adael Amador 09/01, 21 of 80 lineups; Max Kepler 08/31, 4 of 142). Not built
-- judged too rare to be worth the code -- but the column is there when a
scratch does slip through.

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
maxcorr47 measured +7.60 on best and better on 8 of 9 slates -- the strongest
backtest result at the time -- and lost every live contest it entered. That
does not reopen.

**Control was that one arm until 09/04, when `minspend49` replaced it.** The
mechanism for retiring control turned out to be the replay harness rather
than a live split: equal portfolio size, all slates, scored on `best`, better
on 10 of 11 slate dates at t 3.53. Note what that means for this table -- the
live head-to-head record below belongs to a builder that is no longer the
shipping one. `minspend49` has ONE live slate, 09/04 -- it beat control by
+7.30 on 5 seeds and took the only top-10 available, on a night the portfolio
was otherwise poor. One slate settles nothing either way.

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

**THE FIRST TOP-10 FINISHES CAME FROM THE 3-GAME MORNING CARD, 09/03.** The
portfolio flagged as a concentration risk -- Hunter Brown in 61 of 61 lineups,
25 distinct hitters of 54 available, built by merging six seeds because one
seed gave only 16 -- entered contest 194931967 (237 entries) and finished
**rank 2 of 237, with 2 of its 7 entries there inside the top 10**, gap
+12.00. Best lineup 107.20 against a 10th-place score of 95.20.

That settles an open question the file had been holding: "treat 100% exposure
on a <=4-game card as a property of the slate, and size the entry
accordingly" was the right call, and accepting the concentration rather than
relaxing `PAIR_FLOOR_PCT` to manufacture a Brown-free pair is what won. On a
3-game card there are only three legal SP games; a floor that forces variety
buys worse arms. Hunter Brown scored 22.7 and was 63.3% owned -- the chalk was
correct and being 100% on it was correct.

Only 7 of the 61 morning entries are in this export. The other 54 went to
contests whose standings have not been pulled.

**09/03 evening, on FINAL standings.** 76 control lineups over 4 contests on
a 6-game card, 76 of 76 built, 81 distinct hitters, top SP 34%.

    contest      n    best     rank        10th     gap
    194932492   20   136.05    22/1189    144.85   -8.80
    194996367   20   136.50    45/1189    154.75  -18.25
    194932491   20   124.30    79/1169    147.50  -23.20
    194946071   16   129.75   455/7134    168.35  -38.60

All 76: best 136.50, mean 86.40, worst 36.35. Distribution 51.3% below
contest median, 2.6% in the top 1-5% band, 7.9% in 5-10% -- worse than
08/30's 10.0% and 13.3%. 0 top-10 in the evening contests.

**The first exports of these contests were INCOMPLETE and every number read
off them was wrong.** Pulled at 21:50 they gave 10th-place bars of 139.80 /
149.45 / 160.05 and ranks of 43 / 23 / 259; the final exports at 23:01 gave
147.50 / 154.75 / 168.35 and ranks of 79 / 45 / 455. Player FPTS moved too,
so rebuilt-portfolio scores changed, not just ranks. The preliminary files
were also LARGER on disk than the final ones, so file size is not the tell.
Do not analyse a standings export until the slate is genuinely complete, and
re-pull before trusting any number taken from one.

**09/04: FIRST LIVE `minspend49` SLATE. It beat control and the night was
still poor.** 12 games / 24 teams, 140 lineups over 7 contests, 140 of 140
built, salary mean 49,509. Exports pulled 01:22 and NOT yet re-pulled, so by
the rule directly above these numbers are provisional.

    contest       n    best     rank        10th      gap
    194990692    20   148.45      8/890    143.20    +5.25   <- top 10
    194990639    20   122.85     78/1189   144.85   -22.00
    194990693    20   126.05     68/952    152.35   -26.30
    194990641    20   132.55    143/3567   161.80   -29.25
    194990640    20   127.55    198/3567   158.80   -31.25
    194990694    20   113.75    225/1430   150.35   -36.60
    194992516    20   119.55    748/7134   163.00   -43.45

All 140: best 148.45, mean 86.47, worst 45.35. **65.7% of entries finished
below their contest median -- the worst distribution recorded** (09/03 was
51.3%, 08/30 50%). Only 2 of 140 reached the top 5%. One top-10 finish, and
it needed the softest bar on the card: 148.45 would have missed in all six
other contests.

Rebuilt control on the SAME snapshot (scratches already removed), 5 seeds,
140 lineups each:

    seed     control   minspend49   delta
      42      134.05      148.45   +14.40
     100      136.60      129.45    -7.15
     200      141.00      141.00    +0.00
     300      127.80      138.30   +10.50
     400      123.40      142.15   +18.75

    control mean 132.57 sd 7.01   minspend49 mean 139.87 sd 6.91
    paired +7.30  SE 4.76  t 1.53  (3 better, 1 worse, 1 tied)
    top-10: control 0 on ALL FIVE seeds, minspend49 2

Larger than the +3.08 backtest and still not significant, which is what a
5-seed single slate can say. The decisive column is top-10: control's best in
ANY seed was 141.00, below the 143.20 bar of the only winnable contest, so
control goes 0-for-7 on this slate from every draw. The finish is attributable
to the arm, not to which contest a lineup happened to land in.

**Spread did NOT collapse -- that first read was a cross-slate error.**
Tonight's portfolio sd of 18.16 was compared against 09/03's 23.71, which
measures slate difficulty, not the arm. Within this slate against control the
realised per-portfolio sd is 18.86 control vs 19.30 minspend49, averaged over
the same five seeds: spread went UP, the same direction as the replay's
26.4 -> 27.2. When checking whether an arm compresses spread, compare arms on
one slate, never one slate to another.

**Two hitters were scratched by hand and the feed never caught up.** Patrick
Bailey and Nathaniel Lowe (both CLE, game 1 of a doubleheader) were still
`confirmed=Y` in the 16:00 lineups download, and were in 8 roster spots of the
16:03 build. Removed from `Filtered_Lineups.csv` by hand -- all three rows,
including Bailey's unconfirmed game-2 row, so the doubleheader dedup could not
reintroduce him. That is the THIRD hand scratch in five days (Kepler 08/31,
Amador 09/01, these two 09/04), each time with the feed wrong and the user
right. The `Starting` column cross-check described above was judged too rare
to build; three occurrences in five days is no longer rare.

Against the Dime Time top 10, decomposed the same way as 08/30:

                      worst4    top3    zeros    total
    TOP 10 (n=10)      23.33   82.89     0.80   156.19
    OURS (n=20)         7.95   57.53     1.90    87.87
    our best lineup    16.00   79.35     1.00   136.50

Our best lineup's CEILING was already there -- 79.35 top-3 against the
winners' 82.89. The entire 12.95 gap is worst-4 (16.00 vs 23.33) and one
zero. This is the 08/30 finding a second time: `worst4` separates, `top3`
does not. Seven players at >=20% exposure returned <=5 points (Gasper 30%/0.0,
Mateo 30%/0.0, Bauers 30%/2.0, Mayo 30%/3.0, Abreu 20%/0.0, Rodden 20%/5.0,
Kade Anderson 20%/3.9).

**Winners spend the cap; when WE spend the cap we score less. 09/03, 4
contests.** Field top-10 lineups are effectively maxed against the $50,000
Classic cap -- and the poorest of the 40 still spent 48,000, which is above
our MEDIAN lineup:

    contest      top10 mean   maxed    top100 mean   ours mean   ours median
    194932491       49,390    2/10        49,574      46,560       46,750
    194932492       49,650    0/10        49,589      46,935       46,700
    194946071       49,930    7/10        49,622      47,118       46,650
    194996367       49,890    6/10        49,693      46,220       46,650

15 of the 40 top-10 lineups sat at exactly 50,000; 1 of our 76 did. This is
the same ~2,000-3,000 hitter-side gap the winner-profile table already
records, slightly wider than usual.

It is still not a reason to add a floor, and this slate is the cleanest proof
yet. WITHIN our own 76 lineups salary is NEGATIVELY related to score --
rho -0.595, -0.023, -0.280, -0.369, pooled **-0.299**, negative in all four
contests. Split at our median spend:

    <= 46,700   n=39   mean 89.49   best 136.05
    >  46,700   n=37   mean 79.27   best 136.50

The cheap half outscored the expensive half by 10 points a lineup and the two
bests were a dead heat. So the field-level correlation ("winners are maxed")
and the portfolio-level correlation ("our expensive lineups are worse") point
opposite ways, and the floor acts on the second one. That is why spend15 and
minspend47 both lost live and why the 47,000 cut deleted 08/30's best lineup:
forcing the spend makes the builder buy up using `bs`, which predicts nothing,
so it converts cheap arbitrary bats into expensive arbitrary bats. Winners are
maxed because they picked right, not right because they maxed.

**And the yoy term picked the worst of them -- which is still not a lever.**
Mickey Gasper topped BOS on `bs` at 58.36 almost entirely through a +3.46
year-over-year delta (avg26 6.77 against avg25 3.31), which the formula
multiplies by 4. Roman Anthony -- BOS leadoff, second-best avg26 on the team,
21.4% field-owned, in 54.5% of the top 1%, 16.0 points -- ranked 6th on `bs`
and appeared in NONE of the 76 lineups. That is the bs decomposition above
doing exactly what it was measured to do. It is a diagnosis and not a fix:
swapping the fill sort to avg26 measured -7.79 over ten slates and ranking
windows by avg26 -12.82. Do not read this paragraph as a reason to retry
either.

**What actually has live evidence:** entering BEFORE first pitch (on 08/29 the
pre-existing entries beat late-swap builds by ~23 pts/lineup, essentially all
of it points from games that had already locked), and volume (20 -> 40 lineups
took the top-10 hit rate 0.062 -> 0.250).

## minspend49: the first arm to pass the equal-size replay, marginally

Tested 09/03 after the field-salary finding. Hard floor at 49,000, shortfall
refilled from further seeds so both arms are scored at the SAME portfolio
size (dN = 0.0 -- without that the arm builds 45 of 60 and its `best` is
penalised for having fewer draws). 17 snapshots x 3 seeds = 51 pairs,
102 builds, no build failures.

    variant      slates  nAvg    mean     sd  bestAvg   gapAvg  top10
    control          17    57    82.0   26.6    148.6    -8.08   10.7
    minspend49       17    57    83.9   27.5    152.0    -4.75   14.0

    paired: dBest +3.3  SE 1.8  t 1.80  dMean +2.0  dGap +3.32  dN +0.0

The 51 pairs are PSEUDO-REPLICATED -- 17 snapshots are only 11 distinct slate
dates (08/30 appears three times, five other dates twice). Collapsing to one
number per date is the honest test:

    09_02 -4.35   09_03 -3.03   08_26 -0.71   08_29 -0.15   08_30 +2.78
    08_31 +3.02   08_23 +4.40   08_24 +4.45   08_28 +7.02   08_25 +9.76
    08_27 +14.38

    11 dates, mean +3.41, SE 1.67, t 2.04, better on 7 of 11

So it sits exactly on the t=2 line either way. Four things make it unlike the
seven ideas that died:

  * dN = 0. It is not winning by underfilling or by extra draws.
  * dMean is ALSO positive (+2.0). Every previous constraint traded ceiling
    for floor; this one moves both, so it is not that trade.
  * **sd went UP**, 26.6 -> 27.5. Every selection arm compressed spread. This
    removes lineups rather than choosing them, the `minspend47` family that
    CLAUDE.md already identifies as the only one that has ever worked.
  * top-10 count 32 -> 42 over the same pairs, and mean gap to the real 10th
    place -8.08 -> -4.75.

And three that should hold it back:

  * The noise floor is the same size as the effect. Three snapshots of 08/30
    -- the same slate, different input times -- give -8.42, +22.17 and -5.42.
    08/26 gives +8.52 and -9.93. A +3.4 mean sits inside that.
  * `maxcorr47` measured +7.60 on best, better on 8 of 9 slates, the strongest
    backtest of the project, and lost every live contest it entered. This is a
    weaker result than that one.
  * **09/03, the slate that motivated the arm, is one of the four it LOSES
    (-3.03).** The field-salary observation came from that slate and the fix
    does not help there. That cuts against overfitting, but it also means the
    motivating story is not the mechanism.

Not shipped on this evidence alone -- the standing rule is that nothing ships
on a backtest, and the live A/B channel is closed, so there is no holdout left
to break the tie. Recorded as the strongest open candidate.

**Rebuilt on 09/03 evening at the entered size, FINAL standings: +4.55, t
1.11.** 76 lineups, both arms, five base seeds (42/100/200/300/400), scored
against contest 194932491:

    seed     control   minspend49   delta
      42      146.05      142.65    -3.40
     100      146.65      163.65   +17.00
     200      153.30      150.65    -2.65
     300      136.20      147.65   +11.45
     400      140.50      140.85    +0.35

    control    mean 144.54  sd 6.51    minspend49  mean 149.09  sd 9.03
    paired     mean +4.55  sd 9.16  SE 4.09  t 1.11  (3 better, 2 worse)
    top-10 over 20 contest-seeds: control 2, minspend49 10

Consistent with the 11-slate +3.41, and NOT significant. The top-10 count is
the one number that separates the arms cleanly (2 against 10), which is the
metric the objective actually cares about -- but it is 5 seeds on 1 slate.

**This measurement was run first on the incomplete exports and said +10.38,
t 3.00, 4 better and 0 worse.** Same slate, same seeds, same code -- only the
standings changed. An arm that looked like it never lost turned into a 3-2
split. That is the sharpest available warning about scoring anything on a
provisional export.

**RERUN ON FINAL STANDINGS AT 5 SEEDS: 10 of 11 dates better, t 3.53.** The
sweep above was rebuilt once the 09/03 exports were complete, and the seed
count raised from 3 to 5 because noise, not effect size, was the binding
constraint. 170 builds, no failures, pairings verified by hand (the new
morning contest 194931967 did not displace any slate's partner).

    variant      slates  sds  nAvg    mean     sd  bestAvg   gapAvg  top10
    control          17    5    57    82.2   26.4    148.0    -9.01   10.0
    minspend49       17    5    57    83.8   27.2    150.7    -6.32   12.2

    paired, 85 pairs: dBest +2.7  SE 1.3  t 2.03  dMean +1.5  dN +0.0

Collapsed to the 11 distinct slate dates, which is the honest unit:

    08_29 -0.27   08_26 +0.40   08_30 +1.64   08_31 +1.81   09_02 +2.33
    08_23 +2.38   08_28 +2.70   08_24 +3.77   09_03 +3.80   08_25 +4.64
    08_27 +10.62

    11 dates, mean +3.08, SE 0.87, t 3.53, better on 10, worse on 1

The single negative is -0.27, which is a tie. 09/03 flipped from -3.03 to
+3.80 purely on the corrected standings. Extra seeds cut the per-date SE from
1.67 to 0.87 without moving the mean (+3.41 -> +3.08), which is what a real
effect measured more precisely looks like rather than one that shrinks.

Why this is not another maxcorr47 (+7.60 on best, better on 8 of 9, lost every
live contest):

  * maxcorr47 was a SELECTION arm and compressed spread. This one REMOVES
    lineups and sd went UP, 26.4 -> 27.2.
  * dMean is positive too, so it is not the ceiling-for-floor trade that every
    dead constraint made.
  * It was ONE pre-specified hypothesis, tested once and re-tested on
    corrected data -- not one survivor of a sweep of 24, which is the
    multiple-comparisons trap the 08/30 sweep fell into.
  * It is mechanically motivated from field data measured before the test: the
    top-10 lineups on 09/03 averaged 49,715 of the 50,000 cap and the poorest
    of 40 spent 48,000, above our median.

SHIPPED 09/04 as the default arm, on that evidence plus the level sweep
below. **Live record so far, 3 slates:**

  * 09/04 (12 games): +7.30 over control across 5 seeds (t 1.53) on the same
    snapshot, and the only top-10 finish control could not have reached from
    any draw. The slate was otherwise bad -- 65.7% of entries below their
    contest median.
  * 09/05 early (2 games): rank 4 and rank 6 of 1,189, gaps +5.00 and +1.00.
    Verified 9-of-9 lineup-id match, so builder output with no hand edits.
  * 09/05 night (2 games): rank 7 of 1,189, gap +0.40 -- but built with
    `--hard-avoid-bs -999`, so it is NOT a clean test of the shipped config.

Three top-10 finishes in three slates after twelve contests without one.
Encouraging and still not decisive: two of the three were 2-game cards where
the portfolio was 9-12 distinct lineups, and the deciding lineup was a single
construction entered 2-3x. Control has no live record on these slates at all.

**The level does not matter much, and 49,500 is worse. Do not raise it.**
Swept 48,500 / 49,000 / 49,500 against a correct control, 17 snapshots x 5
seeds, equal portfolio size, 340 builds:

    arm            bestAvg  gapAvg  top10   sd    dBest    SE     t
    control          148.0   -9.01   10.0  26.4       --    --    --
    minspend485      150.8   -6.25   11.6  27.0     +2.8   1.4  1.95
    minspend49       150.7   -6.32   12.2  27.2     +2.7   1.3  2.03
    minspend495      149.4   -7.62   13.8  27.5     +1.4   1.7  0.80

    collapsed to 11 slate dates
    minspend485   +3.06  SE 1.15  t 2.67   better 9  worse 2
    minspend49    +3.08  SE 0.87  t 3.53   better 10 worse 1
    minspend495   +2.69  SE 1.57  t 1.71   better 7  worse 4

48,500 and 49,000 are indistinguishable on the mean (+3.06 vs +3.08); the
plateau is real and the exact threshold is not the mechanism. 49,500 keeps a
similar mean but its consistency collapses -- 7-4 instead of 10-1, and the SE
nearly doubles (0.87 -> 1.57). Tightening past 49,000 stops removing bad
lineups and starts dictating which players get bought, the same wall
HARD_AVOID_BS hit at 15 and 20. Keep 49,000: it sits mid-plateau, so a slate
that cannot quite reach it degrades gracefully.

One honest caveat against the level choice: `top10` rises MONOTONICALLY with
the floor, 10.0 / 11.6 / 12.2 / 13.8, so 49,500 produced the most top-10
finishes while scoring the worst on `best` and gap. Those two disagree, and
top-10 count is nominally the objective. It is also the noisiest column here
(a count of rare events over 85 portfolios), which is why `best` remains the
scoring metric -- but if a future sweep reproduces that ordering on more
slates, this decision should be revisited.

## Pitcher-on-the-stack: a real field edge that does NOT survive forcing

Found in the 09/04 winner study and killed by replay on 09/05. Worth keeping
because the trap is the most common one here.

Across the FULL 18,007-lineup field of seven contests -- not a winners-only
slice, so not survivorship:

    SP plays for the stacked team?   n        P(top10)   mean pts
      yes                            3,121     0.801%     93.72
      no                            14,886     0.309%     93.10

2.6x the top-10 rate with the mean flat, which is exactly the shape this
objective wants. 35.2% of top-10 lineups did it and 26.8% put SIX players on
one team that way (five hitters plus that team's starter -- legal, because the
>5 cap counts hitters only). **We had done it 0 times in 139 entries**, and
not by choice: the bring-back takes a bat from the opposing side, and
`pick_sp_pair` then bans every arm whose opp is that side -- which is the
stack team's own starter. The two are mutually exclusive per lineup.

Built as `--sp-with-stack` (drops that lineup's bring-back when the stack team
has a usable arm). It works mechanically: SP-on-stack went 7.9% -> 57.1%.
Replayed 20 snapshots x 5 seeds, equal size:

    variant        bestAvg  gapAvg  top10   dBest   SE     t    dMean
    control          146.2   -8.75   10.6      --    --    --      --
    minspend49       150.1   -4.85   15.0    +3.9   1.2  3.15    +1.8
    spstack          150.1   -4.83   11.2    +3.9   1.6  2.41    +3.1
    spstackonly      147.6   -7.33    9.8    +1.4   1.6  0.89    -0.3

    collapsed to 12 dates: spstackonly +0.02  SE 1.46  t 0.01  6/6
                           spstack vs minspend49  +1.24  t 0.98  8/4

**The pairing ALONE is exactly zero** (+0.02 over 12 dates, 6-6). Combined
with the floor it adds nothing separable (t 0.98) and its bestAvg matches
minspend49 to three digits. And it COSTS the objective's own metric: top-10
count 15.0 -> 11.2, a 25% drop, while dMean rises +1.8 -> +3.1. Mean up,
top-10s down, is the ceiling-for-floor signature.

Why the field number misled: 0.801% is a property of lineups that HAPPENED to
pair, chosen by people who liked one specific correlated game. Forcing the
pairing points it at whatever team the allocator picked, satisfied through
`bs`, which predicts nothing. Same failure as forcing 5-stacks and ranking
windows by avg26. It also trades away the bring-back, one of the few rules
with sound support. Kept behind `--sp-with-stack`, default off.

## Forcing 5-stacks does NOT lose any more -- the old -11.89 was a harness bug

The file said "force 5-stacks -11.89" and "5,5,4 -26.00" and treated stack
size as closed. Both predate the 09/03 harness fixes, when the paired verdict
t-tested **dMean** -- the proxy that shipped spend15 -- so a change trading
mean for ceiling was scored backwards. 5,5,4 was also 4 slates, and the set is
now 12 dates. Retested on top of minspend49, 20 snapshots x 5 seeds:

    variant      bestAvg  gapAvg  top10    sd   dBest   SE     t    dMean
    control        146.2   -8.75   10.6  25.4      --    --    --      --
    minspend49     150.1   -4.85   15.0  26.1    +3.9   1.2  3.15    +1.8
    stack555       151.0   -3.94   15.6  26.7    +4.8   1.6  2.98    +2.1
    stack554       152.0   -2.99   16.2  27.0    +5.8   1.7  3.41    +2.0

Every direction is right and it is NOT the usual trade: bestAvg up, gap to
10th -4.85 -> -2.99, top-10 count UP 15.0 -> 16.2, sd UP 26.1 -> 27.0. So the
original finding was an artifact, and the standing "every stack-profile change
loses" claim is retired.

**But against the arm it would replace it is a coin flip:**

    stack554 vs minspend49  +1.62  SE 1.95  t 0.84  6 better, 6 worse
    stack555 vs minspend49  +0.60  SE 2.03  t 0.30  6 better, 6 worse

    stack554 per date: -6.44 -5.95 -3.76 -3.12 -2.56 -1.96
                       +3.45 +4.86 +4.98 +6.02 +7.71 +16.26

Dead even, mean carried by one date (08/23 +16.26), and consistency collapses
against minspend49's 11/1 at SE 1.02 -- SE more than doubles to 2.30. 5,5,4
beats 5,5,5, consistent with the CONTRARIAN tier being where diversity comes
from. NOT shipped on this; minspend49 stays.

**Rerun at 10 seeds, 600 builds: the seed count was NOT the binding
constraint.** The pre-registered test was whether stack554's per-date SE
would tighten the way minspend49's did when its seeds went 3 -> 5 (1.67 ->
0.87). It did not: 1.95 -> 1.75.

    variant      bestAvg  gapAvg  top10    sd   dBest   SE     t    dMean
    control        145.6   -9.35   10.6  25.3      --    --    --      --
    minspend49     148.8   -6.11   12.9  26.1    +3.2   0.9  3.70    +1.8
    stack554       151.6   -3.37   16.0  27.1    +6.0   1.1  5.22    +2.0

Against CONTROL it is now the strongest arm ever measured here -- t 5.22 on
200 pairs, gap to 10th -3.37 against -9.35, top-10 count 10.6 -> 16.0. Against
minspend49, the arm it would actually replace:

    12 dates, best    +1.91  SE 1.75  t 1.09   7 better, 5 worse
    200 pairs, best   +2.74  SE 1.02  t 2.68   104/77
    200 pairs, top10  +0.155 SE 0.065 t 2.40   44/28, 128 tied
    12 dates, top10   +0.19  SE 0.16  t 1.22   4 better, 5 WORSE

**The two levels disagree and the pooled one is pseudo-replication** -- 200
pairs are 12 slate dates counted ten times, so its SE is a within-slate
number and its t is inflated. Collapsed to dates, `best` is t 1.09 and the
top-10 count is actually 4-5 AGAINST. minspend49 shipped at 10-of-11 dates,
t 3.53; stack554 is 7-of-12 at t 1.09 with double the SE. Same standard,
opposite answer.

**09/06 IS A `stack554` SLATE, run explicitly, not shipped.** Decided 09/05
for a 9-game card. The reasoning: the two arms are indistinguishable on the
replay (t 1.09, 7-5), so the tiebreaker is information -- minspend49 has three
live slates and stack554 has none, and only more slate DATES can settle it,
which is exactly what a live slate adds. Downside is bounded because both beat
control by a wide margin (t 5.22 vs 3.70).

Structural test on that slate's projected lineups, 60 lineups each:

    arm            n   salary  maxStk   5stk%  distinct hitters  topSP%  teams
    minspend49    60   49,575   4.12    21.7%       102            35%   4.52
    stack554      60   49,535   4.85    85.0%       109            35%   3.97
    field top-10                4.44    70.0%                            3.47

minspend49 badly undershoots the winner profile; stack554 overshoots it. The
concentration cost that would be expected did NOT appear -- stack554 used MORE
distinct hitters (109 vs 102) at identical top-SP exposure, because forcing
bigger stacks spreads the build across more teams' bats rather than fewer.

**`stack554` IS A BIG-SLATE ARM. Do NOT use it on <=3 games.** Replayed on
09/05 night (2 games) against the real standings, both arms at 20 lineups /
6 seeds with the same `--cash 0 --hard-avoid-bs -999`:

    arm          distinct   best      rank        gap        top10   mean
    minspend49       7     131.85    27 / 28   -5.60/-6.00     0     99.16
    stack554         5     120.85    68 / 107 -16.60/-17.00    0    102.63

Five distinct lineups instead of seven and `best` 11 points lower, while the
MEAN rises -- the ceiling-for-floor trade, on the metric that matters least.
The mechanism: on a 2-game card the SP pair bans two of the four teams, so all
8 hitters come from 2 teams; forcing a 5-stack pins 5 of 8 slots to one of
them and leaves 3 from the other, collapsing both the legal constructions and
the dedup space. On 9 games there are 18 teams and the same constraint costs
nothing -- it filled 60/60 with MORE distinct hitters than minspend49.

So the 09/06 decision below is specific to a 9-game card and does not
generalise. `minspend49` stays the arm on thin slates.

**`make_entries.py --arms` defaults to minspend49 and will NOT find a
stack554 upload.** Both flags have to be passed or the entries file silently
fills from the wrong (or no) portfolio:

    python build_portfolio.py --variant stack554 --lineups N
    python make_entries.py --arms stack554

Kept explicit rather than switching the default on purpose: a rushed rebuild
near lock calls the builder bare, and the default must not be an experiment.

KEPT AS THE STANDING CANDIDATE, not shipped. If the effect is real it is a
bigger one than minspend49's -- it beats control by nearly double -- but 12
distinct slate dates cannot prove it and **more seeds provably cannot help**;
the 10-seed rerun is the evidence for that. Only more slate DATES will.
Re-test when the replay set reaches ~20 dates.

The general lesson, which cost two sweeps to learn: **seeds buy precision
within a slate, dates buy generalisation across slates.** When an arm's
pooled t looks strong and its collapsed t does not, adding seeds widens the
gap between the two numbers without moving the honest one.

## STACK SIZE IS SETTLED IN BOTH DIRECTIONS. 5,4,3 stays.

Swept 09/07 on the fixed harness (23 slates, `--cash 0` fallback, parallel),
every arm on top of minspend49 so stack size is the only difference:

    stack sizes        bestAvg  gapAvg  top10    sd    vs control
    4,3,3 stack433       142.1  -10.75    9.0  24.4   -0.9  t -0.46
    4,4,3 stack443       146.5   -6.30   17.3  25.5   +3.6  t  1.76
    5,4,3 SHIPPED        148.2   -4.61   18.7  26.0   +5.2  t  3.09
    5,5,4 stack554       152.0   -2.99   16.2  27.0   +5.8  t  3.41

**`sd` falls monotonically as stacks shrink -- 27.0 / 26.0 / 25.5 / 24.4 --
and so does everything else.** Smaller stacks really do cut variance,
including catastrophic busts (09/06 produced a -0.10 lineup that was five PIT
bats against a shutout), but they cut the ceiling by more. That is the
floor-for-ceiling trade this file keeps finding, arriving from the OPPOSITE
direction for once: not tightening a constraint, but loosening concentration.

The shipped 5,4,3 has the best top-10 count of all four despite stack554
having a higher bestAvg -- the same `best` vs top10 disagreement seen in the
salary-floor sweep.

**stack554 vs minspend49, re-measured on the fixed harness: a dead heat.**
23 slates x 5 seeds, collapsed to the 14 distinct dates:

    best    +0.18  SE 2.17  t  0.08   7 better, 7 worse
    top10   -0.05  SE 0.21  t -0.25   4 better, 5 worse

    per-date: -18.02 -6.44 -5.95 -3.76 -3.12 -2.56 -1.96
              +0.98 +3.45 +4.86 +4.98 +6.02 +7.71 +16.26

Earlier readings were +1.62 (t 0.84, 12 dates) and +1.91 (t 1.09). Adding two
dates took it to +0.18. The date that moved it is **09/06 at -18.02, the worst
of the 14 -- the slate stack554 actually ran live, now in the replay set.**
Both arms still beat control decisively (t 3.09 and 2.97); they just cannot be
told apart from each other.

Note the per-date spread: -18.02 to +16.26. Same picture as the team study --
which team erupted dominates, and construction is second-order noise on top.

**KEEP minspend49.** Not because it won, but because a dead heat is no reason
to switch off an arm with three live slates and three top-10 finishes onto one
whose only live slate went 0-for-6. When the measurement cannot separate two
arms, the live record is the tiebreaker.

## The CEILING / CORE / CONTRARIAN tiers carry NO information

Never measured until 09/07 -- `metric_study` only scores numeric columns, so
`tier` had never been joined to results. 919 entries matched to a tier across
52 contests:

    tier          n    meanPts   median   meanPct   top10
    CASH         71     97.73     99.15    49.6%      0
    CONTRARIAN  194     85.87     79.80    55.1%      7
    CORE        616     84.81     84.58    55.7%      5
    CEILING      38     80.24     79.95    56.9%      0

**CEILING is the WORST tier on every measure** -- lowest mean, lowest median,
worst percentile, and zero top-10 finishes in 38 entries. The tier that exists
specifically to produce the winning lineup has never produced one.

Which tier delivered the best lineup in each contest, against its share of
entries:

    CORE        37 of 49  75.5%   from 67.0% of entries
    CONTRARIAN   7 of 49  14.3%   from 21.1% of entries
    CASH         4 of 49   8.2%   from  7.7% of entries
    CEILING      1 of 49   2.0%   from  4.1% of entries

Every tier produces winners in almost exact proportion to how many entries it
gets. That is what a label carrying zero information looks like -- lineups win
at random with respect to tier.

Caveats: CEILING is only 38 entries, so "zero top-10s" is weak alone, though
it is directionally consistent across all four columns. CASH's high mean is a
selection artifact -- cash lineups only ever existed on thin slates.

Same conclusion as metric_study from a different angle: nothing we compute
separates our own lineups, and the tier ladder rearranges the portfolio
without improving it.

**The actionable consequence: CORE is where 75% of winners come from.**
`stack554` is (5,5,4) -- it raises CORE to a 5-stack. So it applies the
stack-size change to the tier that actually matters, which is a better
argument for it than the winner-profile data. Conversely, anything that only
touches CEILING (which is what `sizes=(5,4,3)` does with 5-stacks) is acting
on 4% of the portfolio and 2% of the winners.

## How much do 5-stacks actually win? 61 contests, 149,336 field lineups

Measured 09/07 because "winners run 5-stacks" was being quoted from single
contests. Across every standings export on disk with >=200 parsed lineups:

    mean top-10 5-stack share    64.3%
    mean FIELD  5-stack share    50.3%
    our 09/08 build              18.2%

So the winner/field gap is 14 points, not the 70-vs-nothing the earlier
per-slate numbers implied -- HALF THE FIELD already 5-stacks. Quoting "70% of
winners 5-stack" without the field baseline overstates the edge.

The clean evidence is P(top 10) across all 149k lineups, which is monotonic
and on a large sample:

    1-stack  n= 2,512   0.279%
    2-stack  n=18,326   0.207%
    3-stack  n=20,528   0.322%
    4-stack  n=29,759   0.366%
    5-stack  n=78,153   0.502%

A 5-stack hits the top 10 at 1.37x the rate of a 4-stack.

**The number that matters is not 64.3%, it is 18.2%.** Our default build is
far below the FIELD, never mind the winners -- `sizes = (5, 4, 3)` maps
5-stacks to the CEILING tier only, and CEILING is ~20% of the portfolio, so
the 5-stack share is structurally pinned near 20% on every deep slate. That is
configuration, not a slate property.

**Per-contest variance is enormous:** top-10 5-stack share runs from 10% to
100% across the 61 contests, and several recent ones are low (194946071 10%,
194932491 20%, 09/07 evening 40-50%). It is a tendency across many contests,
not a rule for any one slate.

**And our own replay still says forcing it changes nothing.** `stack554`
raises the share to ~85% and measures +0.18 (t 0.08) against the shipped arm
over 14 dates. The likeliest reconciliation is the one this file keeps
reaching: the field's 5-stackers CHOOSE the team, and we cannot
(implied_total, +0.130).

## 09/08 RUN AS `stack554`, a deliberate call against a tied measurement

The user chose it for the 10-game 09/08 card knowing the replay cannot
separate it from `minspend49cov` (+0.18, t 0.08, 7-7 over 14 dates). The
argument is the structural one above: at 18.2% we are below the FIELD's 50.3%,
and the P(top10) curve favours bigger stacks. Recorded as a decision, not a
measurement, so the result is interpretable either way -- one slate confirms
nothing in either direction.

Note `stack554` does NOT carry the thin-slate coverage guarantee. On a
<=4-game card it also collapses (5 distinct lineups vs 7, `best` 11 points
lower on 09/05 night). Use it on deep cards only.

**But thin-slate winners 5-stack too -- that collapse is a constructability
failure, not evidence against the shape.** Measured over 58 contests split by
depth:

    depth        contests   top10 5stk   field 5stk    gap
    <=4 games         9        58.2%       42.4%     +15.8
    5-7 games        22        62.1%       51.2%     +10.9
    8+ games         27        70.9%       54.3%     +16.6

The winner-over-field edge is LARGEST on thin cards. So the right reading is
that we cannot build 5-stacks on a short slate without running the pool dry,
not that we should not want them there. `minspend49cov`'s thin-slate guarantee
already produces them -- 09/07 gave ATH four 5-stacks and 27 of 33 lineups
were 5-stacks -- so the shipped arm is closer to the winner profile on THIN
cards than on deep ones, which is the opposite of what was assumed.

The deep-slate gap is the real shortfall: winners 70.9%, `minspend49cov`
18.2%.

**Spreading arms across contests is already supported.**
`make_entries.py --arms A,B` loads both portfolios and deals them ROUND-ROBIN
across contests, so every contest gets a mix rather than one arm per contest.
If one arm suits the slate it has entries everywhere. (The round-robin exists
because on 08/30 all twelve contrarian lineups landed in one contest, which
finished 38.5 off its bar.)

**A winner-take-all changes the objective and we cannot exploit it.** Needing
1st rather than top-10 argues for maximum variance in that specific contest,
but we have no way to identify our own high-ceiling lineups: within-slate,
`ceiling` correlates -0.11 with realised score, `proj_points` -0.15,
`max_stack` -0.07. Targeting "our best lineups" at a WTA is not actionable.
What does work is what the user already did -- pick a small field. Contest
selection dominates: on 09/07 the same 143.85 lineup was 132nd in a
4,756-entry field and 6th in a 237-entry one.

## SHIPPED 09/07: `minspend49cov` — per-team 5-stack guarantee on thin cards

The user's design, and the first change all week that improves the shipped arm
with no trade-off. It is `minspend49` plus: on a card of <=4 games, guarantee
EVERY team one 5-stack before the normal allocation runs, overriding the fade.

    variant          bestAvg  gapAvg  top10   dBest   SE     t
    control            141.6  -11.20   11.7      --    --    --
    minspend49         146.7   -6.11   18.7    +5.1   1.7  2.97
    minspend49cov      147.8   -5.02   18.7    +6.2   1.7  3.62

t 3.62 is the strongest arm measured here. Top-10 count is unchanged and the
gap to 10th improves a full point -- it gains without giving anything back,
because on 20 of 23 slates it IS minspend49.

**Verified as a strict superset.** By depth, against minspend49:

    thin <=4 games    9 pairs   dBest +8.42  t 3.73   7 better, 0 worse
    mid  5-7 games   24 pairs   dBest  0.00  SE 0.00  every pair TIED
    deep 8+  games   36 pairs   dBest  0.00  SE 0.00  every pair TIED

Sixty of 69 pairs are byte-identical. If a future sweep shows ANY spread in
the mid/deep rows, the slate-size condition has leaked.

**Why the threshold is at 4 games.** The cost is the guarantee itself, not its
benefit: covering six teams costs six lineups and insures the whole slate;
covering eighteen costs eighteen and spends most of them on offences the
allocator rates near the bottom. Unconditional, it measured -4.99 on mid
slates and -2.81 on deep ones -- losing even though it built 5-13 MORE lineups
there.

**The fade stays.** `allteam5nofade` (guarantee + SP fade disabled) was WORSE
than the guarantee alone on mid slates (-7.15 vs -4.99) and identical on thin
ones, because the guarantee already overrides the fade for its own specs.
Removing the fade is not what makes this work.

**In-sample warning.** Nine thin-slate pairs are three distinct slates, one of
which is 09/07 -- the slate that motivated the idea. Expect +8.42 to move as
more thin cards accumulate.

**What 09/07 evening looked like without it**, which is why this exists: ATH
was hard-faded for facing Cease (adj_bs 63.47) and appeared in ZERO of 36
lineups. Cease then went for 11.65 while ATH put up Henry Bolte 39.00 at 6.4%
owned and Zack Gelof 23.00. Three of the twelve top-10 lineups in that contest
were ATH stacks, two of them 5-stacks. Our best was -23.00 off the bar. A hard
fade converts "less likely to score" into "cannot appear", on a signal
(implied_total, +0.130) barely better than chance.

**Knobs:** `--all-team-five`, `--all-team-five-per N` (guarantees per team,
round-robin), `--all-team-five-max-games N` (default 4), `--fade-sp-bs N`
(default 55, never swept).

## 09/08: WE DROPPED THE BEST OFFENCE ON THE SLATE AND NOTHING SAID SO

The worst silent failure recorded here, and the cheapest to have avoided.

The 15:43 lineups feed had 18 of 20 teams posted. The build ran on it, and
**TOR and TEX appeared in ZERO of the 77 entered lineups** -- not as a fade,
not as an allocation decision, but because `confirmed_mask` drops every
unconfirmed bat and neither team's lineup had posted.

TOR was the single best hitting spot on the card:

    TOR implied total   5.50   -- HIGHEST of the 20 teams
    TOR game total      9.5    -- joint highest
    opposing starter    Jack Perkins, adj_bs -7.21 -- WEAKEST arm on the slate

Highest implied offence against the weakest arm, and we had no way to reach
it from any of 77 draws.

**The warning that existed was worse than useless.** preflight printed:

    NOTE: only 18 of 20 SPs confirmed -- the later games have not posted.

That reads as "two missing ARMS". What it actually meant was "two missing
OFFENCES" -- a 10% cut to the slate. Same words, completely different cost,
and nothing in the pipeline distinguished them.

**Waiting was free.** Both games were the 9:40 PM ET window; the feed was
pulled at 15:43 ET-3, so there were **5.9 hours** of slack. Nothing forced
the early build. The alternative was equally cheap: `--allow-unconfirmed`
plus a late swap once the lineups posted.

This is the THIRD structural team exclusion in two days, all with the same
shape -- a team that cannot appear at all, rather than one rated poorly:

    09/07  ATH  hard SP fade (Cease adj_bs 63.47)   0 of 36 lineups
    09/08  TOR  lineup unposted at feed pull        0 of 77
    09/08  TEX  lineup unposted at feed pull        0 of 77

The fade at least acts on a number (implied_total, +0.130 -- barely better
than chance). Confirmation status acts on a file's timestamp.

**Two gates built 09/08, both diagnostic, neither touching construction.**

`preflight.check_team_coverage()` -- diffs slate teams against teams with any
confirmed player, and now **STOPS the run (exit 1)** naming each missing
team, its start time, and how many hours away it is. `DK_ALLOW_UNCONFIRMED=1`
downgrades it to a note, matching the existing override pattern. Run against
tonight's feed it correctly names TEX and TOR.

`build_portfolio.report_missing_teams()` -- the harness and any direct
builder call skip preflight entirely, so the same warning is repeated where
the pool actually exists, annotated with implied total and slate rank:

    !! 2 of 20 slate teams contributed NO hitters to the pool:
    !!   TEX  implied 3.25  (#16 of 20 on the slate)
    !!   TOR  implied 5.50  (#1 of 20 on the slate)

**Verified as output-neutral**, per the rule the spec-order regression cost:
rebuilt the 09/08 snapshot at the same seed and variant, and both
`DK_upload_09_08_2026_stack554.csv` and the portfolio summary came back
BYTE-IDENTICAL. It is a gate, not an arm -- there is nothing to measure
because it changes no lineup. That is the point.

**The operational rule: read the TEAM count, not the SP count.** preflight
prints `teams with anything confirmed: N`. If N is below the slate team
count, a whole offence is gone, and the SP line will understate it.

## The deep-slate COVERAGE FLOOR measured worse. Do not re-propose it.

Built 09/08 in response to two days of unreachable teams erupting (ATH by
fade 09/07, TOR and TEX by unposted lineups 09/08). The idea was the cheap
version of the guarantee that already works on thin cards: not "every team
gets a 5-stack" -- which costs one lineup per team and measured -2.81 on
deep slates -- but "no team finishes with ZERO", satisfied by a 2, 3 or
4-stack. `--cover-min-size N`, firing only above --all-team-five-max-games.

The budget matters and nearly went unnoticed: `make_specs` emits one spec per
allocated stack regardless of its n_lineups argument, so prepending a
guarantee ADDS n_teams lineups rather than spending them. The first build
returned 95 lineups on a request of 77. Since draws are the strongest lever
in this file (20 -> 40 took the top-10 rate 0.062 -> 0.250), that would have
won the sweep for the wrong reason. The floor now shrinks the allocation by
what it consumes. This is also the mechanism behind the note that the
unconditional 5-stack guarantee "built 5-13 MORE lineups" on mid/deep slates
-- those comparisons were not equal-size.

24 slates x 5 seeds, 625 builds, collapsed to the 15 distinct dates:

    arm        vs minspend49cov   SE     t    better/worse   dTop10
    cover2          -3.30        1.79  -1.84     4 / 9        -0.14
    cover3          -3.08        1.79  -1.72     3 / 10       -0.23
    cover4          -1.13        1.33  -0.85     4 / 9        +0.00
    control         -4.27        0.96  -4.44     1 / 14       -0.30

    pooled vs control:  minspend49cov +4.4 t 3.64   cover3 +0.1 t 0.09
    bestAvg 147.0 -> 142.7   gapAvg -5.06 -> -9.37   top10 22.0 -> 15.0

**cover3 gives back three quarters of the shipped arm's edge.** None of the
three reaches |t| 2 against minspend49cov, so strictly they are "not
separable" -- but the direction is consistent (9-10 of 13 decisive dates
worse) and the control row confirms the harness is measuring correctly.

And it is the same signature as every dead constraint here: **dMean UP
(+2.4) while dBest collapses.** Floor for ceiling, on an objective that is
purely ceiling. The ordering cover4 > cover3 > cover2 says bigger coverage
stacks lose LESS, consistent with the standing finding that the ceiling comes
from maximum concentration on one team.

**Why this does not contradict the 09/08 field data.** That slate showed a
TEX 4-stack converting to a top-10 at 28.6% against 0.56% for lineups with no
TEX -- a 51x swing, on a team implied 3.25 (#16 of 20). That is real and it
is hindsight. The floor cannot know which team will erupt, so it buys ~18
tickets and pays for them by giving up concentration on the teams the
allocator does rate. Seventeen of the eighteen are wrong. Same wall as every
other route around team selection: implied_total is +0.130 and nothing else
reads above zero.

**The distinction that matters: 09/08 was NOT a coverage-allocation failure.**
Once TEX was in the pool, the normal allocation gave them four 3+ stacks with
no help at all -- cover3 produced the SAME four. TEX was missing from the
POOL, which is the preflight gate's job, and that gate costs construction
nothing. Do not reach for the coverage floor when the actual defect is
upstream of the allocator.

**cover5 re-tested at EQUAL SIZE, 09/09, and it loses too.** The user asked
to keep 5-stacks rather than shrink them, which is the right question: the
cover2/3/4 ordering said bigger loses less, and the previous verdict on the
unconditional 5-stack guarantee (-2.81 on deep slates) was measured while the
arm was quietly building 5-13 EXTRA lineups. So it lost with a handicap in
its favour and had never been scored at dN 0. 25 slates x 5 seeds:

    arm             bestAvg  gapAvg  top10
    minspend49cov     146.4   -4.55   23.8
    cover4            144.5   -6.46   21.0
    cover5            144.5   -6.50   20.2

    collapsed, vs minspend49cov:
    cover4  mid -0.53 t +0.27 (3/3)   deep -2.91 t -1.81 (2/6)
    cover5  mid -2.01 t -0.98 (2/4)   deep -1.67 t -0.93 (3/5)

At dN 0 it reads -1.67 instead of -2.81, so most of the old number WAS the
free draws -- but the sign does not change and top-10 count is the worst of
the whole family (20.2 against 23.8). Thin slates come back exactly 0.00 with
SE 0.00 on every pair, so the slate-size condition is holding.

That closes the coverage question in both directions: cheap coverage loses,
and expensive coverage that preserves the 5-stack shape loses too.

Kept behind `--cover-min-size`, default 0 (off). NOT shipped.

Note the run lost `09_06_2026` entirely -- 25 build failures, 5 seeds x 5
arms, so every arm lost it equally and the pairing stays valid, but the set
is 24 slates / 15 dates rather than 25. Unexplained; not the cash-path
failure, and worth a separate look.

## The salary floor is LOAD-BEARING, and it pays MOST on deep slates

Tested 09/09 because 09/08's three biggest contests were won at 46,700,
46,700 and 44,700 -- ALL BELOW the 49,000 hard floor, so minspend49 could not
have built any of them. TEX was implied 3.25, DK priced their bats cheap
(Langford 4,400 / Seager 4,200 / Carter 2,800 / Lopez 2,200) and then they
erupted. It is a compelling observation and it WILL look compelling again, so
this entry exists to stop it being re-litigated.

The floor cannot be scored against `control`, which also lacks the seeds:8
refill and the coverage guarantee -- that comparison credits the floor with
extra draws. `covnofloor` is minspend49cov with the floor and ONLY the floor
removed. 25 slates x 5 seeds, collapsed to dates:

    depth        dates   dBest    SE      t    better/worse
    thin <=4        3    +2.36   4.30   +0.55      2 / 1
    mid 5-7         6    -3.79   1.46   -2.59      0 / 6
    deep 8+         8    -4.08   1.60   -2.55      2 / 6

    pooled: bestAvg 146.4 -> 143.6   gap -4.55 -> -7.40   top10 23.8 -> 18.8

**Removing the floor costs 4.08 points of `best` on deep slates and loses on
ALL SIX mid-slate dates.** It is the strongest single-component result
measured here, and it runs opposite to the hypothesis that motivated the
test.

**On 09/08 itself -- the slate that motivated it -- covnofloor scores +0.1.**
Removing the floor would have gained nothing on the night it looked most
culpable. The winners were cheap AND had TEX; we had no TEX, and the price
point was incidental to that. Do not infer a floor problem from a cheap
winning lineup.

This also resolves an earlier confound: minspend49cov vs control split by
depth reads +4.67 on deep slates (6 of 7 dates), and the isolation shows that
is the floor, not the seeds:8 refill.

**The LEVEL is still flat, confirming the original plateau.**
`minspend485cov` (48,500) vs the shipped 49,000:

    thin  +4.30 t +0.81 (1/2)   mid -1.03 t -0.50 (2/4)   deep +0.29 t +0.46 (4/3)
    pooled bestAvg 146.7 vs 146.4, top10 22.4 vs 23.8

Same answer as the first level sweep (48,500 +3.06, 49,000 +3.08). A floor is
load-bearing; its exact height is not. So lowering it to make room for a
46,700 winner buys almost nothing -- the binding constraint sits well below
48,500.

Both arms kept as `covnofloor` / `minspend485cov`, default off.

## Pre-lineup snapshots are not build failures. Fixed 09/09.

Every sweep on 09/08-09/09 reported the same block of failures:

    09_06_2026:  9 games, 180 DK players
    09_08_2026: 10 games, 200 DK players

one per arm per seed, 60 across four sweeps. Diagnosed 09/09: **the two
directories are `09_06_2026_prev2211` and `09_08_2026_prev2345`, which have
ZERO confirmed SPs.** A build archives its inputs on every run, so an
early-afternoon build leaves behind a snapshot taken before lineups posted.
build_portfolio then correctly refuses it -- "N SPs listed but NONE
confirmed=Y" -- and the harness retried it once per arm per seed.

**I misread the impact twice, and the reason is worth recording.** The
failure text is the builder's own header line, which names the SLATE DATE,
while two directories can share one date. `09_08_2026` and
`09_08_2026_prev2345` are different snapshots; the base one built fine in
every sweep and appears in every result table. So the claim that "the deep
cell is missing two of its ten members" and that "the slate the whole
investigation came from contributed nothing" was WRONG -- no date was ever
lost and no conclusion was affected. When a diagnostic names a slate, check
whether it names the DIRECTORY or the date parsed out of its contents.

The real cost was 20-30 wasted builds a sweep and a failure count that read
like a defect in the arms.

The harness now skips these up front and says which DIRECTORY and why:

    skipping 09_06_2026_prev2211: 0 of 18 SPs confirmed
      -- snapshot predates posted lineups, cannot build

Verified: 25 slates, 0 build failures. Note this is the same underlying
condition the preflight gate stops in production -- a slate whose lineups
have not posted -- arriving in the replay set as an artifact of archiving.

## Block order concentrates on the best offence BY ACCIDENT, and fixing it loses

09/09, a 4-game card. The user asked why SD held nine of fifteen stacks and
every other team held one. The answer was not analytical, and they were right
to say so:

    Primary-stack allocation (72 GPP lineups):
      SD: 12 (implied 5.00)    ATH: 11 (implied 4.00)
      WSH: 12 (implied 3.50)   SEA: 11 (implied 4.75)
      STL: 12 (implied 4.75)   TEX:  1 (implied 3.25, faded)
      TOR: 12 (implied 5.50)   SF:   1 (implied 3.25, faded)

**The allocation is FLAT.** Six teams within one lineup of each other. The
portfolio came out 14-to-1 because `make_specs` emits in BLOCK order, SD's
block sorts first, and the builder exhausted at 15 of 90 without reaching
WSH's block. A flat allocation became a concentrated portfolio through list
position.

**This is an UNDERFILL pathology, not a general one.** A deep slate fills
60-77 lineups and reaches every block, so order barely matters. Thin slates
underfill by construction, so they get it worst -- and thin slates are where
this project's best live results have come from.

**The fix produced exactly the intended distribution and measured WORSE.**
`--all-team-sizes 5,4` makes the per-team guarantee a LADDER: every team gets
a 5-stack, then every team gets a 4-stack, tier following size so the
4-stacks land in CORE rather than CEILING. Before, the guarantee emitted one
spec per team, always size 5, always CEILING -- which is why no team but the
block leader ever got a 4-stack. They were never requested, not rejected.

On 09/09 it did what it says: SD 9 -> 5 of 15, and five teams held both a
5-stack and a 4-stack. 25 slates x 5 seeds:

    depth        dates   dBest    SE      t    better/worse
    thin <=4        3    -5.62   3.74   -1.50      0 / 2
    mid 5-7         6    +0.00   0.00   +0.00      0 / 0
    deep 8+         8    +0.00   0.00   +0.00      0 / 0

    thin pooled, 20 pairs: -7.39  t -2.37

The slate-size condition is clean -- mid and deep are 0.00 with SE 0.00 on
every pair. On the only cell it touches it costs 5.6 points and wins no date.

**Why: at fixed portfolio size, a second stack for WSH is a stack NOT given
to SD.** The allocator's flat count says the teams are comparable as stack
CANDIDATES, but the lineups still have to buy the actual bats, and the
lower-implied team's are worse. Same trade that killed cover2/3/4/5, reached
from the opposite direction -- and the argument that this one was different
because it REDISTRIBUTES at fixed size rather than ADDING a coverage tax did
not save it. Redistribution at fixed size is still a transfer from the best
offence to worse ones.

**So the arbitrary rule was accidentally doing something defensible.** Block
order is not a reason, but it concentrates on whichever team the allocator
sorted first, and concentration on the best offence is what produces the
ceiling. The defect is real and it is not costing points, so it is not the
lever.

**The narrower fix is still untested and is the one worth trying.** ONE LIST
IS DOING TWO JOBS: `make_specs` uses spec position both to assign tiers
(CEILING to the first n_ceil, CONTRARIAN to the last n_cont) and to set the
order in which the builder attempts them. The 09/07 round-robin regression
(-6.7 on the shipped arm) came from breaking the first to fix the second.
Assign tiers on the block-ordered list exactly as now, THEN reorder only the
attempt sequence with each spec carrying its assigned tier. That fixes the
arbitrariness without transferring lineups between teams -- SD keeps its
count, only the order of attempts changes when the build exhausts. Untested
as of 09/09.

Kept behind `--all-team-sizes`, default off (`team54`). NOT shipped.

## 09/09: rank 11 by 1.10, and the winners were TWO-TEAM SPLITS

3 contests, 43 entries, 4-game card, `minspend49cov` merged with `covnofloor`
to fill. Standings verified final (0 entries with time remaining).

    contest      field  ours    best  rank    10th     gap  top10
    195370063      237     7   97.55    20  110.50  -12.95      0
    195370071    1,189    16  132.55    11  133.65   -1.10      0
    195370072    1,189    20  124.55    18  133.65   -9.10      0

All 43: best 132.55, mean 80.35, worst 37.50. Second time the project has
finished 11th by around a point (08/30 was rank 11 by 0.50).

**Almost nothing went wrong except the SHAPE.** SD was in 27 of 30 top-10
lineups and we were heavy on it. We held the slate's top scorer -- Jackson
Merrill 47.0 at 13.3% owned -- in 9 of 24 lineups. Only two top-10 players
were outside our pool and both busted (1.4 and 3.0). The three winners:

    195370063  152.55   STL 4 + SD 3
    195370071  152.55   STL 4 + SD 3
    195370072  144.50   SD 4 + STL 3

**Zero of our 24 distinct lineups held SD 3+ AND STL 3+.** Seventeen were
single 5-stacks. STL was in 15 of 30 top-10 lineups and we had two STL
stacks -- and STL was ALLOCATED 12, identical to SD.

    TOP 10 (n=30)   worst4 17.32   top3 82.85   zeros 0.53   total 132.52
    OURS  (n=43)    worst4  6.04   top3 55.33   zeros 2.53   total  80.35

## The SECONDARY STACK is not dead -- the 08/30 verdict was the wrong AXIS

`--secondary-stack N` places a contiguous run of N bats from a second team
before the fill loop: CEILING becomes 5+3, CORE 4+3, CONTRARIAN 3+3. Second
team ranked by implied total, sampled from the top four, never the primary,
never a team our SPs oppose, never faded.

08/30 measured a secondary 3-man stack at **-6.39 over nine slates** against
control and the file closed the question. Retested 09/09 on 27 slates against
minspend49cov with placement scoring, collapsed to dates:

    arm     depth        dates   dBest    SE      t    better/worse
    sec3    thin <=4         4   +2.70   1.98  +1.36      4 / 0
            mid 5-7          6   +1.61   1.47  +1.09      5 / 1
            deep 8+          8   -4.88   4.06  -1.20      3 / 5
    sec2    thin <=4         4   -1.13   4.34  -0.26      2 / 2
            mid 5-7          6   +0.42   2.12  +0.20      4 / 2
            deep 8+          8   -4.08   1.64  -2.49      2 / 6

**The sign depends entirely on DEPTH**, and the 08/30 set was weighted toward
deep cards. The old number was not wrong, it was aggregated over the wrong
axis -- the same error as the per-team 5-stack guarantee, which read -2.81
unconditionally and +8.42 once confined to thin slates.

**The mechanism was stated BEFORE the data, in the 08/30 note that killed
it:** "two 3-man runs need two teams to erupt, one 5-man run needs one." That
predicts a split pays only where the pool is too thin to build another good
single stack -- thin and mid, not deep. That is exactly where it lands, which
is why this is treated differently from the four ideas that failed this week.

Caveat on the deep row: 09_06 alone reads **-30.7** against -7.9, -5.5, -3.9,
-3.4 and three positives. Drop it and deep is roughly flat. The deep loss is
less established than the thin/mid gain.

`sec2` is dead -- weakest thin cell and a genuine deep loss at t -2.49.

**`secthin` = sec3 gated at <=7 games. Verified as a strict superset:** deep
8+ returns +0.00 with SE 0.00 on all 65 pairs. Pooled over 27 slates it reads
bestAvg 146.8 against 145.5 and gap -1.21 against -2.51 -- the best gap-to-
10th measured here -- on 9 of 10 decisive dates.

NOT SHIPPED. Four thin dates and six mid ones do not clear the bar that
shipped minspend49cov (10 of 11 dates, t 3.53). Kept as `--secondary-stack`
/ `--secondary-max-games`.

## Attempt order is a SEPARATE question from tier assignment

The narrow fix flagged when the 5,4 ladder failed, now built and measured.
`make_specs` used spec position for two different jobs: assigning tiers
(CEILING to the first n_ceil, CONTRARIAN to the last n_cont) AND setting the
order the builder attempts them. `--interleave-attempts` reorders ONLY the
second, after tagging, so every spec keeps the tier it was assigned on the
block-ordered list and no lineup moves between teams.

    arm       depth        dates   dBest    SE      t    better/worse
    ilv       thin <=4         4   +1.84   1.34  +1.37      3 / 0
              mid 5-7          6   -0.78   1.36  -0.57      3 / 3
              deep 8+          8   +1.05   1.69  +0.62      4 / 4

Compare the three attempts at the SAME complaint:

    team54  (moves lineups between teams)      thin -5.62   0 / 2
    ilv     (moves nothing, reorders attempts) thin +1.84   3 / 0
    sec3    (changes lineup shape)             thin +2.70   4 / 0

The distinction holds: redistributing lineups away from the best offence
loses; changing WHICH specs get attempted when the build exhausts does not.

**`ilvthin` = gated at <=4 games, verified strict superset** -- mid and deep
both +0.00 SE 0.00 across 105 pairs. Confining it also contains the top-10
cost, which was spread across all depths ungated (thin -0.40, mid -0.30, deep
-0.12 per portfolio).

NOT SHIPPED, on four thin dates. Kept as `--interleave-attempts` /
`--interleave-max-games`.

**Top-10 count disagreed with `best` on FOUR consecutive arms this week**
(ilv, ilvthin, sec3, secthin all raise bestAvg and lower top10). The standing
resolution is that `best` scores because top-10 count is the noisiest column,
but four in a row is worth watching rather than assuming.

## `merge_arms.py`: cross-arm fill, and the gate that was measured wrong

The 09/07 and 09/09 hand-merges are now a script. A thin slate caps the
portfolio below the entry count and the shortfall becomes DUPLICATES, which
score identically to their twin and are worth zero extra draws; a distinct
lineup from another arm is worth one.

**The concentration gate had the wrong counterfactual and rejected every
donor.** First version admitted a donor only if it made no exposure worse.
On the 09/07 snapshot that rejected all 19 distinct donors, because the base
already held one bat at 64% against a 20% cap and nearly every donor from the
same pool contains him.

That test compares a donor against an EMPTY SLOT. The real alternative is a
DUPLICATE, which contains the same players and adds no draw -- so refusing the
donor does not protect concentration, it forfeits a shot. The gate now allows
the worst exposure to drift up to `--tolerance` (default 5 points) above the
BASE portfolio's worst, measured against the base so one admission cannot
ratchet the bar.

Re-run on the same 3-game snapshot: **33 -> 47 distinct, zero duplicates, and
the worst bat exposure FELL 64% -> 55%** because the donors diluted it. The
strict gate had been rejecting the very lineups that reduce concentration.

**Choose donors that share the base's measured components.** minspend49cov +
ilvthin differ only in attempt order, so their disagreements are exactly the
lineups the other never reached and nothing measured is diluted.
minspend49cov + covnofloor was used on 09/09 and DILUTES the salary floor --
that portfolio came out at a 48,042 mean with one lineup at 42,400, against a
floor worth -4.08 dBest on deep slates and -3.79 on mid when removed.

## On a thin slate, 125 vs 147 is mostly NOISE. A bound, not a lever.

Chased on 09/10 after the 09/09 replay appeared to show a 146.6 portfolio
against the 132.55 that was entered. Three separate readings of that number
were wrong before it resolved; the resolution is worth more than the chase.

**What actually existed on 09/09:**

    11:15 build, PROJECTED WSH lineup, as built     best 125.55
    confirmed build, as ENTERED                     best 132.55

So rebuilding on the confirmed feed GAINED 7 points. The projected build's
best lineup carried CJ Abrams 0.0 and Zack Gelof 0.0; the confirmed one
replaced them. The decision to rebuild once lineups post is correct and this
slate supports it.

**Where 146.55 came from.** The replay does not score the portfolio that was
built -- it REBUILDS from the snapshot's inputs at its own settings. Rebuilt
from the same projected inputs at `--lineups 60 --seed 42 --seeds 8` it
produces 146.55, verified reproducible, a legal portfolio with 2 of 15
lineups clearing the 133.65 bar. Its best lineup is an SD five-stack plus
three STL -- the two-team split that won all three contests, arrived at from
a batting order that was WRONG.

That portfolio never existed. Entering it required keeping lineup data known
to be a guess AND a request size that does nothing on most slates.

**The finding is the sensitivity itself.** Request size swept on the two
snapshots of the same slate:

    confirmed inputs   --lineups 40 / 60 / 90   best 132.55 / 132.55 / 132.55
    projected inputs   as built (43) vs rebuilt (60)    125.55 vs 146.55

Identical to the penny on one input set; a 21-point swing on the other. That
is not a lever, it is noise sensitivity -- the same shape as the seed
sensitivity already recorded (09/06: one arm, one snapshot, three seeds,
131.20 / 151.60 / 161.20).

**So treat 125-147 on a 4-game card as one noise band.** 09/09 finished 1.10
under a bar sitting in the middle of it. Construction cannot be expected to
control that gap, and an arm that appears to move `best` by 10 points on a
single thin slate has told you nothing. This is the quantitative version of
what the per-date spreads have been saying all along (stack554 vs
minspend49cov ran -18.02 to +16.26 across 14 dates).

**Do NOT re-derive a request-size lever from this.** Sweeping 40/60/90 on the
confirmed snapshot returned identical best AND identical top-SP and top-bat
exposure (60%/60%). The caps hypothesis -- that a bigger request sizes the
exposure caps to a portfolio that never exists and switches them off -- is
arithmetically true and made no difference to the outcome.

**Process failure, third occurrence in one session.** All three were the same
error: reading a number without checking which ARTIFACT produced it.

  * 09_08_2026 "build failed" was 09_08_2026_prev2345, a different directory
  * the 146.6 came from pooling 09_09_2026 with 09_09_2026_prev1115, because
    the analysis matched on `slate.startswith('09_09')`
  * 146.6 was then compared against a portfolio built at different settings

The lesson was recorded after the first one -- check whether a label names
the DIRECTORY or the date parsed from its contents -- and repeated twice more
within hours. When a slate label appears in any output, resolve it to a
directory before comparing anything. Two snapshots per date is NORMAL: the
builder archives its inputs on every run, so any day with two builds has a
`_prevHHMM` twin.

## CONTEST SELECTION IS THE BIGGEST MEASURED EFFECT IN THIS FILE

Measured 09/10 across 65 standings exports, grouped into 23 slates by
player-pool overlap so the comparison is WITHIN a slate -- same players, same
night, different fields. `contest_study.py`.

    slate   contests  smallest n  its bar   largest n  its bar    gap
    72048          4         237   135.00        4756   168.85  +33.85
    28005          5        1189   179.00        5251   212.45  +33.45
    50985          5         237   125.05        1650   149.85  +24.80
    70063          3         237   110.50        1189   133.65  +23.15
    16721          2        1189   176.80        5839   198.70  +21.90
    82398          2         475   136.75        3567   157.45  +20.70
    46071          4        1169   147.50        7134   168.35  +20.85
    92516          7         890   143.20        7134   163.00  +19.80
    ...
    mean bar gap +14.75, and the BIGGER field had the higher bar on 15 of 16

By field size across all 65:

    field       contests   mean bar   mean winning score
    <300               4     116.44     145.84
    300-1k             8     151.47     177.19
    1k-2.5k           32     152.91     175.10
    2.5k+             21     170.14     193.40

**A sub-300 field's 10th-place bar is ~54 points below a 2.5k+ field's**, and
quadrupling the field costs about 15. Fifteen of sixteen slates, one tie.

For scale, the best CONSTRUCTION result in three weeks is minspend49cov at
+5.1 dBest (t 2.97) over control. **Contest selection is worth roughly three
times more, and more consistently.** Every near-miss in this file is really a
contest-selection story:

    08/30  rank 11 by 0.50; top-10 in either of the other two contests
    09/04  the only top-10 needed the softest bar; 148.45 missed in six others
    09/07  the same 143.85 lineup was 132nd in a 4,756 field and 6th in a 237
    09/09  132.55 was rank 11 vs a 133.65 bar, while a 237-entry contest on
           the same slate had a bar of 110.50

This is the one lever that has never been worked on, and it is bigger than
everything that has.

**Caveat before over-reading it.** Small fields pay less, and this measures
the BAR, not the money -- the file deliberately does not track dollars
because ROI measures the contest rather than the build. What it establishes
is where a portfolio of our observed strength (best lineup ~130-150) can
actually finish top ten. It cannot say whether the payout justifies it.
Four sub-300 contests is also a thin cell.

## Duplicates must go to DIFFERENT contests. Fixed 09/10.

A duplicate in another contest is an independent shot at a different bar. Two
copies in the SAME contest rise and fall together -- one outcome counted
twice, and the second entry buys nothing.

`make_entries` dealt duplicates by round-robin on the duplicate index alone,
ignoring which contest the row belonged to. On 09/09, **6 of 18 duplicated
lineups had both copies in the same contest.** It now tracks what each
contest already holds and picks a lineup that contest is missing, falling
back only when the entry count for a contest exceeds the distinct supply.

Re-dealt 09/09: 0 of 18 share a contest, and the two lineups entered three
times land one per contest.

## THREE arms nearly DOUBLE the distinct lineups on a thin card

Measured 09/10 on the 09/07 3-game snapshot, `--lineups 90` each, merged with
`merge_arms.py`:

    base  minspend49cov                33 distinct
      +   ilvthin                     +19
      +   secthin                      +8
                                    -----
                                       60 distinct, zero duplicates

**82% more draws**, and concentration IMPROVED rather than degraded -- worst
bat 64% -> 60%, top SP 36% -> 37%. Donors dilute, because they are different
lineups drawn from the same pool.

This matters more than any arm in this file. More draws is the strongest
measured lever anywhere here (20 -> 40 lineups took the top-10 hit rate
0.062 -> 0.250, roughly twice the best arm's effect), and a thin slate caps a
single arm well below the entry count -- 09/09 delivered 15 of a 90 request
no matter what was asked for.

**Donor yield tracks how much the arm shares with the base.** ilvthin gave 19
and secthin 8. ilvthin differs from the base ONLY in spec attempt order, so
it reaches lineups the base never tried but builds them the same way.
secthin changes lineup SHAPE, which has fewer legal constructions on a short
card -- it logged 161,543 failed secondary-stack attempts, because after
removing the primary team, the two teams the SPs oppose and any faded team
there is almost nothing left to pair with. It still found 8 the others
missed.

So pick donors that differ from the base in ONE dimension, not many. A donor
that shares the base's measured components (floor, guarantee, fade) and
changes only how the space is walked is the most productive kind.

**Do not read the arms' own sweep results into this.** ilvthin and secthin
are NOT shipped -- four thin dates and six mid ones do not clear the bar
minspend49cov cleared. As DONORS the exposure is much smaller: the base
portfolio is untouched and the extras are lineups those arms would have built
anyway. That is the same argument that justified the 09/07 cross-arm fill,
and it is weaker than a shipping decision on purpose.

### AMENDED the same day: the marginal lineup is much worse than the first

The "82% more draws" headline above is the COUNT. Replayed on 09/09 against
the real standings it bought nothing, and that is the honest half of this.

    arm merge on 09_09_2026 (4 games, confirmed feed)
      base minspend49cov  15   + ilvthin 4  + secthin 6  + covnofloor 9 = 34
      what was actually entered                                        = 25

                          entered(25)   4-arm(34)
      best                    132.55      132.55
      2nd                     127.55      127.55
      3rd                     124.55      124.55
      mean                     83.47       77.20
      clears 110.50 bar             5           6
      clears 133.65 bar             0           0

    the 9 lineups added but never entered: BEST 116.90, none above 133.65

**36% more draws produced one extra lineup over the soft bar and nothing at
all against the hard one.** Identical top three; the mean FELL, because the
marginal lineup is drawn from the bottom of the pool.

The principle survives -- more draws is still the strongest construction
lever -- but it is not a free win, and it was not the binding constraint on
09/09. That slate's pool topped out near 132.6 across all five seeds while
the bar was 133.65. Drawing from a distribution 25 times or 34 times cannot
help when the distribution does not reach the bar.

**So the two slates disagree and BOTH are real:**

    09/07 (3 games)  33 -> 60 distinct, donors productive
    09/09 (4 games)  15 -> 34 distinct, donors all worse than what we had

Game count is not what separates them -- 09/09 had MORE games and a far
smaller pool, because SF and TEX were hard-faded leaving 6 usable teams of 8.
Expect donor yield and donor QUALITY to vary slate to slate, and do not
budget for the 09/07 number.

**Donor productivity also contradicted the "differ in one dimension" rule.**
On 09/09 ilvthin gave 4 and covnofloor gave 9. ilvthin differs only in walk
order so it re-treads the same salary space and collides with what the base
already built (9 of its 15 were already held); covnofloor removes the floor
and builds in genuinely different territory. The corrected rule: **prefer a
donor that differs in a dimension which changes what is CONSTRUCTIBLE**, not
merely how the space is walked -- while remembering covnofloor buys that by
diluting the floor, worth -4.08 dBest on deep slates.

**What would actually have converted 09/09:** the 132.55 entered in the
237-entry contest (bar 110.50) rather than the 1,189-entry one (bar 133.65).
Five of the entered lineups already cleared 110.50. Contest selection remains
the only thing measured this week that turns that slate into a top-10 finish.

## 09/10: TOP-10, and the lineup came from a DONOR arm

3-game card, 47 entries, 3 contests. Standings verified final.

    contest      field  ours    best  rank    10th     gap  top10
    195473811      237     7   87.20    70  103.45  -16.25      0
    195473819    1,189    20  110.70    10  110.70   +0.00      1
    195473820    1,189    20   99.05    84  111.05  -12.00      0

Rank 10 of 1,189, exactly on the bar. All 47: best 110.70, mean 81.21.

**The top-10 lineup came from `ilvthin`, a DONOR, not the base arm:**

    source        n    best     mean
    base         24    97.70    77.16
    ilvthin      14   110.70    83.63   <- the top-10 finish
    covnofloor    9    94.05    85.45

**Without the merge the best lineup was 97.70 -- 13 points under the bar.**
ilvthin also held the 3rd-best (96.30). The base arm alone finishes ~80th.

The lineup was an SEA five-stack with both aces: Logan Gilbert 32.1, Zack
Wheeler 27.6, Julio Rodriguez 18.0. **SEA was one of the two teams on
PROJECTED batting orders** -- the `--allow-unconfirmed` call is what made it
reachable at all, because the feed at 08:18 had SEA and TEX unposted and their
game did not start until 4:10 PM ET while first pitch was 12:15 PM ET.
Waiting was NOT free on this card; waiting meant not entering.

**This is the first live evidence for cross-arm fill**, which the file had
recorded as "a live technique with sound reasoning and no measurement". One
slate, but unambiguous: the entire finish traces to a donor.

**And it cuts against the 09/09 amendment.** That entry records the marginal
donor lineup as coming "from the bottom of the pool" -- 9 added lineups
topping out at 116.90 and changing nothing. Today the donors produced the
BEST lineups in the portfolio. Both are real. The honest reading is that donor
QUALITY is slate-dependent and not predictable in advance, not that donors are
reliably weak or reliably strong. Budget for the technique adding draws; do
not budget for where in the distribution they land.

**What still went wrong, and both are already-documented defects:**

  * **TEX was hard-faded and appeared in 10 of 30 top-10 lineups.** HOU led
    with 13 of 30 and we held 12 HOU stacks (up from ONE before the merge --
    the merge helped there too). TEX we held 4. That is the FOURTH time a
    faded team has landed in the top-10 lineups (ATH 09/07, TEX 09/08,
    TEX 09/06, TEX 09/10).
  * **The 237-entry contest got our weakest slice** -- best 87.20, rank 70
    against a 103.45 bar, while the portfolio's best lineup went to a
    1,189-entry field. That is exactly the "small contests get the HEAD of
    the portfolio" defect, still unfixed, and it is the contest the selection
    study says is most winnable.

## Small contests get the HEAD of the portfolio, not a sample of it

Found 09/08 when the user noticed one arm looked heavy in one contest.
`make_entries` deals round-robin across contests, which fixes the tier mix
(08/30's twelve contrarian lineups all landing in one contest) but NOT
exposure. A contest smaller than the portfolio exhausts early, so it only
ever sees the first N rows:

    contest      n   distinct   Misiorowski   portfolio rows drawn from
    195250979    7      7/7      4/7  57%      1-30
    195250985   10     10/10     5/10 50%      1-35
    195251085   20     20/20     0            3-71
    195251086   20     20/20     0            2-76
    195251087   20     20/20     0            3-77

Portfolio-wide Misiorowski was 19/77 = 25%, inside the 40% cap. The cap is a
PORTFOLIO fraction and nothing enforces it per contest. Because the portfolio
is ordered CEILING -> CORE -> CONTRARIAN and the anchor SP concentrates in
the head, the two small contests got double the intended exposure and never
saw the contrarian tail at all.

Not fixed -- flagged during the 09/08 build with minutes to lock and the user
chose to enter as-is. The fix is to stratify the small-contest slice across
the whole portfolio rather than take a prefix. Relevant because contest
selection dominates placement (on 09/07 the same 143.85 lineup was 132nd in a
4,756-entry field and 6th in a 237-entry one), so the SMALL contests are the
winnable ones and they are exactly the ones getting the least diversified
slice.

## I BROKE THE SHIPPED ARM WITH AN UNMEASURED "FIX". Do not touch spec order.

Recorded because the failure mode is subtle and I fell into it while enforcing
the same rule on everything else.

`make_specs` emits the allocation in BLOCK order -- all of team A's lineups,
then team B's. That looked like a bug: on 09/07 the slate delivered 30 of 67
and SF got ZERO stacks despite the second-highest implied total, purely for
sorting late. I replaced it with round-robin interleaving, called it a fix,
and ran a sweep.

The block order is LOAD-BEARING. The tier ladder assigns CEILING to the first
n_ceil specs and CONTRARIAN to the last n_cont, so it only lands the ceiling
tier on the best offences BECAUSE the list is in block order. Interleaving
scattered the tiers across teams.

    minspend49 vs control    before  +5.2  t 3.09   bestAvg 148.2
                             after   -1.1  t -0.67  bestAvg 141.5

A 6.7-point regression in the live default, and it silently contaminated the
sweep running against it -- the baseline moved underneath the experiment, the
same class of error as the 09/04 `--variant` default bug. Reverted; the
comment at the call site carries these numbers.

Two rules this cost: never change a SHARED code path (make_specs, audit,
allocate_stacks) while a comparison is running against it, and no builder
change ships unmeasured -- including ones that look like obvious bug fixes.

## FIX #18 RETIRED 09/07: thin slates no longer build cash-style

The single most consequential change in a while, and it came from the user
asking why we suppress 5-stacks when winners have them.

`SMALL_SLATE_GAMES = 4` routed any <=4-game card entirely through
`try_build_cash`. That function is a CASH builder -- written for 50/50s and
double-ups, where a high floor wins and ceiling is worthless. Every choice in
it trades ceiling for floor:

    CASH_TEAM_CAP = 3       forbids 4- and 5-stacks OUTRIGHT
    CASH_MIN_SALARY = 3000  no punt plays
    CASH_MIN_SPEND = 48000  must nearly max the cap
    fill = top-2 by avg26   near-argmax, so it lands on the same bats

Fix #18's own justification was "cash lineups took both cashes" on 8/22 --
evidence about a contest type **the user stopped entering**. The objective has
been a top-10 FINISH for weeks.

What it was costing:

  * **2-game cards built ZERO lineups.** CASH_MIN_SPEND is unreachable once
    the SP pair bans two of four teams. Broken, not suboptimal.
  * **09/07 evening: all 36 entered lineups were 3-stacks.** Top-10 lineups on
    thin slates ran 4- and 5-stacks 36% / 50% / 70% / 100% of the time across
    the four thin contests with standings. We were structurally incapable of
    the shape that wins.
  * **The three top-10 finishes on 09/05 came from `--cash 0` portfolios**,
    68-100% four-and-five stacked. That flag was passed to work around the
    2-game crash, not as strategy. The workaround was the win.
  * The stray `DK_upload_cash_*` file this path writes broke `make_entries`
    minutes before lock on 09/07.

After the change, the same three snapshots build 9 / 6 / 31 where they
previously built 0 / 0 / 30.

`try_build_cash` and the CASH_* constants are UNTOUCHED and still reachable
with `--cash N`. This is a routing change, not a deletion.

**Methodological consequence for every sweep before 09/07.** Cash mode ignores
variant config -- stack_sizes, the salary floor, all of it -- so on <=4-game
slates every arm produced the IDENTICAL portfolio (verified: control,
stack554 and stack443 all returned the same 36 lineups on 09/07). Those slates
contributed ZERO discriminating information to the paired t-tests and only
widened the SEs. No conclusion reverses, but arm comparisons run before this
date were diluted by however many thin slates were in the set.

**Not yet scored.** 09/07 evening is the first slate where both paths build a
portfolio, and its standings were not available when this shipped. The change
rests on the 2-game builds being broken, the winner shapes, and the 09/05
provenance -- not on a replay. Score it when those standings land.

## The opposing-SP ban is LOAD-BEARING, not orthodoxy. Do not relax it.

User's idea, 09/07: let FILL hitters face our own SP while the stack still
never may, to widen the construction space. Built as `--fill-may-oppose`
(`fillopp49`, `fillopp554`). It needed three changes -- the GPP fill filter,
the cash path's team filter, and the AUDIT, which otherwise rejects every
relaxed lineup.

**It did not open up combinations, in either regime:**

    9-game card   minspend49 110 -> fillopp49 108
                  stack554   112 -> fillopp554 113
    3-game card   minspend49  36 -> fillopp49  36

On a big slate, banning 2 of 18 teams was never the constraint. On a thin
slate the wall is `try_build_cash`'s near-argmax fill (top-2 by avg26), which
converges on the same bats however many teams are legal -- widening the pool
cannot route around it.

**And where it did change construction, it cost the ceiling.** 23 slates x 3
seeds:

    variant       bestAvg  gapAvg  top10    sd   dBest   SE     t    dMean
    control         143.0   -9.85   11.7  25.0      --    --    --      --
    minspend49      148.2   -4.61   18.7  26.0    +5.2   1.7  3.09    +2.2
    fillopp49       142.9   -9.91   16.7  25.3    -0.1   2.0 -0.03    +2.7
    fillopp554      146.4   -6.38   16.0  26.3    +3.5   2.0  1.71    +1.9

`fillopp49` gives back the arm's ENTIRE edge -- back to control on both
bestAvg and gap. Note its dMean is **+2.7, the highest of any arm measured**,
while dBest is -0.1: letting fills oppose our SP raises the average lineup and
destroys the top one. That is the negative correlation working exactly as
theory says -- the opposing bat's hits are our pitcher's runs, so they cancel
-- and it is the cleanest floor-for-ceiling example in this file.

So the ban is not DFS folklore we inherited; it is worth ~5 points of `best`.
Kept behind `--fill-may-oppose`, default off.

## Cross-arm fill: use a SECOND arm for the shortfall instead of duplicating

User's idea, 09/07. When a slate caps the portfolio below the entry count, the
remaining rows get duplicates -- and a duplicate is worth ZERO extra draws,
since it scores identically. A distinct lineup from a different arm is worth
one. Done live on 09/07 (6 games, 80 entries):

    before:  71 distinct + 9 duplicates   71 effective draws   79 hitters
    after:   79 distinct + 1 duplicate    79 effective draws   83 hitters

The donors were `stack554` lineups, merged only if they were not already
present AND did not push any player past ABSOLUTE_SP_CAP or FILL_CAP over the
COMBINED portfolio -- which is why 8 of 9 slots filled rather than 9. Top SP
after the merge was 29/79 = 36.7%, inside the 40% cap. All 79 pass the audit
independently.

Why it is defensible rather than a hack: stack554 and minspend49 are a
measured dead heat (+0.18, t 0.08 over 14 dates), so the marginal lineups are
not worse in expectation, and "more draws" is the strongest lever in this file
(20 -> 40 lineups took the top-10 hit rate 0.062 -> 0.250). The donors are
5-stack constructions, so the tail of the portfolio runs hotter than the rest.

NOT YET REPLAYED. The merge is a portfolio-assembly step rather than a builder
arm, so the harness cannot test it as-is -- it would need to build two arms
per (slate, seed) and merge before scoring. Queued as the next test. Until
then this is a live technique with sound reasoning and no measurement.

**Attribution:** the upload keeps the `_minspend49` filename while containing
8 stack554 lineups. Those still hash exactly (builder output, not hand
edits), but 09/07 is NOT a clean minspend49 slate and should not be counted as
one.

## Can we pick the team to stack? Measured: barely, and that is the ceiling

384 team-slates over 24 slates. Predictor from the frozen snapshot, target the
realised sum of that team's TOP 5 hitters -- what a 5-stack actually captures.
Within-slate Spearman, because "which team on THIS card" is the only
comparison the builder makes:

    metric      WITHIN    worst    positive on
    implied    +0.130   -0.327     15 of 22
    gtot       +0.099   -0.426     14 of 22
    sal        +0.066   -0.343     17 of 22
    opp_impl   -0.000   -0.594     12 of 22
    avg26      -0.005   -0.371     10 of 22

`implied_total`, already the input to `allocate_stacks`, is the best available
signal and it is weak -- negative on 7 of 22 slates. In concrete terms:

    the highest-implied team was...      random baseline (~16 teams)
      the best stack       9.1%              6%
      in the top 3        27.3%             19%
      in the top 5        45.5%             31%
      in the BOTTOM HALF  54.5%

Better than chance, and the team Vegas likes most still lands in the bottom
half more often than not.

The other candidates are EXHAUSTED, not untested. Opposing SP quality reads
exactly 0.000 -- Vegas prices the starter into the line, so you cannot beat
the line with an input it contains. Team avg26 is -0.005, the team-level echo
of `bs` predicting nothing. Weather is inside the Vegas number too. Salary is
+0.066 and the most CONSISTENT (17 of 22) but weaker and largely redundant.

09/06 is the illustration: TEX appeared in 31 of 56 top-10 lineups and no
available signal flagged it. The field found it by covering every team.

**So the leverage is not better team selection -- it is covering more teams.**
And the stack-size sweep above shows we cannot buy that coverage by shrinking
stacks either, because the ceiling falls faster than the variance. Both routes
around the team-selection problem are now closed.

## Seed blocking: a well-shaped idea that measured WORSE

User's idea, 09/06: rotate the RNG seed every N lineups instead of holding one
seed for the whole portfolio. The diagnosis behind it was correct -- the same
arm on the same 09/06 snapshot gave `best` of 131.20 / 151.60 / 161.20 across
three seeds, a 30-point swing a single-seed portfolio is fully exposed to. And
each spec gets exactly ONE attempt per seed, so a poor RNG path on the slate's
best stack is never revisited.

Built as `--seed-block N` / `seedblk10` / `seedblk20`, on top of minspend49.
The spec list is deliberately untouched, so the team allocation is NOT thinned
-- every team still gets its lineups, just built under different RNG paths.
21 slates x 3 seeds:

    variant      bestAvg  gapAvg  top10    sd   dBest   SE     t    dMean
    control        146.5   -8.11   11.0  25.6      --    --    --      --
    minspend49     152.2   -2.39   18.7  26.8    +5.7   1.8  3.20    +2.4
    seedblk10      149.3   -5.34   16.0  26.3    +2.8   2.3  1.19    +1.9
    seedblk20      150.2   -4.39   16.3  26.5    +3.7   2.1  1.82    +2.4

**Worse at both block sizes.** Against minspend49, seedblk20 loses 2.0 on
bestAvg, 2.0 on gap, and takes top-10 count 18.7 -> 16.3; seedblk10 is worse
still. Both fall to "not separable from control" while plain minspend49 sits
at t 3.20.

Why the mechanism backfires: each block builds under exposure counters and
seen_sigs already set by earlier blocks, so later blocks work in a
progressively MORE constrained space than a single seed ever faces. You trade
one clean exploration of every spec for several partial ones, and that costs
more than the poor-RNG-path problem it was meant to fix. Monotonic in block
size (10 worse than 20) is the tell: smaller blocks mean more re-rolls and
more of the penalty.

Kept behind `--seed-block`, default 0 (off). Worth noting it is NOT in the
selection family that killed five arms -- it never ranks lineups -- which is
why it was worth testing at all.

## The harness runs builds in PARALLEL now, and silently dropped 3 slates

Two fixes on 09/06.

**Parallelism.** `backtest_variants.py` ran builds one at a time via
subprocess.run and used ~15% of an 8-core machine; 7 cores sat idle for entire
sweeps. It now submits every (snapshot, arm, seed) build to a
ThreadPoolExecutor (`--jobs`, default 6) and scores afterwards. Builds are
independent subprocesses and subprocess.run releases the GIL, so this is a
pure speedup -- **verified by rerunning a sweep and matching the sequential
per-slate numbers digit for digit.** ~84% CPU, and a 288-build sweep runs in
~15 min instead of ~70. Each task gets its OWN scratch dir; sharing one would
reproduce the wipe-each-other bug at build level.

Two consequences worth knowing. Progress output now lands at the END, because
all builds finish before scoring -- a stdout counter every 10% was added,
since `tick()` is stderr-only and silences itself when redirected, so a
backgrounded sweep printed nothing at all. And the parallel run is CPU-bound,
so do not start a second sweep alongside it.

**The 2-game slates were dropping silently.** 36 "build failed" lines on the
09/06 sweep, exactly 9 per arm = 3 snapshots x 3 seeds x 4 arms. Not a
parallelism bug -- it is the `CASH_MIN_SPEND` failure documented below: a
<=4-game card routes through try_build_cash, and on 2 games the SP pair bans
two of four teams so all 8 hitters must come from the remaining two, which
cannot reach 48,000. Production works around it by hand with `--cash 0`; the
harness now retries the same way, but ONLY on total failure, so 3- and 4-game
cards that build fine keep their cash-style path and stay comparable with
earlier sweeps.

The replay set had quietly shrunk from 24 snapshots to 21 without saying so.
**Always read the build-failure count before the results table** -- an arm
comparison over a silently reduced slate set is exactly the four-slate trap
this file warns about, arriving through the back door.

## The replay harness had been broken since the ROI removal (fixed 09/03)

Three defects, all found on 09/03 while testing minspend49. Any replay result
quoted between the ROI removal and 09/03 came from a harness that could not
run, so it was measured before that and is still valid -- but nothing new had
been measurable in between.

  * `backtest_variants.py` imported `PAYOUT_TABLES` and `entry_payout` from
    `post_contest.py`, both deleted when payouts stopped being tracked. It
    died at import. It now scores on placement -- gap to the real 10th-place
    score and top-10 count -- which is what the objective actually is.
  * The paired verdict was a t-test on **dMean**. That is the exact proxy that
    shipped `spend15` (+10.60 on the mean, p=0.056, then lost all three live
    slates). It now tests `dBest`, prints dMean beside it so a
    ceiling-for-floor trade is visible, and prints **dN** -- an arm that
    underfills gets a lower `best` for free, and that has to be readable.
  * The scratch dir was a fixed `%TEMP%/bt_variants` that every build wipes
    before writing, so two sweeps running at once destroyed each other's
    inputs mid-build. Now per-PID.
  * `build_once` OMITTED `--variant` for the control arm, relying on the
    builder's default being the unconfigured builder. The moment minspend49
    shipped as that default (09/04), "control" silently rebuilt minspend49
    and a 340-build sweep compared the arm against itself. The tell was
    unmistakable and worth remembering: **dBest exactly +0.0, SE 0.0, "no
    spread across pairs"**, and a control row whose bestAvg/gapAvg/top10
    matched the previous sweep's minspend49 row to the digit. It now always
    passes `--variant`. A harness that infers an arm from a default is one
    shipping decision away from measuring nothing.

**A "min spend" arm never tested its own floor.** The min-spend ladder bends
the floor in 1,000 steps and then drops it entirely (`[base, base-1000,
base-2000, None]`), so `minspend47` portfolios contained lineups well under
47,000 and the arm was never evaluated at its nominal level. `--hard-min-salary`
disables the ladder. The floor then genuinely binds and the portfolio
UNDERFILLS instead -- 45 of 60 on the 09/03 snapshot -- which is the honest
behaviour, and `--seeds N` refills it.

**`--seeds N` merges portfolios across consecutive seeds.** Same builder
object, so `seen_sigs` keeps the merge deduped and every exposure counter
keeps binding across it; caps stay fractions of the ORIGINAL request, so a
refilled portfolio concentrates no more than a first-pass one. Verified on
09/03 morning by hand (a 3-game card gave 16 lineups on one seed, 67 across
six) and now built in. Note `make_specs` emits one spec per ALLOCATED stack
regardless of its `n_lineups` argument, which only sets tier proportions --
the refill must slice it to the shortfall or it builds a second full
portfolio (first attempt returned 84 of a requested 60).

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
    5,5,4 bigger primary stacks             4       -26.00     RETRACTED*
    secondary 3-man stack                   9        -6.39     no
    window ranked by batting order          4       -16.50     no
    hitter floor avg26 >= 4.0               9        -2.28     no
    hitter floor avg26 >= 6.0               4       -21.71     no
    hitter min-salary 3000 / 3500           4    -10.03/-18.54 no
    HARD_AVOID_BS 0 / 5 / 15 / 20           9   -5.15/0/-21.05/-21.05  keep 10

(*) 5,5,4 was 4 slates on the pre-09/03 harness. Retested 09/05 over 12 dates
on the fixed one it reads +5.8 dBest, t 3.41, top-10 up. See "Forcing 5-stacks
does NOT lose any more". Everything else in this table was scored on `best`
and stands -- but any 4-slate row here is provisional by the rule above.

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
on Vegas implied -2.59.

> **RETRACTED for the two stack-size entries, 09/05.** Both were measured on
> the pre-09/03 harness, whose paired verdict t-tested dMean. Retested on the
> fixed harness over 12 dates on top of minspend49, `stack554` reads **+5.8
> dBest (t 3.41) with top-10 count UP 15.0 -> 16.2** -- see "Forcing 5-stacks
> does NOT lose any more". It still does not beat minspend49 (t 0.84, 6-6), so
> nothing shipped changed, but the claim that stack-profile changes always
> lose is dead. The other four entries here stand; they were scored on `best`.

The reason for the ones that stand is always the same: the constraint gets
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

**`own_mean` is the first metric ever to read POSITIVE, on 3 slates. Do not
spend it yet.** The parked ownership measurement landed 09/03. `metric_study`
over 17 paired slates, Spearman against realised points:

    metric        08/31   09/02   09/03   POOLED   WITHIN
    own_mean      +0.37   +0.30   +0.37    +0.41    +0.34
    own_min       +0.17   +0.25   +0.21    +0.30    +0.23
    floor_target                           -0.06    -0.07
    Salary                                 -0.07    -0.12
    proj_points                            -0.02    -0.15
    ceiling                                -0.16    -0.11
    max_stack                              -0.02    -0.07

Every other candidate sits between -0.16 and -0.02 within-slate. own_mean is
+0.34, positive on all three slates it has, and beats Salary -- plausibly
because own_pct carries batting order and salary does not fully price it. It
is the only encouraging number in this file that is not a bug fix.

Two reasons it changes nothing today. Three slates is precisely the trap:
floor_target read **+0.63 on its first two** and settled at -0.07, and 08/30's
four-slate false positive is documented above. And spending it means SELECTING
lineups on own_mean, which is the fifth arm of a family where four have died
-- selection compresses spread (sd 27.20 -> 25.86 -> 25.19), so no scoring
function lengthens the right tail. Keep recording it. Revisit at ten slates,
and even then the lever is the problem, not the metric.

Note this is a different question from the ownership work that failed:
`--hitter-min-own` and the barbell gated individual HITTERS on own_pct. This
measures whether a LINEUP's mean projected ownership predicts its score. The
first is closed; the second is open and unresolved.

**The replay set is now ten slates** (09/03 evening pairs to a standings
file). Every sweep result below was measured on nine. The three closest calls
-- secondary stack (-6.39), pure-Vegas allocation (-2.59) and uniform window
sampling (-2.10) -- are the ones a tenth slate could move; the rest lost by
more than 10 and are not close. Nothing experimental is shipped: the builder
is the 08/30 build plus the doubleheader fix and the 09/02 bring-back fix.

**The 09/03 morning snapshot was overwritten by the evening build.** Two
slates on one date share `Snapshots\09_03_2026` and the upload filename. The
builder preserves the upload CSV automatically (`_prev0855`), but NOT the
snapshot directory -- so the morning 3-game card is not in the replay set and
cannot be recovered. Copy the snapshot dir aside before the second build of a
two-slate day.

**Every constraint tightening trades floor for ceiling.** Tighter fill caps,
bigger stacks, hitter floors, a higher SP ban -- each one lifts the bottom of
the portfolio and shortens the top. Seven ideas, seven dead, and this is the
shape of all of them. `best` is the objective, so the trade is always the
wrong way round.

## Control is not better in small contests -- it is worst there

Asked on 09/05 on the intuition that a small field suits the simpler builder.
The replay set spans 475 to 7,134 entries; splitting the 5-seed sweep by
field size, dBest vs control and top-10 count per portfolio:

    field    pairs |  mspend  stack554 stack555 |  ctrl  mspend  st554
    <1k        30  |  +5.68    +8.71    +6.88   |  0.13   0.53   0.60
    1-2k       50  |  +2.16    +2.94    +2.85   |  0.90   1.00   1.04
    2-4k       15  |  +3.89    +5.09    +4.68   |  0.27   0.60   0.73
    4k+         5  | +10.62   +18.33   +12.40   |  0.00   0.00   0.00

Control loses in every bucket, and sub-1k is where its top-10 rate is worst
relative to the arms: 0.13 per portfolio against 0.53. (The 4k+ cell is 5
pairs from one contest -- ignore it.)

The live case is cleaner than the table. 09/04's only top-10 was the
890-entry contest, bar 143.20; control rebuilt on that snapshot across five
seeds produced best lineups of 134.05 / 136.60 / 141.00 / 127.80 / 123.40 --
**its ceiling never reached the bar from any draw**. There is a mechanism: a
small field has a lower 10th-place bar, so converting depends on the best
lineup clearing a reachable number, and control's ~3,000 of unspent salary
caps exactly that. Small contests are where the floor matters MOST, not least.

## Daily flow

The interpreter is `C:\Users\CHAT2\anaconda3\python.exe`. Bare `python` on
PATH resolves to the Windows Store stub, which has no pandas and fails at
`import pandas`; the Bash tool cannot execute it at all ("Permission denied").
`run_slate_build.bat` activates the conda env, so it is only direct invocation
that needs the full path. Set `PYTHONUTF8=1` when redirecting output.

    python preflight.py                  # gate: are lineups posted?
    python filtered_DK_Salaries.py       # match the two load/ downloads
    python mlb_odds.py --csv mlb_odds.csv
    python vegas_sp_adjust.py
    python build_portfolio.py --lineups 60
    python make_entries.py [--duplicates]

**The shipped arm is `minspend49cov` as of 09/07** (`minspend49` from 09/04 to
09/07, control before that). Both defaults point at it -- `--variant` in
build_portfolio and `--arms` in make_entries -- so `run_slate_build.bat`,
which calls the builder bare, picks it up with no edit. The arm carries its
own refill count (`"seeds": 8`) because a hard floor underfills, 45 of 60 on
09/03; shipping that as a flag would have meant remembering `--seeds` at 6pm
or silently entering short.

`minspend49cov` = `minspend49` plus a per-team 5-stack guarantee that fires
only at <=4 games, so on any normal card it is byte-identical to the arm it
replaced. `--variant minspend49` drops the guarantee, `--variant control` the
older builder, `--variant none` the unsuffixed filenames.

**Cash lineups are opt-in since 09/07.** No slate size builds them by default,
so `--cash 0` is never needed any more -- and 2-game cards no longer fail.

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

**Doubleheaders duplicated hitters in the pool, and it cost a top-10.** The
lineups feed carries one row per `game_number`, so on a twin bill the same
player appears twice at DIFFERENT batting orders -- 08/29 had 27, e.g.
Ceddanne Rafaela batting 2nd in game 1 (confirmed) and 4th in game 2 (a
projection). Under `--allow-unconfirmed` both rows pass, so one player could
fill two slots of a supposedly contiguous window. `build_hitter_pool` now
keeps one row per player, preferring the confirmed one. Best is unchanged on
the seven single-header slates and goes 195.90 -> 211.35 on 08/29's
doubleheader -- 12.65 ABOVE that contest's 10th-place score of 198.70. This
only bites under `--allow-unconfirmed`, which is exactly the morning build.

**Watch for outside commits mid-session.** `ff86cb5` ("update build",
committed from GitHub Desktop while experiments were running) captured an
in-flight patch -- uniform window sampling, `randrange(0, len(wins))` -- that
was about to be reverted. It measured -2.10 on best and failed the audit
outright on one slate. Restored in 1746704. While a sweep is running the
working tree is a scratch surface, not a state worth committing.

**09/05 WAS THE BEST DAY RECORDED: 3 top-10 finishes in 4 contests.** Two
2-game cards, both `minspend49`, 79 entries. NOTE these exports were pulled
~20 min after the late games ended and have NOT been re-pulled -- by the
09/03 rule the +0.40 in particular could move.

    contest       n    best     rank        10th      gap   slate
    195058895    20   134.75      4/1189   129.75    +5.00   early
    195058896    19   134.75      6/1189   133.75    +1.00   early
    195060012    20   137.85      7/1189   137.45    +0.40   night
    195062373    20   131.45     30/2330   137.85    -6.40   night

The one miss had the highest bar and the largest field. Night portfolio mean
103.52 and median percentile 10.7% in the Dime Time -- half our entries there
finished in the top 11%, the best distribution recorded.

**Count DISTINCT lineups, not entries, when duplicating.** "2 of 20" and "3 of
20 in the top 10" were ONE lineup entered 2x and 3x (DupCount in
post_entries). Four top-10 ENTRIES on the day, three top-10 LINEUPS, and each
contest's was a single construction. Duplicates pay independently but are not
independent draws.

**A banned arm was the best pitcher on the slate, for the 3rd time in 12.**
09/05 night: Jeffrey Springs, `adj_bs 5.57`, banned by HARD_AVOID_BS=10,
scored **29.35 -- the highest of the four SPs -- at 19.7% ownership** against
Glasnow's 73.1% and Kirby's 57.6%. Splitting the 12 entered lineups:

    with Springs    n=6   mean 125.82   best 137.85   cleared the bar: 1
    without         n=6   mean  79.67   best 108.70   cleared the bar: 0

The top-10 lineup WAS a Springs lineup. With the ban left on, this card builds
2 lineups (only 2 legal cross-game SP pairs survive), neither with Springs,
ceiling ~108.70 against a 137.45 bar -- a guaranteed miss.

This does NOT reopen the level. The sweep still stands: 0 cost -5.15 and 15/20
cost -21.05 over 9 normal slates, and banned arms average 10.91 pts/start
against 13.42. What it establishes is narrower and operational: **on a card
where the ban removes legal SP PAIRS rather than merely disfavouring an arm,
lift it.** `--hard-avoid-bs` now exists for exactly that, defaults to 10, and
prints a warning when overridden. Check the legal-pair count before the seed
count on any <=3-game card.

**Seeds only help once the SP pair pool is wide enough.** Same day, same flag,
opposite answers -- replayed against the real standings:

    early slate   seeds 8 -> 9 lineups     seeds 40 -> 9 lineups   (identical
                  best 134.75, ranks 4 and 6 either way)
    night slate   seeds 8 -> 7 lineups     seeds 40 -> 12 lineups

The early card had all 4 arms eligible, so 4 legal pairs, and the binding
constraint was the DEDUP rule (2+ different players from every existing
lineup) -- more RNG paths cannot get past that, and seeds 9-40 found nothing.
The night card was pair-starved until the ban came off, and only then did
extra seeds have anywhere to go. Diagnose which wall you are against before
spending time on either.

**A 2-GAME CARD BUILDS ZERO LINEUPS ON THE DEFAULT PATH. Use `--cash 0`.**
09/05, 2 games / 4 teams / 36 batters, everything confirmed. The build
returned 0 lineups and then CRASHED writing the empty portfolio
(`ValueError: Length mismatch: Expected axis has 0 elements` at `up.columns =
cols`). Control returned 0 as well, so this is the slate rule, not the arm.

The mechanism: `SMALL_SLATE_GAMES = 4` routes a <=4-game card entirely
through `try_build_cash`, which enforces `CASH_MIN_SPEND = 48000`. With two
games the SP pair opposes two of the four teams, so all 8 hitters must come
from the REMAINING TWO -- and that pool cannot reach 48,000 at legal
positions. All 16,000 attempts failed "no valid construction".

`--cash 0` forces the GPP path and it builds: 9 lineups minspend49, 8
control. Nine is the slate's true ceiling -- `--lineups 120` also returned
exactly 9, so unlike 08/29 the caps are NOT binding and asking for more does
not help. Fill the rest with `make_entries.py --duplicates` and accept that
39 entries are 9 distinct shots.

Not fixed in code: the small-slate threshold should probably fall to 3 games,
or CASH_MIN_SPEND should scale with the number of eligible hitter teams. Left
alone because a 2-game card is rare and the workaround is one flag -- but the
CRASH on an empty portfolio is a real defect worth fixing whenever touched.

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
