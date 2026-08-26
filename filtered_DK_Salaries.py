import pandas as pd
import os
import re # We need the regex library for advanced text replacing

import slate_io

# Define your specific folder path
folder_path = r"G:\My Drive\DK\load"
folder_path_export = r"G:\My Drive\DK\export"

# The Lineups feed and DK disagree on three abbreviations. Same map as
# build_portfolio.py and vegas_sp_adjust.py — keep the three in sync.
ABBR_REMAP = {"OAK": "ATH", "WAS": "WSH", "CHW": "CWS"}

# Every team on the slate fields 9 batters + 1 SP in the lineups feed.
ROWS_PER_TEAM = 10

lineups_path = None
dk_salaries_path = None

# Shared with preflight.py so the two cannot disagree about which file is
# today's input. It also accepts .xlsx: the lineups feed served one on 08/26
# and a .csv-only filter reports it as missing, which reads as "lineups not
# posted" rather than "wrong extension". Unmatched_*.csv now goes to 'export',
# but a stray copy left in 'load' would otherwise be picked up as an INPUT —
# those names contain 'lineup'/'dksalaries' and sort after the real downloads,
# so last-match-wins would silently choose them; find_inputs skips them.
if not os.path.isdir(folder_path):
    print(f"Error: The folder '{folder_path}' could not be found.")
else:
    lineups_path, dk_salaries_path = slate_io.find_inputs(folder_path)

if not lineups_path or not dk_salaries_path:
    print("Error: Missing either the Lineups or DKSalaries file.")
