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
    python build_portfolio.py                # 5 cash + 15 GPP lineups, seed 42
    python build_portfolio.py --lineups 20 --cash 5 --seed 7
    python build_portfolio.py --cash 0       # pure GPP portfolio (old behavior)
    python build_portfolio.py --selftest     # audit-logic checks only, no files

Cash mode (--cash N, default 5): the first N lineups are floor-maximized for
single-entry double-ups — SP pairs from the top vegas-adj blended arms, hitters
by avg26 (floor) from top-half implied-total teams, mini-stacks <=3, no punts
below $3K, no darts. All lineups (cash + GPP) share uniqueness and exposure
caps because the whole file also enters the mass-entry GPP; the cash subset is
additionally written to DK_upload_cash_<date>.csv for the single-entry contests.

Strategy rules encoded (from the baseball-analyzer skill's Fix Registry):
    #6/#10  hard-fade offenses facing an SP with vegas-adj BS >= 55 or in the
            bottom implied-total tier: max 1 primary stack, <=25% appearances
    #8      bring-back: stacks in games with total >= 8.0 reserve one hitter
            slot for the opposing offense BEFORE fills (slot-aware)
    #9      stacks use contiguous batting-order windows (wrap allowed)
    #12     SP exposure: below-median vegas-adj blended -> <=20%; bottom two -> <=15%
    #13     SP tiers keyed on vegas-adjusted BLENDED (adj_bs is tiebreaker only)
    #14     stack allocation = 0.6*norm(top-4 hitter BS) + 0.4*norm(implied
            total), hard cap 3 primary stacks per team
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

Hard constraints (audited on every lineup before anything is written):
    salary <= 50000; DK position eligibility respected; <=5 hitters per team;
    no hitter opposing a rostered SP; players span >=2 games; no duplicate
    players; no duplicate lineups (and any two differ by >=2 players).
