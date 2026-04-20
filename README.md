# BUVC Leaderboard — Modern Design

These two files replace the old `build_leaderboard.py` so running the script
produces the new branded design (`BUVC_Leaderboard.html`) by default.

## Install

Drop both files into your Hudl folder (next to `hudl_exports/`):

    Hudl/
      build_leaderboard.py          <-- replaces the existing one
      Leaderboard_template.html     <-- new file (required)
      hudl_exports/
        ...stats.txt

## Run

    python build_leaderboard.py

This writes `BUVC_Leaderboard.html` — a single self-contained file that
works offline. It includes three design directions you can switch between
via the in-page Tweaks panel:

  * Editorial — magazine-style masthead, podium, sortable table
  * Cards     — trading-card top 3 + compact card grid
  * Broadcast — dark ESPN-style dashboard with ticker

Click any player row/card to open a detail modal with their season stats
and club-wide category rankings.
