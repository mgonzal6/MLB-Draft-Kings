@echo off
rem ============================================================
rem  SLATE BUILD (run by hand) — double-click AFTER downloading
rem  today's Lineups + DKSalaries CSVs into G:\My Drive\DK\load.
rem  Filters the slate, pulls fresh odds (so lines are current,
rem  not 12 hours old), and writes vegas.csv + the adjusted SP
rem  table. Output stays on screen; details also go to
rem  last_slate_build.log.
rem ============================================================
cd /d "C:\Users\CHAT2\MLB Draft Kings"
call C:\Users\CHAT2\anaconda3\Scripts\activate.bat

rem Redirected output defaults Python to cp1252, which crashes on the
rem checkmark/box-drawing characters the scripts print. Force UTF-8.
set PYTHONUTF8=1

set "LOG=%~dp0run_daily_prep_error.log"
set "RUNLOG=%~dp0last_slate_build.log"
echo ===== slate build started %DATE% %TIME% ===== >> "%LOG%"
echo ===== slate build started %DATE% %TIME% ===== > "%RUNLOG%"

echo.
echo ============================================================
echo  Step 1 of 3 - Filter DK salaries + lineups
echo ============================================================
python filtered_DK_Salaries.py
if errorlevel 1 (
    echo ERROR: filtered_DK_Salaries.py failed. >> "%LOG%"
    echo.
    echo *** filtered_DK_Salaries.py FAILED - fix the load files and rerun. ***
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Step 2 of 3 - Pull MLB odds
echo ============================================================
python mlb_odds.py --csv mlb_odds.csv >> "%RUNLOG%" 2>&1
if errorlevel 1 (
    echo WARNING: mlb_odds.py failed - odds not updated. >> "%LOG%"
    echo *** mlb_odds.py FAILED - see last_slate_build.log. Continuing with existing odds file. ***
) else (
    echo Odds pulled - full board saved to last_slate_build.log.
)

echo.
echo ============================================================
echo  Step 3 of 3 - Vegas implied totals + SP adjust
echo ============================================================
if not exist "G:\My Drive\DK\export\mlb_odds.csv" (
    echo WARNING: mlb_odds.csv missing - skipped vegas_sp_adjust. >> "%LOG%"
    echo *** mlb_odds.csv not found - skipping Vegas step. ***
) else (
    python vegas_sp_adjust.py
    if errorlevel 1 (
        echo WARNING: vegas_sp_adjust.py failed - vegas.csv not updated. >> "%LOG%"
        echo.
        echo *** vegas_sp_adjust.py FAILED - read the message above. ***
    )
)

echo.
echo Slate build done.
echo ===== slate build finished %DATE% %TIME% ===== >> "%LOG%"
pause
exit /b 0
