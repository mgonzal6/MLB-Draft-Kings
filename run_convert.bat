@echo off
cd /d "G:\My Drive\DK\code"
call C:\Users\CHAT2\anaconda3\Scripts\activate.bat

set "LOG=%~dp0run_daily_prep_error.log"
echo ===== run started %DATE% %TIME% ===== >> "%LOG%"

echo.
echo ============================================================
echo  Step 1 of 6 - Convert parquet
echo ============================================================
python parquet_convert.py
if errorlevel 1 (
    echo ERROR: parquet_convert.py failed. >> "%LOG%"
    exit /b 1
)

echo.
echo ============================================================
echo  Step 2 of 6 - Base analysis
echo ============================================================
python base_analysis.py
if errorlevel 1 (
    echo ERROR: base_analysis.py failed. >> "%LOG%"
    exit /b 1
)

echo.
echo ============================================================
echo  Step 3 of 6 - Comparison analysis
echo ============================================================
python comparison.py
if errorlevel 1 (
    echo ERROR: comparison.py failed. >> "%LOG%"
    exit /b 1
)

echo.
echo ============================================================
echo  Step 4 of 6 - Filter DK Salaries
echo ============================================================
python filtered_DK_Salaries.py
if errorlevel 1 (
    echo ERROR: filtered_DK_Salaries.py failed. >> "%LOG%"
    exit /b 1
)

echo.
echo ============================================================
echo  Step 5 of 6 - Pull MLB odds
echo ============================================================
python mlb_odds.py --csv mlb_odds.csv
if errorlevel 1 (
    echo WARNING: mlb_odds.py failed - odds not updated. >> "%LOG%"
)

echo.
echo ============================================================
echo  Step 6 of 6 - Vegas implied totals + SP adjust
echo ============================================================
if not exist "G:\My Drive\DK\export\mlb_odds.csv" (
    echo WARNING: mlb_odds.csv missing - skipped vegas_sp_adjust, build falls back to no-Vegas. >> "%LOG%"
    echo WARNING: mlb_odds.csv not found - skipping Vegas step.
) else (
    python vegas_sp_adjust.py
    if errorlevel 1 (
        echo WARNING: vegas_sp_adjust.py failed - vegas.csv not updated. >> "%LOG%"
    )
)

echo.
echo Done.
echo ===== run finished %DATE% %TIME% ===== >> "%LOG%"
exit /b 0
