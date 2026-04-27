#!/usr/bin/env python3
"""
Leaderboard builder — BUVC + NEVBC.

Reads Hudl stats exports and writes a single self-contained HTML dashboard
for the chosen club(s). Run it, pick BUVC / NEVBC / Both, done.

File layout it expects:

    Hudl/
      hudl_exports/            <- BUVC stats exports
      hudl_exports_nevbc/      <- NEVBC stats exports
      NEVBC_Leaderboard_template.html    <- NEVBC bundle (frozen)
      Hudl_exports_bundle/
        build_leaderboard.py   <- this file
        Leaderboard_template.html        <- BUVC bundle (placeholder-based)
        BUVC_Leaderboard.html            <- OUTPUT (BUVC)

NEVBC output overwrites  ../NEVBC_Leaderboard_broadcast.html  in the parent
Hudl/ folder. BUVC output overwrites BUVC_Leaderboard.html in this folder.
"""

import os
import re
import json
import glob
import base64
import gzip
import sys


# ----- Club registry ------------------------------------------------------
# Imported from clubs.py at the project root (single source of truth shared
# with hudl_stats_exporter.py).

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clubs import CLUBS_BUILD as CLUBS, prompt_club_choice  # noqa: E402


# ----- Stats parsing (unchanged from BUVC builder) -----------------------