"""
import argparse
import random
import re
import sys
import zlib
from collections import defaultdict

import pandas as pd

EXPORT_DIR = r"G:\My Drive\DK\export"
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
MIN_FLOOR = 80              # skill minimum entry threshold


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
    slate_date = None
    for gi in dk["Game Info"].unique():
        m = re.match(r"(\w+)@(\w+)\s+(\d{2}/\d{2}/\d{4})", str(gi))
        if m:
            a, h, d = m.groups()
            opp_map[a], opp_map[h] = h, a
            game_map[a] = game_map[h] = f"{a}@{h}"
            slate_date = slate_date or d.replace("/", "_")

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
        sys.exit("ERROR: no confirmed SPs matched — check inputs.")

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

def allocate_stacks(hit_pool, sp_df, vegas, n_lineups, opp_map):
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
    alloc = (pool / pool.sum() * n_lineups).round().astype(int).clip(upper=3)
    order = pool.sort_values(ascending=False).index.tolist()
    while alloc.sum() > n_lineups:
        for t in reversed(order):
            if alloc[t] > 0:
                alloc[t] -= 1
                break
    # Top-up order: non-faded teams to cap 3, then faded teams to their Fix #6
    # maximum of 1; on tiny slates where even that can't fill the count, raise
    # the non-faded cap (never the faded one) until it fits.
    fade_order = [t for t in stackscore.sort_values(ascending=False).index
                  if t in fades]
    caps_t = {t: 3 for t in order}
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


def make_specs(alloc, n_lineups):
    """Tier assignment: ~20% ceiling (5-stacks), ~20% contrarian (3-stacks),
    rest core (4-stacks)."""
    n_ceil = max(1, round(n_lineups * 0.2))
    n_cont = max(1, round(n_lineups * 0.2))
    stack_list = [t for t, n in alloc.items() for _ in range(n)]
    specs, first_seen = [], set()
    for idx, team in enumerate(stack_list):
        if idx < n_ceil and team not in first_seen:
            tier, size = "CEILING", 5
        elif idx >= len(stack_list) - n_cont:
            tier, size = "CONTRARIAN", 3
        else:
            tier, size = "CORE", 4
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
        self.cash_hitter_cap = 3
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
        # Try the preferred tier first; if it can't produce a single valid
        # pair (small slates: same-game clashes, pair caps), fall back to the
        # full eligible pool rather than failing the lineup outright.
        for pool in (pref, elig):
            if not pool:
                continue
            rng.shuffle(pool)
            pool.sort(key=lambda s: -(s["adj_blended"] + rng.uniform(0, 5)))
            pairs = []
            for a in pool:
                for b in pool:
                    if a["name"] == b["name"]:
                        continue
                    if self.game_map[a["team"]] == self.game_map[b["team"]]:
                        continue
                    key = tuple(sorted((a["name"], b["name"])))
                    if self.pair_use[key] >= PAIR_CAP:
                        continue
                    if (b["name"], a["name"]) not in [(y["name"], x["name"])
                                                      for x, y in pairs]:
                        pairs.append((a, b))
                    if len(pairs) >= 6:
                        return pairs
            if pairs:
                return pairs
        return []

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
                    continue
                lu_, salary = res
                s_new = set(self.sig(lu_))
                if any(len(s_new - set(s0)) < 2 for s0 in self.seen_sigs):
                    continue
                floor = (lu_["SP1"]["blended"] + lu_["SP2"]["blended"]
                         + sum(sorted((lu_[s]["avg26"] for s in HITTER_SLOTS),
                                      reverse=True)[:5]) * 2.5)
                if floor < MIN_FLOOR:
                    continue
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
        for slot in open_slots:
            rem = sum(1 for s in HITTER_SLOTS if s not in placed) - 1
            budget = SALARY_CAP - salary - rem * 2000
            cands = [h for h in self.hit_pool
                     if slot in h["slots"] and h["name"] not in used
                     and h["team"] not in banned and tcount[h["team"]] < 5
                     and h["salary"] <= budget
                     and (h["team"] == stack_t                       # Fix #15
                          or self.fill_appear[h["name"]] < fill_cap)
                     and not (h["team"] in self.fades
                              and self.fade_appear[h["team"]]
                              + local_fade[h["team"]]
                              + self.fade_reserved.get(h["team"], 0) >= fade_cap)]
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

        if salary > SALARY_CAP:
            return None
        lu_ = {"SP1": sp1, "SP2": sp2, **placed}
        if self.sig(lu_) in self.seen_sigs:
            return None
        if len({self.game_map[p["team"]] for p in lu_.values()}) < 2:
            return None
        return lu_, salary

    def build_all(self, specs):
        for spec in specs:
            built = False
            for attempt in range(500):
                rng = stable_rng(self.seed, spec["stack"], spec["tier"], attempt)
                res = self.try_build(spec, rng)
                if not res:
                    continue
                lu_, salary = res
                s_new = set(self.sig(lu_))
                if any(len(s_new - set(s0)) < 2 for s0 in self.seen_sigs):
                    continue
                floor = (lu_["SP1"]["blended"] + lu_["SP2"]["blended"]
                         + sum(sorted((lu_[s]["avg26"] for s in HITTER_SLOTS),
                                      reverse=True)[:5]) * 2.5)
                if floor < MIN_FLOOR:
                    continue
                self.seen_sigs.add(self.sig(lu_))
                self.sp_use[lu_["SP1"]["name"]] += 1
                self.sp_use[lu_["SP2"]["name"]] += 1
                self.pair_use[tuple(sorted((lu_["SP1"]["name"], lu_["SP2"]["name"])))] += 1
                for s in HITTER_SLOTS:
                    if (lu_[s]["team"] in self.fades
                            and lu_[s]["team"] != spec["stack"]):
                        self.fade_appear[lu_[s]["team"]] += 1
                    if lu_[s]["team"] != spec["stack"]:              # Fix #15
                        self.fill_appear[lu_[s]["name"]] += 1
                self.lineups.append({"spec": spec, "lineup": lu_,
                                     "salary": salary, "floor": floor})
                built = True
                break
            if not built:
                print(f"  !! no unique valid lineup for {spec['stack']} "
                      f"({spec['tier']}) — skipping, never duplicating")
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
                    help="floor-maximized lineups (default 5, or ALL on a "
                         "<=4-game slate; 0 = pure GPP)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--export", default=EXPORT_DIR)
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
        args.cash = args.lineups if n_games <= SMALL_SLATE_GAMES else 5
        if n_games <= SMALL_SLATE_GAMES:
            print(f"  small slate ({n_games} games <= {SMALL_SLATE_GAMES}): "
                  f"building the whole portfolio cash-style (Fix #18)")

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
    alloc, impl, fades = allocate_stacks(hit_pool, sp_df, vegas, n_gpp, opp_map)
    print(f"\nHard fades (≤25% appearances): {sorted(fades)}")
    print(f"Primary-stack allocation ({n_gpp} GPP lineups):")
    for t, n in alloc.items():
        print(f"  {t}: {n}  (implied {impl.get(t, float('nan')):.2f})")

    specs = make_specs(alloc, n_gpp)
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
    lineups = b.build_all(specs)

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
        srows.append({"#": i + 1, "tier": L["spec"]["tier"], "stack": L["spec"]["stack"],
                      "SP1": lu_["SP1"]["name"], "SP2": lu_["SP2"]["name"],
                      "teams": " ".join(f"{t}x{n}" for t, n in
                                        sorted(tc.items(), key=lambda x: -x[1])),
                      "salary": L["salary"], "floor": round(L["floor"], 1)})
    cols = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
    up = pd.DataFrame(rows)
    up.columns = cols
    up_path = f"{args.export}\\DK_upload_{slate_date}.csv"
    up.to_csv(up_path, index=False)
    summary = pd.DataFrame(srows)
    sum_path = f"{args.export}\\portfolio_summary_{slate_date}.csv"
    summary.to_csv(sum_path, index=False)

    cash_idx = [i for i, L in enumerate(lineups) if L["spec"]["tier"] == "CASH"]
    if cash_idx:
        cash_up = pd.DataFrame([rows[i] for i in cash_idx])
        cash_up.columns = cols
        cash_path = f"{args.export}\\DK_upload_cash_{slate_date}.csv"
        cash_up.to_csv(cash_path, index=False)

    print(f"\n{summary.to_string(index=False)}")
    print("\nSP exposure:")
    for n, c in sorted(b.sp_use.items(), key=lambda x: -x[1]):
        print(f"  {n}: {c}/{caps[n]}")
    print(f"\nwrote {up_path}")
    print(f"wrote {sum_path}")
    if cash_idx:
        print(f"wrote {cash_path}  ({len(cash_idx)} single-entry cash lineups)")


if __name__ == "__main__":
    main()
