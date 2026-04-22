"""One-shot template patcher: reorder CATEGORIES to put kills/set,
aces/set, digs/set as the highlights (removing total-K/Aces/DIG headline
categories), add pass% as the last, and extend the data remapper with the
new SA_Set and PASS_PCT fields.

Affects both:
  BUVC_Leaderboard_broadcast_template.html
  ../NEVBC_Leaderboard_template.html

Safe to re-run: idempotent — the patch only applies if CATEGORIES still
matches the old form.
"""
import re, json, gzip, base64, pathlib

NEW_CATEGORIES = r"""const CATEGORIES = [
  { id: 'kset',     label: 'Kills/Set',  short: 'K/S',  stat: 'K_Set',     fmt: v => v.toFixed(2),  subStat: 'K',        subLabel: 'TOTAL K',  subFmt: v => Math.round(v), qual: 'atk' },
  { id: 'aceset',   label: 'Aces/Set',   short: 'A/S',  stat: 'SA_Set',    fmt: v => v.toFixed(2),  subStat: 'SA',       subLabel: 'TOTAL SA', subFmt: v => Math.round(v), qual: 'srv' },
  { id: 'dset',     label: 'Digs/Set',   short: 'D/S',  stat: 'D_Set',     fmt: v => v.toFixed(2),  subStat: 'DIG',      subLabel: 'TOTAL DIG', subFmt: v => Math.round(v), qual: 'sets' },
  { id: 'attack',   label: 'Attack %',   short: 'ATK%', stat: 'ATK_PCT',   fmt: v => (v*100).toFixed(1)+'%', subStat: 'TA', subLabel: 'ATTEMPTS', subFmt: v => Math.round(v), qual: 'atk' },
  { id: 'serve',    label: 'Serve %',    short: 'SRV%', stat: 'Serve_PCT', fmt: v => (v*100).toFixed(1)+'%', subStat: 'Serve_TA', subLabel: 'ATTEMPTS', subFmt: v => Math.round(v), qual: 'srv' },
  { id: 'passpct',  label: 'Pass Rating',short: 'PASS', stat: 'PASS_PCT',  fmt: v => v.toFixed(2),           subStat: 'SR_TA',    subLabel: 'RECEPTIONS', subFmt: v => Math.round(v), qual: 'sr' },
  { id: 'all',      label: 'All Stats',  short: 'ALL',  stat: 'K',         fmt: v => Math.round(v), subStat: 'SETS',     subLabel: 'SETS',     subFmt: v => Math.round(v), isAll: true },
];"""

# Data remapper: we want to add SA_Set + PASS_PCT + SR_TA.
REMAP_ANCHOR = "K_Set: +p['K/Set'] || 0,"
REMAP_INSERTION = (
    "K_Set: +p['K/Set'] || 0,\n"
    "      SA_Set: +p['SA/Set'] || 0,\n"
    "      PASS_PCT: +p['PASS%'] || 0,\n"
    "      SR_TA: +p['SR_TA'] || 0,"
)

# Extend qualifyPlayer to understand 'sr' and 'sets' qual types AND gate
# every qualifying category on a minimum-sets-played floor. This prevents
# the "1 set, 1 perfect pass, #1 on leaderboard" problem.
QUAL_ANCHOR = """  if (cat.qual === 'atk') return (p.TA || 0) >= M.atk * sets;
  if (cat.qual === 'srv') return (p.Serve_TA || 0) >= M.srv * sets;
  return true;
}"""
QUAL_INSERTION = """  // Rate-based categories also require a minimum sets-played floor.
  const minSets = M.minSets ?? 10;
  if (sets < minSets) return false;
  if (cat.qual === 'atk') return (p.TA || 0) >= M.atk * sets;
  if (cat.qual === 'srv') return (p.Serve_TA || 0) >= M.srv * sets;
  if (cat.qual === 'sr')  return (p.SR_TA || 0) >= (M.sr ?? 1.0) * sets;
  if (cat.qual === 'sets') return true; // gated by minSets above
  return true;
}"""

# Extend minsForCat to read sr/minSets thresholds from Tweaks (or defaults).
MIN_ANCHOR = "srv: T.minSrvPerSet ?? DEFAULT_MIN.srv,"
MIN_INSERTION = (
    "srv: T.minSrvPerSet ?? DEFAULT_MIN.srv,\n"
    "    sr: T.minSrPerSet ?? 1.0,\n"
    "    minSets: T.minSetsPlayed ?? 10,"
)