def _num(raw):
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if s in ("", "-"):
        return 0.0
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load_roster(export_dir, team_name):
    safe = team_name.replace(" ", "_").replace("-", "_").replace("/", "_")
    path = os.path.join(export_dir, f"{safe}_roster.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj.get("roster", {}) or {}
    except Exception:
        return {}


def parse_file(filepath, export_dir):
    """Return {team, gender, age, players: [...]}. Robust to varying column
    orders: cells are mapped by header label.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    team_m   = re.search(r"^Team:\s*(.+)$",       content, re.M)
    gender_m = re.search(r"^Gender:\s*(.+)$",     content, re.M)
    age_m    = re.search(r"^Age Group:\s*(\d+)$", content, re.M)
    if not (team_m and gender_m and age_m):
        return None
    team   = team_m.group(1).strip()
    gender = gender_m.group(1).strip()
    age    = age_m.group(1).strip()

    lines = content.split("\n")
    name_idx = next((i for i, L in enumerate(lines) if L.strip() == "NAME"), None)
    if name_idx is None:
        return None

    header = []
    i = name_idx + 1
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1; continue
        if s.startswith("#") or s in ("Un-Identified", "My Team", "Opponent"):
            break
        header.append(s)
        i += 1

    def find(label, start=0):
        for idx in range(start, len(header)):
            if header[idx] == label:
                return idx
        return -1

    atk_k    = find("K")
    srv_sa   = find("SA")
    set_ast  = find("AST")
    dig_ds   = find("DS")
    gen_sets = find("SETS PLAYED")
    atk_ta   = find("TA", atk_k + 1) if atk_k >= 0 else -1
    srv_ta_end = set_ast if set_ast >= 0 else len(header)
    srv_ta = -1
    if srv_sa >= 0:
        for idx in range(srv_sa + 1, srv_ta_end):
            if header[idx] == "TA":
                srv_ta = idx; break
    atk_pct  = find("ATK%", atk_k + 1 if atk_k >= 0 else 0)
    kill_pct = find("KILL%")
    ks       = find("K/S")
    srv_pct  = -1
    if srv_sa >= 0:
        for idx in range(srv_sa + 1, srv_ta_end):
            if header[idx] == "PCT":
                srv_pct = idx; break
    pass_pct = find("PASS%")  # Serve Receive PASS%
    # SR TA is the "TA" column that sits just before the optional "?" then PASS%
    sr_ta = -1
    if pass_pct > 0:
        for idx in range(pass_pct - 1, max(0, pass_pct - 8), -1):
            if header[idx] == "TA":
                sr_ta = idx; break

    players = []
    roster = load_roster(export_dir, team)

    # Build a short-form lookup: "Claire Jan" -> "claire j." so we can
    # upgrade stats-row names ("#2 Claire J.") to full roster names.
    # We prefer this over jersey-based lookup because Hudl's stats
    # tagging sometimes uses different jersey numbers than the roster
    # page (player swaps jerseys mid-season, sub wears a different
    # number, etc.) — trusting jersey as primary key causes us to
    # rename "Zoey F." as "Claire Jan" when the latter is on #1 in
    # roster but Zoey was tagged at #1 in the videos.
    roster_shortname_lookup = {}
    for _j, _full in roster.items():
        parts = (_full or "").strip().split()
        if len(parts) >= 2 and parts[-1]:
            short = f"{parts[0]} {parts[-1][0]}.".lower()
            roster_shortname_lookup[short] = _full
    while i < len(lines):
        name = lines[i].strip()
        if not name:
            i += 1; continue
        # Hudl's exported stats row labels come in two shapes:
        #   "#42"             -> jersey only (no name attached)
        #   "#42 Kayah A."    -> jersey + short name
        jersey_m = re.match(r"^#(\d+)(?:\s+(.+?))?$", name)
        is_jersey = bool(jersey_m)
        name_from_stats = (jersey_m.group(2) if jersey_m else "") or ""
        is_unid   = name == "Un-Identified"
        is_team   = name == "My Team"
        is_opp    = name == "Opponent"
        if not (is_jersey or is_unid or is_team or is_opp):
            i += 1; continue
        if i + 1 >= len(lines) or not lines[i+1].startswith("\t"):
            i += 1; continue
        cells = lines[i+1].split("\t")
        if cells and cells[0] == "":
            cells = cells[1:]
        def cell(idx):
            if idx < 0 or idx >= len(cells):
                return ""
            return cells[idx]

        if is_team or is_opp:
            i += 2; continue

        jersey = jersey_m.group(1) if is_jersey else ""
        # Name preference (revised for identity correctness):
        #   1. If the stats row has an inline name ("#2 Claire J."), it
        #      IS the tagger's label for the person these stats belong to.
        #      Upgrade the short form to a full name via name-match lookup
        #      (not jersey lookup). Keep the short form as-is if no match.
        #   2. Otherwise (bare "#42" rows), fall back to jersey->roster.
        #   3. Un-Identified rows are labeled "Un-ID".
        display = None
        if name_from_stats:
            display = (
                roster_shortname_lookup.get(name_from_stats.lower())
                or name_from_stats
            )
        elif jersey:
            display = roster.get(jersey) or f"#{jersey}"
        else:
            display = "Un-ID"

        # Tail-stable cells (read from END of row): SETS PLAYED is always
        # the last cell and PTS +/- is second-to-last. Reading from the end
        # is robust against Hudl dropping BLOCK columns (BS/BA/BE/B/S) when
        # a team hasn't verified blocks — which otherwise causes SETS=0 and
        # hides the player from the React-rendered leaderboard.
        def cell_from_end(offset):
            # offset 1 = last cell, 2 = second-to-last, etc.
            if offset < 1 or offset > len(cells):
                return ""
            return cells[-offset]

        # Detect whether BLOCK columns are present: full row has 48 cells,
        # a row with blocks dropped has 44. When dropped, DIG cells sit
        # right before PTS +/-.
        has_block_cells = len(cells) >= 44 + 4  # heuristic threshold

        K = _num(cell(atk_k))
        E_ = _num(cell(find("E")))
        TA = _num(cell(atk_ta))
        SA = _num(cell(srv_sa))
        SE_ = _num(cell(find("SE")))
        Serve_TA = _num(cell(srv_ta))
        Serve_PCT = _num(cell(srv_pct))
        # Count from end: SETS(-1) PTS(-2) [BLOCK×4 if present] DE(-3 or -7) DS(-4 or -8).
        DIG_ = _num(cell_from_end(8 if has_block_cells else 4))
        # SETS PLAYED is always the last cell.
        SETS = _num(cell_from_end(1))
        ATK_PCT = _num(cell(atk_pct))
        KILL_PCT = _num(cell(kill_pct))
        K_S = _num(cell(ks))
        PASS_PCT = _num(cell(pass_pct))
        SR_TA = _num(cell(sr_ta))

        players.append({
            "name":      display,
            "jersey":    jersey,
            "team":      team,
            "gender":    gender,
            "age":       age,
            "K":         K,
            "E":         E_,
            "TA":        TA,
            "ATK%":      ATK_PCT,
            "KILL%":     KILL_PCT,
            "K/S":       K_S,
            "SA":        SA,
            "SE":        SE_,
            "Serve_TA":  Serve_TA,
            "Serve_PCT": Serve_PCT,
            "DIG":       DIG_,
            "SETS":      SETS,
            "K/Set":     (K / SETS) if SETS > 0 else 0.0,
            "D/Set":     (DIG_ / SETS) if SETS > 0 else 0.0,
            "SA/Set":    (SA / SETS) if SETS > 0 else 0.0,
            "PASS%":     PASS_PCT,
            "SR_TA":     SR_TA,
        })
        i += 2

    # Hudl's export sometimes includes the same player twice within a
    # single team (e.g. Northeast Girls 17.1 has two #42 rows — one with
    # 93 sets of real season stats and another with a 2-set sliver).
    # Dedupe by (jersey, name): keep the row with the highest SETS. This
    # also collapses accidental duplicate 'Un-Identified' rows.
    if players:
        by_key = {}
        for p in players:
            key = (p.get("jersey") or "", p.get("name") or "")
            existing = by_key.get(key)
            if existing is None or (p.get("SETS") or 0) > (existing.get("SETS") or 0):
                by_key[key] = p
        players = list(by_key.values())

    return {"team": team, "gender": gender, "age": age, "players": players}


def collect_teams(export_dir):
    """Parse every *_stats.txt in export_dir. Returns a list of team dicts."""
    files = sorted(glob.glob(os.path.join(export_dir, "*_stats.txt")))
    if not files:
        return None, f"No *_stats.txt files found in {export_dir}"
    all_teams = []
    print(f"  Found {len(files)} stats files")
    for fp in files:
        parsed = parse_file(fp, export_dir)
        if parsed is None:
            print(f"    skipped (malformed): {os.path.basename(fp)}")
            continue
        print(f"    {parsed['team']} ({parsed['gender']} {parsed['age']}U): {len(parsed['players'])} players")
        all_teams.append(parsed)
    return all_teams, None


# ----- Injection: BUVC (simple placeholder) ------------------------------

def inject_placeholder(template_html, payload_json, placeholder):
    if placeholder not in template_html:
        raise ValueError(f"Placeholder {placeholder!r} not found in template")
    return template_html.replace(placeholder, payload_json)


# ----- Injection: NEVBC (gzipped blob inside bundler manifest) -----------

MANIFEST_RE = re.compile(
    r'(<script type="__bundler/manifest">\s*)(\{.*?\})(\s*</script>)',
    re.DOTALL,
)

def inject_club_blob(template_html, payload_json, data_var):  # noqa: same as legacy nevbc
    """Find the gzipped blob that defines window.__NEVBC_DATA__, replace the
    `allTeams = [...]` array inside it with fresh data, re-gzip, put it back.
    """
    m = MANIFEST_RE.search(template_html)
    if not m:
        raise ValueError("bundler manifest script not found in NEVBC template")
    manifest = json.loads(m.group(2))

    # The data-bootstrapping blob ASSIGNS to window.__NEVBC_DATA__; other
    # blobs (like shared.jsx) only reference it. Skip blobs without the
    # assignment pattern.
    assign_re = re.compile(rf"window\.{re.escape(data_var)}\s*=\s*\(function")
    patched_uuid = None
    for uuid, entry in manifest.items():
        if not entry.get("compressed"):
            continue
        try:
            raw = gzip.decompress(base64.b64decode(entry["data"])).decode("utf-8")
        except Exception:
            continue
        if not assign_re.search(raw):
            continue

        # Use a lambda replacement so payload_json isn't interpreted as a
        # regex substitution string (player names like "Un\u00ed" or "\b"
        # would otherwise blow up with "bad escape").
        replacement_1 = f"const allTeams = {payload_json};\n  return allTeams;"
        new_raw, n = re.subn(
            r"const allTeams = \[[\s\S]*?\];\s*return allTeams;",
            lambda _m: replacement_1,
            raw,
            count=1,
        )
        if n == 0:
            replacement_2 = (
                f"window.{data_var} = (function(){{\n"
                f"  const allTeams = {payload_json};\n"
                f"  return allTeams;\n"
                f"}})();"
            )
            new_raw, n = re.subn(
                rf"window\.{re.escape(data_var)}\s*=\s*\(function\(\)\s*\{{[\s\S]*?\}}\)\(\);",
                lambda _m: replacement_2,
                raw,
                count=1,
            )
        if n == 0:
            raise ValueError(f"could not locate allTeams array in assignment blob {uuid[:8]}")

        gz = gzip.compress(new_raw.encode("utf-8"), compresslevel=9, mtime=0)
        entry["data"] = base64.b64encode(gz).decode("ascii")
        patched_uuid = uuid
        break

    if patched_uuid is None:
        raise ValueError(f"no blob assigning window.{data_var} found in template")

    new_manifest_json = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False)
    return template_html[:m.start(2)] + new_manifest_json + template_html[m.end(2):]


# ----- Per-club build ----------------------------------------------------

# --- Snapshot integration ----------------------------------------------
# Stats this tournament = current totals minus the latest snapshot.
# Rank movement = current rank vs snapshot rank.

_BUILDER_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOTS_DIR = os.path.join(os.path.dirname(_BUILDER_DIR), "snapshots")

# Stats we rank players on (higher = better for all of these).
RANKED_STATS = [
    "K", "SA", "DIG", "SETS", "TA", "Serve_TA", "SR_TA",
    "K_Set", "D_Set", "SA_Set",   # per-set rates (note: builder writes "K/Set" but React remaps)
    "ATK_PCT", "KILL_PCT", "Serve_PCT",
]


def find_latest_snapshot(club_key: str):
    """Return path to lexicographically-latest snapshot for this club, or None."""
    pattern = os.path.join(SNAPSHOTS_DIR, f"{club_key}_*.json")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _stat_value(p, stat):
    """Read a stat off a player dict, handling builder/runtime key variants."""
    if stat in p:
        return p[stat] or 0
    # Fall back to snapshot-style keys (snapshots store "K/Set" not "K_Set" etc.)
    aliases = {"K_Set": "K/Set", "D_Set": "D/Set", "SA_Set": "SA/Set",
               "ATK_PCT": "ATK%", "KILL_PCT": "KILL%"}
    return p.get(aliases.get(stat, stat), 0) or 0


def _compute_ranks(players, stats):
    """For each stat, sort players DESC and assign ranks. Returns dict
    keyed by 'team::name' -> {stat: rank}."""
    ranks = {}
    for stat in stats:
        sortable = sorted(players, key=lambda p: _stat_value(p, stat), reverse=True)
        for rank, p in enumerate(sortable, 1):
            key = f"{p.get('team','')}::{p.get('name','')}"
            ranks.setdefault(key, {})[stat] = rank
    return ranks


def enrich_with_snapshot(all_teams, snapshot_path):
    """Add p.tournament, p.prevRank, p.currentRank to each player.
    Idempotent: if no snapshot, leaves players unchanged."""
    if not snapshot_path:
        print("  (no snapshot found — skipping tournament enrichment)")
        return all_teams

    snap = json.load(open(snapshot_path))
    snap_players = [p for t in snap["teams"] for p in t["players"]]
    snap_by_key = {(p.get("team"), p.get("name")): p for p in snap_players}

    prev_ranks = _compute_ranks(snap_players, RANKED_STATS)
    cur_players = [p for t in all_teams for p in t["players"]]
    cur_ranks = _compute_ranks(cur_players, RANKED_STATS)

    enriched_players_count = 0
    new_player_count = 0
    for t in all_teams:
        for p in t["players"]:
            key = (p.get("team"), p.get("name"))
            ranks_key = f"{p.get('team','')}::{p.get('name','')}"
            sp = snap_by_key.get(key)

            if sp is None:
                # Player wasn't in the snapshot — they're new since the
                # last tournament. All of their current stats are
                # "this tournament" stats.
                p["tournament"] = {
                    k: p.get(k, 0) for k in (
                        "K", "E", "TA", "SA", "SE", "DIG", "SETS",
                        "Serve_TA", "SR_TA", "K/Set", "D/Set", "SA/Set",
                        "ATK%", "KILL%", "Serve_PCT", "PASS%",
                    )
                }
                new_player_count += 1
            else:
                tour = {}
                # Volume stats: simple subtraction
                for vol in ("K", "E", "TA", "SA", "SE", "DIG", "SETS",
                            "Serve_TA", "SR_TA"):
                    tour[vol] = (p.get(vol, 0) or 0) - (sp.get(vol, 0) or 0)
                # Rates: recompute from new volume (rate diffs are meaningless)
                sets = tour["SETS"]
                ta   = tour["TA"]
                sta  = tour["Serve_TA"]
                tour["K/Set"]  = tour["K"]   / sets if sets > 0 else 0.0
                tour["D/Set"]  = tour["DIG"] / sets if sets > 0 else 0.0
                tour["SA/Set"] = tour["SA"]  / sets if sets > 0 else 0.0
                tour["ATK%"]   = (tour["K"] - tour["E"]) / ta if ta > 0 else 0.0
                tour["KILL%"]  = tour["K"] / ta if ta > 0 else 0.0
                tour["Serve_PCT"] = ((sta - tour["SE"]) / sta) if sta > 0 else 0.0
                # Pass rating is a per-pass average — can't subtract. Carry current.
                tour["PASS%"] = p.get("PASS%", 0)
                p["tournament"] = tour
                enriched_players_count += 1

            p["prevRank"]    = prev_ranks.get(ranks_key, {})
            p["currentRank"] = cur_ranks.get(ranks_key, {})

    print(f"  enriched: {enriched_players_count} returning + {new_player_count} new")
    print(f"  baseline: {os.path.basename(snapshot_path)}")
    return all_teams


def build_club(club_key):
    cfg = CLUBS[club_key]
    print(f"\n=== {cfg['label']} ===")
    print(f"  data:     {cfg['export_dir']}")
    print(f"  template: {cfg['template']}")
    print(f"  output:   {cfg['output']}")

    if not os.path.exists(cfg["export_dir"]):
        print(f"  ERROR: export folder does not exist: {cfg['export_dir']}")
        print(f"  Run hudl_stats_exporter.py first.")
        return False

    if not os.path.exists(cfg["template"]):
        print(f"  ERROR: template not found: {cfg['template']}")
        return False

    all_teams, err = collect_teams(cfg["export_dir"])
    if err:
        print(f"  ERROR: {err}")
        return False
    if not all_teams:
        print("  ERROR: no usable stats files found")
        return False

    # Tournament + rank-movement enrichment
    all_teams = enrich_with_snapshot(all_teams, find_latest_snapshot(club_key))

    payload = json.dumps(all_teams, separators=(",", ":")).replace("</", "<\\/")

    with open(cfg["template"], "r", encoding="utf-8") as f:
        tpl = f.read()

    try:
        if cfg["inject"] == "placeholder":
            out = inject_placeholder(tpl, payload, cfg["placeholder"])
        elif cfg["inject"] == "club_blob":
            out = inject_club_blob(tpl, payload, cfg["data_var"])
        else:
            raise ValueError(f"unknown inject mode: {cfg['inject']}")
    except Exception as e:
        print(f"  ERROR during data injection: {e}")
        return False

    with open(cfg["output"], "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  Wrote {cfg['output']}")

    # Optional mirror (e.g., copy to index.html so GitHub Pages root updates)
    mirror = cfg.get("mirror_to")
    if mirror:
        with open(mirror, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"  Mirrored to {mirror}")

    total_players = sum(len(t["players"]) for t in all_teams)
    print(f"    Teams:   {len(all_teams)}")
    print(f"    Players: {total_players}")
    return True


# prompt_club_choice is imported from clubs.py (see top of file).


def main():
    print("=" * 60)
    print("  Leaderboard Builder  (BUVC + NEVBC)")
    print("=" * 60)
    print()
    clubs = prompt_club_choice()
    print()

    results = []
    for club_key in clubs:
        ok = build_club(club_key)
        results.append((club_key, ok))

    print()
    print("=" * 60)
    for club_key, ok in results:
        label = CLUBS[club_key]["label"]
        print(f"  {label}: {'OK' if ok else 'FAILED'}")
    print("=" * 60)
    if not all(ok for _, ok in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
