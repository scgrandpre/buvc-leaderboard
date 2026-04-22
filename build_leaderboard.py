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
# For each club: export folder (data), template file, output file, and how
# data gets injected ('placeholder' or 'club_blob').

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)

CLUBS = {
    "buvc": {
        "label":        "BUVC (Boston United)",
        "export_dir":   os.path.join(PARENT_DIR, "hudl_exports"),
        # Blob-patching mode: read a frozen copy of the live broadcast
        # bundle and replace the allTeams array inside window.__BUVC_DATA__.
        "template":     os.path.join(SCRIPT_DIR, "BUVC_Leaderboard_broadcast_template.html"),
        "output":       os.path.join(SCRIPT_DIR, "BUVC_Leaderboard_broadcast.html"),
        "inject":       "club_blob",
        "data_var":     "__BUVC_DATA__",
        # After writing output, also overwrite this file so the GitHub Pages
        # root URL reflects the refresh.
        "mirror_to":    os.path.join(SCRIPT_DIR, "index.html"),
    },
    "nevbc": {
        "label":        "NEVBC (Northeast Volleyball)",
        "export_dir":   os.path.join(PARENT_DIR, "hudl_exports_nevbc"),
        "template":     os.path.join(PARENT_DIR, "NEVBC_Leaderboard_template.html"),
        "output":       os.path.join(PARENT_DIR, "NEVBC_Leaderboard_broadcast.html"),
        "inject":       "club_blob",
        "data_var":     "__NEVBC_DATA__",
    },
}


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

    players = []
    roster = load_roster(export_dir, team)
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
        # Name preference: roster full name > stats inline name > "#N" > Un-ID
        display = None
        if jersey:
            display = roster.get(jersey)
        if not display and name_from_stats:
            display = name_from_stats         # e.g. "Kayah A."
        if not display:
            display = f"#{jersey}" if is_jersey else "Un-ID"

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
        })
        i += 2
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


def prompt_club_choice():
    print("Which club do you want to build?")
    print("  [1] BUVC  (Boston United)")
    print("  [2] NEVBC (Northeast Volleyball)")
    print("  [3] Both")
    while True:
        choice = input("Pick 1, 2, or 3: ").strip()
        if choice == "1": return ["buvc"]
        if choice == "2": return ["nevbc"]
        if choice == "3": return ["buvc", "nevbc"]
        print("  Please enter 1, 2, or 3.")


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
