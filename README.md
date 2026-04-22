# BUVC Leaderboard

Public web dashboard of season stats for **Boston United Volleyball Club**,
built from Hudl exports.

**Live:** https://scgrandpre.github.io/buvc-leaderboard/

## What's here

- `index.html` — root page served by GitHub Pages (currently mirrors the broadcast view)
- `BUVC_Leaderboard_broadcast.html` — current built dashboard
- `BUVC_Leaderboard_broadcast_template.html` — frozen template; rebuilds patch this
- `build_leaderboard.py` — rebuilds the dashboard from Hudl stats exports

## Data & privacy

Stats are pulled from Hudl with club permission. The dashboard displays:

- Full first + last names of rostered players (youth athletes)
- Season-to-date aggregated stats (kills, digs, aces, attack %, etc.)

No contact info, birthdates, or addresses. If a parent or player wants
their name removed or displayed as first-name-only, reach out and we'll
redeploy within the day.

## Rebuilding

End-to-end workflow + pipeline details are in the parent project's
`RUNBOOK.md` (not public). Short version:

```bash
python3 hudl_stats_exporter.py   # pick BUVC, pulls data
python3 build_leaderboard.py     # pick BUVC, writes this file
git add -A && git commit -m "Refresh" && git push
```