def patch_source(src: str) -> tuple[str, list[str]]:
    notes = []
    # 1) Replace CATEGORIES unconditionally (anytime the patcher runs,
    # apply the latest canonical ordering/formatting).
    m = re.search(r"const CATEGORIES = \[[\s\S]*?\];", src)
    if m:
        if m.group(0) == NEW_CATEGORIES:
            notes.append("categories already current")
        else:
            src = src[: m.start()] + NEW_CATEGORIES + src[m.end():]
            notes.append("categories updated")
    else:
        notes.append("categories block not found")

    # 1b) React components initialize state with the removed 'kills' id.
    # Retarget those useStates to the new first category id 'kset' so the
    # CATEGORIES.find(...) lookup doesn't return undefined.
    new_src = re.sub(r"useState\(['\"]kills['\"]\)", "useState('kset')", src)
    if new_src != src:
        src = new_src
        notes.append("useState('kills') -> useState('kset')")

    # 1c) Default sort column was often 'K' (total kills). Retarget to
    # 'K_Set' so the initial sort matches the new primary category.
    new_src = re.sub(r"useState\(['\"]K['\"]\)", "useState('K_Set')", src)
    if new_src != src:
        src = new_src
        notes.append("useState('K') -> useState('K_Set')")

    # 2) Add SA_Set + PASS_PCT + SR_TA to data remapper
    if "SR_TA: +p['SR_TA']" in src:
        notes.append("remapper already current")
    elif REMAP_ANCHOR in src:
        # Strip old partial insertion if present (without SR_TA)
        if "SA_Set: +p['SA/Set']" in src:
            src = re.sub(
                r"K_Set: \+p\['K/Set'\] \|\| 0,\n\s+SA_Set:[\s\S]*?PASS_PCT: \+p\['PASS%'\] \|\| 0,",
                REMAP_ANCHOR,
                src,
            )
        src = src.replace(REMAP_ANCHOR, REMAP_INSERTION, 1)
        notes.append("remapper extended with SR_TA")

    # 3) Extend qualifyPlayer to recognize 'sr' and 'sets', and add a
    # min-sets floor for all rate-based categories.
    if "const minSets = M.minSets" in src:
        notes.append("qualifyPlayer already has min-sets floor")
    elif QUAL_ANCHOR in src:
        src = src.replace(QUAL_ANCHOR, QUAL_INSERTION, 1)
        notes.append("qualifyPlayer: added sr/sets + min-sets floor")
    elif "cat.qual === 'sr'" in src:
        # Previous iteration added sr/sets but no minSets floor — retrofit.
        old_qp = (
            "if (cat.qual === 'srv') return (p.Serve_TA || 0) >= M.srv * sets;\n"
            "  if (cat.qual === 'sr')  return (p.SR_TA || 0) >= (M.sr ?? 1.0) * sets;\n"
            "  if (cat.qual === 'sets') return sets >= (M.minSets ?? 10);\n"
            "  return true;\n"
            "}"
        )
        if old_qp in src:
            src = src.replace(old_qp, QUAL_INSERTION.strip().replace(
                "  if (cat.qual === 'atk') return (p.TA || 0) >= M.atk * sets;",
                "if (cat.qual === 'atk') return (p.TA || 0) >= M.atk * sets;",
            ), 1)
            notes.append("qualifyPlayer: retrofit min-sets floor")

    # 4) Extend minsForCat defaults
    if "sr: T.minSrPerSet" in src:
        pass  # already patched
    elif MIN_ANCHOR in src:
        src = src.replace(MIN_ANCHOR, MIN_INSERTION, 1)
        notes.append("minsForCat extended with sr/minSets")

    return src, notes


MANIFEST_RE = re.compile(
    r'(<script type="__bundler/manifest">\s*)(\{.*?\})(\s*</script>)',
    re.DOTALL,
)


def patch_file(path: pathlib.Path) -> None:
    html = path.read_text()
    m = MANIFEST_RE.search(html)
    if not m:
        print(f"  {path.name}: no manifest")
        return
    manifest = json.loads(m.group(2))
    touched = False
    for uuid, entry in manifest.items():
        if not entry.get("compressed"):
            continue
        try:
            raw = gzip.decompress(base64.b64decode(entry["data"])).decode("utf-8")
        except Exception:
            continue
        # Scan every JSX-like blob: CATEGORIES + remap live in shared.jsx,
        # but useState('kills') lives in editorial/broadcast/cards blobs.
        if (
            "const CATEGORIES" not in raw
            and "K_Set: +p[" not in raw
            and "useState(" not in raw
        ):
            continue
        new_raw, notes = patch_source(raw)
        if new_raw == raw:
            continue
        entry["data"] = base64.b64encode(
            gzip.compress(new_raw.encode("utf-8"), compresslevel=9, mtime=0)
        ).decode("ascii")
        touched = True
        print(f"  {path.name} [{uuid[:8]}]: {', '.join(notes)}")
    if touched:
        new_json = json.dumps(manifest, separators=(',', ':'), ensure_ascii=False)
        path.write_text(html[:m.start(2)] + new_json + html[m.end(2):])
        print(f"  -> wrote {path.name}")


def main():
    here = pathlib.Path(__file__).parent
    parent = here.parent
    for p in (here / "BUVC_Leaderboard_broadcast_template.html",
              parent / "NEVBC_Leaderboard_template.html"):
        if not p.exists():
            print(f"SKIP: {p} not found")
            continue
        print(f"\n{p}:")
        patch_file(p)


if __name__ == "__main__":
    main()
