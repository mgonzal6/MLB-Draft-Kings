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
shipping one, and `minspend49` has NO live record at all. Its first live
slate is the real test, and one slate will not settle it either way.

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
below. It has NO live record; the first live slate is still the real test.

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

**The shipped arm is `minspend49` as of 09/04, not control.** Both defaults
now point at it -- `--variant` in build_portfolio and `--arms` in
make_entries -- so `run_slate_build.bat`, which calls the builder bare, picks
it up with no edit. The arm also carries its own refill count (`"seeds": 8`)
because a hard floor underfills, 45 of 60 on 09/03; shipping it as a flag
would have meant remembering `--seeds` at 6pm or silently entering short.
`--variant control` restores the previous builder, `--variant none` the older
unsuffixed filenames.

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
