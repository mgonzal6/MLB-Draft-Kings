import pandas as pd
import os
import re # We need the regex library for advanced text replacing

# Define your specific folder path
folder_path = r"G:\My Drive\DK\load"
folder_path_export = r"G:\My Drive\DK\export"

lineups_path = None
dk_salaries_path = None

# Loop through all files to find matches
try:
    for filename in os.listdir(folder_path):
        filename_lower = filename.lower()
        if 'lineup' in filename_lower and filename_lower.endswith('.csv'):
            lineups_path = os.path.join(folder_path, filename)
        elif 'dksalaries' in filename_lower and filename_lower.endswith('.csv'):
            dk_salaries_path = os.path.join(folder_path, filename)
except FileNotFoundError:
    print(f"Error: The folder '{folder_path}' could not be found.")

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
    lineups_df = pd.read_csv(lineups_path)
    dk_salaries_df = pd.read_csv(dk_salaries_path)

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

    # 3. Extract the unique lists of players for mutual filtering
    lineup_names = lineups_df['Match_Name'].unique()
    dk_names = dk_salaries_df['Match_Name'].unique()

    # 4. Create masks and filter BOTH dataframes
    # Filter DK Salaries based on Lineup names
    dk_matched_mask = dk_salaries_df['Match_Name'].isin(lineup_names)
    filtered_dk_salaries = dk_salaries_df[dk_matched_mask].copy()
    unmatched_dk_salaries = dk_salaries_df[~dk_matched_mask].copy()

    # Filter Lineups based on DK Salaries names
    lineup_matched_mask = lineups_df['Match_Name'].isin(dk_names)
    filtered_lineups = lineups_df[lineup_matched_mask].copy()
    unmatched_lineups = lineups_df[~lineup_matched_mask].copy()

    # 5. Drop the temporary 'Match_Name' columns
    filtered_dk_salaries = filtered_dk_salaries.drop(columns=['Match_Name'])
    unmatched_dk_salaries = unmatched_dk_salaries.drop(columns=['Match_Name'])
    filtered_lineups = filtered_lineups.drop(columns=['Match_Name'])
    unmatched_lineups = unmatched_lineups.drop(columns=['Match_Name'])

    # 6. Save ALL datasets
    output_matched_dk_path = os.path.join(folder_path_export, "Filtered_DKSalaries.csv")
    output_unmatched_dk_path = os.path.join(folder_path, "Unmatched_DKSalaries.csv")
    
    output_matched_lineups_path = os.path.join(folder_path_export, "Filtered_Lineups.csv")
    output_unmatched_lineups_path = os.path.join(folder_path, "Unmatched_Lineups.csv")
    
    filtered_dk_salaries.to_csv(output_matched_dk_path, index=False)
    
    filtered_lineups.to_csv(output_matched_lineups_path, index=False)

   
    # Print an updated summary report
    print(f"Success! Files have been generated in your G: Drive:")
    print(f" - Matched lists saved to 'export' folder.")
    print(f" - Unmatched lists saved to 'load' folder.")
    print("-" * 40)
    print(f"Total players originally in DK file:      {len(dk_salaries_df)}")
    print(f"Total players originally in lineup file:  {len(lineups_df)}")
    print("-" * 40)
    print(f"Total MATCHED players in DK:              {len(filtered_dk_salaries)}")
    print(f"Total UNMATCHED players in DK:            {len(unmatched_dk_salaries)}")
    print("-" * 40)
    print(f"Total MATCHED rows in Lineups:            {len(filtered_lineups)}")
    print(f"Total UNMATCHED rows in Lineups:          {len(unmatched_lineups)}")