else:
    print(f"Found Lineups file: {os.path.basename(lineups_path)}")
    print(f"Found DK Salaries file: {os.path.basename(dk_salaries_path)}")

    # Staleness warning — these are manual downloads from DK; if they haven't
    # been refreshed, everything downstream (filtering, Vegas adjust) is built
    # on an old slate.
    import time
    for label, path in (("Lineups", lineups_path), ("DKSalaries", dk_salaries_path)):
        age_days = (time.time() - os.path.getmtime(path)) / 86400
        if age_days > 1:
            print(f"*** WARNING: {label} file is {age_days:.0f} days old "
                  f"({os.path.basename(path)}) — download today's file from DK. ***")
    print("-" * 40)

    # 1. Load the datasets
    lineups_df = slate_io.read_table(lineups_path)
    dk_salaries_df = slate_io.read_table(dk_salaries_path)

    # 2. Clean column headers
    lineups_df.columns = lineups_df.columns.str.strip()
    dk_salaries_df.columns = dk_salaries_df.columns.str.strip()

    # --- SUPERCHARGED NORMALIZATION STEP ---
    def normalize_names(name_series):
        # A. Make everything lowercase
        s = name_series.str.lower()

        # B. Remove all punctuation (periods, commas, hyphens)
        s = s.replace(r'[^\w\s]', '', regex=True)

        # C. Remove common suffixes (jr, sr, ii, iii, iv) ONLY as separate words
        # The \b ensures we don't accidentally remove "jr" from the middle of a name
        s = s.replace(r'\b(jr|sr|ii|iii|iv)\b', '', regex=True)

        # D. Remove any single-letter middle initials (like the 'v' in Louis V Garcia)
        s = s.replace(r'\b[a-z]\b', '', regex=True)

        # E. Nickname Dictionary
        # Add any first-name mismatches here. The code will swap the long name for the short one.
        nicknames = {
            r'\bmichael\b': 'mike',
            r'\bleonardo\b': 'leo',
            r'\bmatthew\b': 'matt',
            r'\bchristopher\b': 'chris'
        }
        for long_name, short_name in nicknames.items():
            s = s.replace(long_name, short_name, regex=True)

        # F. Clean up any extra blank spaces left behind by deleted words
        s = s.replace(r'\s+', ' ', regex=True).str.strip()

        return s
    # --------------------------------------

    # Apply the normalizer
    lineups_df['Match_Name'] = normalize_names(lineups_df['player name'])
    dk_salaries_df['Match_Name'] = normalize_names(dk_salaries_df['Name'])

    # 3. Exact match on the normalized name (the original behaviour).
    lineup_names = set(lineups_df['Match_Name'])
    dk_names = set(dk_salaries_df['Match_Name'])

    lu_hit = lineups_df['Match_Name'].isin(dk_names)
    dk_hit = dk_salaries_df['Match_Name'].isin(lineup_names)

    # 3b. RESCUE PASS
    # The exact match silently drops players whose two feeds disagree on a
    # first name: the Lineups feed said "Donnie Walton" where DK said
    # "Donovan Walton", so a 2900-salary ATH infielder in the posted lineup
    # was invisible to the builder on both 8/23 and 8/24. The nickname dict
    # above can't scale to every such pair, so retry each leftover lineup row
    # against DK players on the SAME team, keyed on last name + first initial.
    # Accept only when exactly one DK player fits, so two Waltons on one
    # roster can never be cross-matched; requiring the first initial keeps an
    # SP from matching a same-surname reliever.
    lu_team = (lineups_df['team code'].astype(str).str.strip()
               .str.upper().replace(ABBR_REMAP))
    dk_team = (dk_salaries_df['TeamAbbrev'].astype(str).str.strip()
               .str.upper().replace(ABBR_REMAP))
    lu_last = lineups_df['Match_Name'].str.split().str[-1]
    dk_last = dk_salaries_df['Match_Name'].str.split().str[-1]
    lu_init = lineups_df['Match_Name'].str[:1]
    dk_init = dk_salaries_df['Match_Name'].str[:1]

    rescued = []
    for i in lineups_df.index[~lu_hit]:
        cand = dk_salaries_df.index[(~dk_hit)
                                    & (dk_team == lu_team[i])
                                    & (dk_last == lu_last[i])
                                    & (dk_init == lu_init[i])]
        if len(cand) != 1:
            continue
        j = cand[0]
        rescued.append((lineups_df.at[i, 'player name'],
                        dk_salaries_df.at[j, 'Name'], lu_team[i]))
        # Adopt DK's spelling. build_portfolio.py re-matches lineups against
        # salaries by name, so leaving the feed's spelling in place would just
        # move the same drop one step downstream.
        lineups_df.at[i, 'player name'] = dk_salaries_df.at[j, 'Name']
        lineups_df.at[i, 'Match_Name'] = dk_salaries_df.at[j, 'Match_Name']
        lu_hit.at[i] = True
        dk_hit.at[j] = True

    # 4. Split both frames on the final masks.
    filtered_dk_salaries = dk_salaries_df[dk_hit].drop(columns=['Match_Name'])
    unmatched_dk_salaries = dk_salaries_df[~dk_hit].drop(columns=['Match_Name'])
    filtered_lineups = lineups_df[lu_hit].drop(columns=['Match_Name'])
    unmatched_lineups = lineups_df[~lu_hit].drop(columns=['Match_Name'])

    # 5. Save ALL datasets. The unmatched paths used to be built here but the
    # files were never actually written, which is why the Walton drop went
    # unnoticed for days. All four go to 'export' — 'load' is kept to just the
    # two manual DK downloads.
    output_matched_dk_path = os.path.join(folder_path_export, "Filtered_DKSalaries.csv")
    output_unmatched_dk_path = os.path.join(folder_path_export, "Unmatched_DKSalaries.csv")

    output_matched_lineups_path = os.path.join(folder_path_export, "Filtered_Lineups.csv")
    output_unmatched_lineups_path = os.path.join(folder_path_export, "Unmatched_Lineups.csv")

    filtered_dk_salaries.to_csv(output_matched_dk_path, index=False)
    unmatched_dk_salaries.to_csv(output_unmatched_dk_path, index=False)

    filtered_lineups.to_csv(output_matched_lineups_path, index=False)
    unmatched_lineups.to_csv(output_unmatched_lineups_path, index=False)

    # 6. Summary report
    print("Success! Files have been generated in your G: Drive:")
    print(" - Matched and unmatched lists saved to 'export' folder.")
    print("-" * 40)
    print(f"Total players originally in DK file:      {len(dk_salaries_df)}")
    print(f"Total players originally in lineup file:  {len(lineups_df)}")
    print("-" * 40)
    print(f"Total MATCHED players in DK:              {len(filtered_dk_salaries)}")
    print(f"Total UNMATCHED players in DK:            {len(unmatched_dk_salaries)}")
    print("-" * 40)
    print(f"Total MATCHED rows in Lineups:            {len(filtered_lineups)}")
    print(f"Total UNMATCHED rows in Lineups:          {len(unmatched_lineups)}")

    if rescued:
        print("-" * 40)
        print(f"Name mismatches rescued ({len(rescued)}) - DK spelling adopted:")
        for lu_name, dk_name, team in rescued:
            print(f"  {team}: lineups '{lu_name}'  ->  DK '{dk_name}'")

    # 7. SLATE INTEGRITY
    # The totals above can't tell you whether 139 is the right number. Every
    # slate team should contribute ROWS_PER_TEAM lineup rows, so a shortfall
    # is either a player dropped by name matching or a lineup the feed hasn't
    # posted yet. Those two need different responses, so report them apart.
    slate_teams = sorted(set(dk_team))
    kept_per_team = lu_team[lu_hit].value_counts()
    lost_per_team = lu_team[~lu_hit].value_counts()
    expected = len(slate_teams) * ROWS_PER_TEAM
    matched_on_slate = int(sum(kept_per_team.get(t, 0) for t in slate_teams))

    print("-" * 40)
    print(f"Slate integrity: {len(slate_teams)} teams x {ROWS_PER_TEAM} = "
          f"{expected} rows expected, {matched_on_slate} matched")

    dropped, not_posted = [], []
    for t in slate_teams:
        gap = ROWS_PER_TEAM - int(kept_per_team.get(t, 0))
        if gap <= 0:
            continue
        (dropped if lost_per_team.get(t, 0) else not_posted).append((t, gap))

    lost_team_of_row = lu_team[~lu_hit]
    for t, gap in dropped:
        names = unmatched_lineups[(lost_team_of_row == t).values]
        listed = ", ".join(f"{r['player name']} (batting {r['batting order']})"
                           for _, r in names.iterrows())
        print(f"  *** {t} short {gap} - dropped by name matching: {listed} ***")
    for t, gap in not_posted:
        print(f"  {t} short {gap} - no unmatched rows, lineup not posted yet")
    if dropped:
        print(f"  -> review {output_unmatched_lineups_path}")
    elif not not_posted:
        print("  OK - every slate team is fully accounted for.")
