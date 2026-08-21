@echo off
rem ============================================================
rem  MORNING STATS BUILD (scheduled) — needs only the BigDataBall
rem  feed, which updates overnight. Slate-dependent steps (DK
rem  salary filter, odds pull, Vegas adjust) live in
rem  run_slate_build.bat — run THAT after downloading the day's
rem  Lineups + DKSalaries CSVs into G:\My Drive\DK\load.
rem ============================================================
cd /d "C:\Users\CHAT2\MLB Draft Kings"
call C:\Users\CHAT2\anaconda3\Scripts\activate.bat

rem Redirected output defaults Python to cp1252, which crashes on the
rem checkmark/box-drawing characters the scripts print. Force UTF-8.
set PYTHONUTF8=1

set "LOG=%~dp0run_daily_prep_error.log"
set "RUNLOG=%~dp0last_run.log"
echo ===== morning stats run started %DATE% %TIME% ===== >> "%LOG%"
echo ===== morning stats run started %DATE% %TIME% ===== > "%RUNLOG%"

echo.
echo ============================================================
echo  Step 1 of 3 - Convert parquet
echo ============================================================
python parquet_convert.py >> "%RUNLOG%" 2>&1
if errorlevel 1 (
    echo ERROR: parquet_convert.py failed - see last_run.log >> "%LOG%"
    exit /b 1
)

echo.
echo ============================================================
echo  Step 2 of 3 - Base analysis
echo ============================================================
python base_analysis.py >> "%RUNLOG%" 2>&1
if errorlevel 1 (
    echo ERROR: base_analysis.py failed - see last_run.log >> "%LOG%"
    exit /b 1
)

echo.
echo ============================================================
echo  Step 3 of 3 - Comparison analysis
echo ============================================================
python comparison.py >> "%RUNLOG%" 2>&1
if errorlevel 1 (
    echo ERROR: comparison.py failed - see last_run.log >> "%LOG%"
    exit /b 1
)

echo.
echo Done.
echo ===== morning stats run finished %DATE% %TIME% ===== >> "%LOG%"
echo ===== morning stats run finished %DATE% %TIME% ===== >> "%RUNLOG%"
exit /b 0
