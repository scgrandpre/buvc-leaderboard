#!/usr/bin/env python3
"""
Build BUVC Leaderboard — modern branded design.

Reads Hudl stats exports from hudl_exports/ and emits BUVC_Leaderboard.html,
a single self-contained HTML file with three toggleable design directions
(Editorial, Cards, Broadcast), podium of leaders, sortable/filterable
tables, and a player detail modal.

Usage:
    python build_leaderboard.py
"""

import os
import re
import json
import glob

EXPORT_DIR = "hudl_exports"
OUTPUT_HTML = "BUVC_Leaderboard.html"
TEMPLATE_FILE = "Leaderboard_template.html"


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


def load_roster(script_dir, team_name):
    safe = team_name.replace(" ", "_").replace("-", "_").replace("/", "_")
    path = os.path.join(script_dir, EXPORT_DIR, f"{safe}_roster.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj.get("roster", {}) or {}
    except Exception:
        return {}


def parse_file(filepath, script_dir):
    """Return {team, gender, age, players: [...]} in the simple shape the
    modern leaderboard expects. Robust to varying column orders: we map
    cells by their header label.
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

    # Collect header labels up to the first player row
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

    # Locate section boundaries (we need distinct TA columns for ATK vs SRV)
    def find(label, start=0):
        for idx in range(start, len(header)):
            if header[idx] == label:
                return idx
        return -1

    atk_k = find("K")
    srv_sa = find("SA")
    set_ast = find("AST")
    dig_ds = find("DS")
    gen_sets = find("SETS PLAYED")
    # ATK TA is between K and SA
    atk_ta = find("TA", atk_k + 1) if atk_k >= 0 else -1
    # SRV TA is between SA and AST (if SET exists); else after SA
    srv_ta_end = set_ast if set_ast >= 0 else len(header)
    srv_ta = -1
    if srv_sa >= 0:
        for idx in range(srv_sa + 1, srv_ta_end):
            if header[idx] == "TA":
                srv_ta = idx; break
    # ATK%, KILL%, K/S, Serve PCT
    atk_pct = find("ATK%", atk_k + 1 if atk_k >= 0 else 0)
    kill_pct = find("KILL%")
    ks = find("K/S")
    # Serve PCT comes after SA
    srv_pct = -1
    if srv_sa >= 0:
        for idx in range(srv_sa + 1, srv_ta_end):
            if header[idx] == "PCT":
                srv_pct = idx; break
    se = find("SE")

    # Walk rows
    players = []
    roster = load_roster(script_dir, team)
    i = i  # continue from where header ended
    while i < len(lines):
        name = lines[i].strip()
        if not name:
            i += 1; continue
        is_jersey = bool(re.match(r"^#\d+$", name))
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

        jersey = name.lstrip("#") if is_jersey else ""
        display = roster.get(jersey) if jersey else None
        if not display:
            display = name if is_jersey else "Un-ID"

        K = _num(cell(atk_k))
        E_ = _num(cell(find("E")))
        TA = _num(cell(atk_ta))
        SA = _num(cell(srv_sa))
        SE_ = _num(cell(find("SE")))
        Serve_TA = _num(cell(srv_ta))
        Serve_PCT = _num(cell(srv_pct))
        DIG_ = _num(cell(dig_ds))
        SETS = _num(cell(gen_sets))
        ATK_PCT = _num(cell(atk_pct))
        KILL_PCT = _num(cell(kill_pct))
        K_S = _num(cell(ks))

        players.append({
            "name":      f"#{jersey}" if jersey else "Un-ID",
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
        })
        i += 2
    return {"team": team, "gender": gender, "age": age, "players": players}


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    export_dir = os.path.join(script_dir, EXPORT_DIR)
    files = sorted(glob.glob(os.path.join(export_dir, "*_stats.txt")))
    if not files:
        print(f"No stats files found in {export_dir}")
        return

    print(f"Found {len(files)} stat files")
    all_teams = []
    for fp in files:
        parsed = parse_file(fp, script_dir)
        if parsed is None:
            print(f"  skipped: {os.path.basename(fp)}"); continue
        print(f"  {parsed['team']} ({parsed['gender']} {parsed['age']}U): {len(parsed['players'])} players")
        all_teams.append(parsed)

    tpl_path = os.path.join(script_dir, TEMPLATE_FILE)
    if not os.path.exists(tpl_path):
        print(f"\n!! Template not found: {tpl_path}")
        print("   Place Leaderboard_template.html next to this script.")
        return

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    payload = json.dumps(all_teams, separators=(",", ":")).replace("</", "<\\/")
    out = tpl.replace("__BUVC_DATA_PLACEHOLDER__", payload)

    out_path = os.path.join(script_dir, OUTPUT_HTML)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"\nWrote {out_path}")
    print(f"  Teams:   {len(all_teams)}")
    print(f"  Players: {sum(len(t['players']) for t in all_teams)}")


if __name__ == "__main__":
    main()
