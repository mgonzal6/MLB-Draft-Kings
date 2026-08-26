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

## Scripts are Linux-clean

`build_portfolio.py` is stdlib + pandas + `lineup_id`; nothing Windows-only.
The `G:\` paths are argparse defaults, overridable with `--export`. Keep it
that way so the harness can drive it from anywhere.
