# session_layout.py

import os
import re
import hashlib
import random

from datetime import datetime
from typing import Optional, List, Dict, Tuple

import xml.etree.ElementTree as ET

from epu.report_utils import open_image_or_none
from epu.report_style import FONT_SIZES
from epu.report_scale_bars import add_scale_bar_by_xml

# Imports from annotators
try:
    from epu.annotate_atlas import map_grids_to_atlas, square_type_and_mtime
except Exception:
    map_grids_to_atlas = None
    square_type_and_mtime = None

try:
    import epu.annotate_gridsquare as ag
    annotate_gridsquare_image_or_pair = getattr(ag, "annotate_gridsquare_image_or_pair", None)
    annotate_single_gridsquare_image = getattr(ag, "annotate_single_gridsquare_image", None)
    find_unique_foilhole_xmls_earliest_latest = getattr(ag, "find_unique_foilhole_xmls_earliest_latest", None)
    get_selected_holes_for_gridsquare = getattr(ag, "get_selected_holes_for_gridsquare", None)
    add_plasmon_caption = getattr(ag, "add_plasmon_caption", None)
except Exception:
    annotate_gridsquare_image_or_pair = None
    annotate_single_gridsquare_image = None
    find_unique_foilhole_xmls_earliest_latest = None
    get_selected_holes_for_gridsquare = None
    add_plasmon_caption = None

# ---------- Patterns and helpers (from generate_report.py, without reportlab) ----------

GRID_IMG_RE = re.compile(r"^GridSquare_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
GRID_SUPPORT_IMG_RE = re.compile(r"^GridSquare_Support_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
FOILHOLE_RE = re.compile(r"^FoilHole_([A-Za-z0-9]+)_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
MICROGRAPH_RE = re.compile(
    r"^FoilHole_([A-Za-z0-9]+)_Data_[^_]+_[^_]+_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE
)
GS_ID_RE = re.compile(r"grid\s*square[_\s-]*([0-9]+)", re.IGNORECASE)

def _dt_from_foilhole_filename(name: str) -> Optional[datetime]:
    m = FOILHOLE_RE.match(name)
    if not m:
        return None
    date_str, time_str = m.group(2), m.group(3)
    dt = parse_datetime_tokens(date_str, time_str)
    return dt if isinstance(dt, datetime) else None

def _dt_from_micrograph_filename(name: str) -> Optional[datetime]:
    m = MICROGRAPH_RE.match(name)
    if not m:
        return None
    date_str, time_str = m.group(2), m.group(3)
    dt = parse_datetime_tokens(date_str, time_str)
    return dt if isinstance(dt, datetime) else None

def _first_micrograph_dt_in_gridsquare(gs_dir: str) -> Optional[datetime]:
    data_dir = os.path.join(gs_dir, "Data")
    if not os.path.isdir(data_dir):
        return None
    earliest = None
    for name in os.listdir(data_dir):
        dt = _dt_from_micrograph_filename(name)
        if dt is None:
            continue
        if earliest is None or dt < earliest:
            earliest = dt
    return earliest

def parse_datetime_tokens(date_str, time_str):
    try:
        return datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    except Exception:
        return (date_str, time_str)

def gridsquare_images(gs_dir: str):
    """
    Return (latest_support_path, latest_non_support_path) for this GridSquare directory.
    Each may be None if not present.
    """
    support = []
    nonsupport = []
    try:
        for name in os.listdir(gs_dir):
            m = GRID_SUPPORT_IMG_RE.match(name)
            if m:
                date_str, time_str = m.group(1), m.group(2)
                support.append((os.path.join(gs_dir, name), date_str, time_str))
                continue
            m = GRID_IMG_RE.match(name)
            if m:
                date_str, time_str = m.group(1), m.group(2)
                nonsupport.append((os.path.join(gs_dir, name), date_str, time_str))
    except Exception:
        pass

    def pick_latest(lst):
        if not lst:
            return None
        lst.sort(key=lambda tup: parse_datetime_tokens(tup[1], tup[2]), reverse=True)
        return lst[0][0]

    return pick_latest(support), pick_latest(nonsupport)

def latest_foilholes_per_key(gs_dir: str):
    holes_dir = os.path.join(gs_dir, "FoilHoles")
    if not os.path.isdir(holes_dir):
        return []
    groups = {}
    for name in os.listdir(holes_dir):
        m = FOILHOLE_RE.match(name)
        if not m:
            continue
        key, date_str, time_str = m.group(1), m.group(2), m.group(3)
        path = os.path.join(holes_dir, name)
        dt = parse_datetime_tokens(date_str, time_str)
        prev = groups.get(key)
        if prev is None or dt > prev[0]:
            groups[key] = (dt, path, date_str, time_str)
    out = []
    for key, (_, path, date_str, time_str) in groups.items():
        out.append((key, path, date_str, time_str))
    out.sort(key=lambda x: x[0])
    return out

def find_matching_micrographs(gs_dir: str, foilhole_key: str) -> List[str]:
    """
    Return ALL matching micrograph JPG paths for this FoilHole key, newest-first.
    Matches your naming: FoilHole_<key>_Data_<...>_<...>_<YYYYMMDD>_<HHMMSS>.jpg
    """
    data_dir = os.path.join(gs_dir, "Data")
    if not os.path.isdir(data_dir):
        return []

    candidates = []
    for name in os.listdir(data_dir):
        m = MICROGRAPH_RE.match(name)
        if not m:
            continue
        key, date_str, time_str = m.group(1), m.group(2), m.group(3)
        if key != foilhole_key:
            continue
        dt = parse_datetime_tokens(date_str, time_str)
        candidates.append((dt, os.path.join(data_dir, name)))

    # sort newest-first; if dt parsing failed, it returns tuple(date,time) which is still sortable
    candidates.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in candidates]

def choose_micrographs_for_display(keys_order, micro_map, max_total=12, seed_str=""):
    """
    keys_order: foilhole keys in priority order
    micro_map: key -> list of micrograph paths (newest-first)
    Returns: key -> list of chosen micrograph paths (preserve vertical stacking order)
    """
    seed = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)

    chosen = {k: [] for k in keys_order}

    if len(keys_order) >= max_total:
        # >12 foilholes: pick at most one micrograph per foilhole (random)
        for k in keys_order[:max_total]:
            lst = micro_map.get(k, [])
            if lst:
                chosen[k] = [rng.choice(lst)]
        return chosen

    # <12 foilholes: target up to 12 micrographs total, >=1 per foilhole if available
    total = 0

    # First pass: 1 per foilhole (if it has any)
    for k in keys_order:
        lst = micro_map.get(k, [])
        if lst:
            pick = rng.choice(lst)
            chosen[k].append(pick)
            total += 1

    # Fill remaining slots by adding extra micrographs from foilholes in priority order
    while total < max_total:
        added_any = False
        for k in keys_order:
            if total >= max_total:
                break
            lst = micro_map.get(k, [])
            if not lst:
                continue
            remaining = [p for p in lst if p not in chosen[k]]
            if not remaining:
                continue
            chosen[k].append(rng.choice(remaining))
            total += 1
            added_any = True
            if total >= max_total:
                break
        if not added_any:
            break  # no more micrographs available anywhere

    # Stack oldest-first visually
    for k in chosen:
        chosen[k] = sorted(chosen[k], key=lambda p: os.path.getmtime(p), reverse=False)

    return chosen

def find_gridsquares(base_folder: str) -> List[str]:
    gs_root = os.path.join(base_folder, "Images-Disc1")
    if not os.path.isdir(gs_root):
        return []
    gs_dirs = [
        os.path.join(gs_root, d)
        for d in os.listdir(gs_root)
        if d.startswith("GridSquare") and os.path.isdir(os.path.join(gs_root, d))
    ]
    gs_dirs.sort()
    return gs_dirs

def extract_epu_from_gridsquare_name(gs_name: str) -> Optional[str]:
    m = GS_ID_RE.search(gs_name or "")
    return m.group(1) if m else None

def _ln(tag: str) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else tag

def _parse_acquisition_area_shifts_from_dm(session_dir: str) -> List[Tuple[float, float]]:
    """
    Return list of (dx_px, dy_px) template shifts for DataAcquisitionAreas from EpuSession.dm.
    If not found, returns [].
    """
    dm_path = os.path.join(session_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return []

    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return []

    def find_first(elem, local_name):
        for e in elem.iter():
            if _ln(e.tag) == local_name:
                return e
        return None

    def parse_shift(node):
        if node is None:
            return None
        dx = dy = None
        for ch in node.iter():
            ln = _ln(ch.tag).lower()
            if ln == "width":
                try:
                    dx = float(ch.text)
                except Exception:
                    pass
            elif ln == "height":
                try:
                    dy = float(ch.text)
                except Exception:
                    pass
        if dx is None or dy is None:
            return None
        return (dx, dy)

    shifts: List[Tuple[float, float]] = []
    daa_node = find_first(root, "DataAcquisitionAreas")
    if daa_node is None:
        return shifts

    for kv in daa_node.iter():
        if "KeyValuePair" not in _ln(kv.tag):
            continue
        value_elem = None
        for ch in kv:
            if _ln(ch.tag) == "value":
                value_elem = ch
                break
        if value_elem is None:
            continue

        sh = find_first(value_elem, "ShiftInPixels")
        s = parse_shift(sh)
        if s is not None:
            shifts.append(s)

    return shifts

def _find_matching_micrograph_xml_from_jpg(micro_jpg_path: str) -> Optional[str]:
    """
    Best-effort match micrograph XML in same folder.
    Tries exact .xml name first, then falls back to scanning by FoilHole_<key>_..._<YYYYMMDD>_<HHMMSS>.xml.
    """
    base = os.path.splitext(micro_jpg_path)[0]
    direct = base + ".xml"
    if os.path.isfile(direct):
        return direct

    name = os.path.basename(micro_jpg_path)
    m = MICROGRAPH_RE.match(name)
    if not m:
        return None
    key, date_str, time_str = m.group(1), m.group(2), m.group(3)
    ts = f"{date_str}_{time_str}"

    data_dir = os.path.dirname(micro_jpg_path)
    try:
        for n in os.listdir(data_dir):
            if not n.lower().endswith(".xml"):
                continue
            if not n.lower().startswith(f"foilhole_{key.lower()}_"):
                continue
            if ts in n:
                p = os.path.join(data_dir, n)
                if os.path.isfile(p):
                    return p
    except Exception:
        return None

    return None

def _parse_shift_in_pixels_from_micrograph_xml(xml_path: str) -> Optional[Tuple[float, float]]:
    """
    Best-effort extraction of a (dx,dy) shift (in pixels) from a micrograph XML.
    Searches for the first ShiftInPixels node with Width/Height children.
    """
    if not xml_path or not os.path.isfile(xml_path):
        return None
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return None

    for elem in root.iter():
        if _ln(elem.tag) != "ShiftInPixels":
            continue
        dx = dy = None
        for ch in elem.iter():
            ln = _ln(ch.tag).lower()
            if ln == "width":
                try:
                    dx = float(ch.text)
                except Exception:
                    pass
            elif ln == "height":
                try:
                    dy = float(ch.text)
                except Exception:
                    pass
        if dx is not None and dy is not None:
            return (dx, dy)

    return None

def _closest_acq_area_index(shift: Tuple[float, float], acq_shifts: List[Tuple[float, float]]) -> Optional[int]:
    """
    Given a micrograph shift and the list of acquisition-area shifts from EpuSession.dm,
    return 1-based index of nearest acquisition area.
    """
    if shift is None or not acq_shifts:
        return None
    sx, sy = shift
    best_i = None
    best_d2 = None
    for i, (ax, ay) in enumerate(acq_shifts, start=1):
        d2 = (sx - ax) ** 2 + (sy - ay) ** 2
        if best_d2 is None or d2 < best_d2:
            best_d2 = d2
            best_i = i
    return best_i

def extract_sample_and_root_from_atlas_path(p: str) -> Optional[Tuple[str, str]]:
    """
    Given a path like ...\\Sample4\\Atlas\\Atlas.dm (or ...\\Sample4\\Atlas),
    return (sample_dir, atlas_root_name).
    """
    if not p:
        return None
    p = p.strip().strip('"').strip("'")
    if re.search(r"(?i)\batlas\.dm$", p):
        atlas_dir = os.path.dirname(p)
    else:
        atlas_dir = p
    last = os.path.basename(atlas_dir)
    if last.lower() != "atlas":
        return None
    sample_dir = os.path.basename(os.path.dirname(atlas_dir))
    if not re.match(r"(?i)^sample\d+$", sample_dir):
        return None
    atlas_root_dir = os.path.dirname(os.path.dirname(atlas_dir))
    atlas_root_name = os.path.basename(atlas_root_dir)
    return sample_dir, atlas_root_name

def atlas_root_is_valid(root: str) -> bool:
    if not os.path.isdir(root):
        return False
    try:
        for name in os.listdir(root):
            if re.match(r"(?i)^sample\d+$", name):
                adir = os.path.join(root, name, "Atlas")
                if os.path.isfile(os.path.join(adir, "Atlas.dm")):
                    return True
    except Exception:
        pass
    return False

def atlas_id_from_epu_dm(session_dir: str) -> Optional[str]:
    dm_path = os.path.join(session_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return None
    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return None
    for elem in root.iter():
        if _ln(elem.tag).lower() == "atlasid":
            txt = (elem.text or "").strip()
            return txt if txt else None
    return None

def atlas_name_from_epu_dm_path(session_dir: str) -> Optional[str]:
    """
    Robustly extract the atlas root folder name from EpuSession.dm by locating the
    <AtlasId> element text (a path like ...\\<atlas_root>\\Sample0\\Atlas\\Atlas.dm).

    Returns the atlas root folder name (the folder right before SampleN), or None.
    """
    dm_path = os.path.join(session_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return None

    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return None

    atlas_path = None
    for elem in root.iter():
        if _ln(elem.tag).lower() == "atlasid":
            txt = (elem.text or "").strip()
            if txt:
                atlas_path = txt
                break

    if not atlas_path:
        return None

    # Split Windows or POSIX paths safely
    parts = [p for p in re.split(r"[\\/]+", atlas_path.strip().strip('"').strip("'")) if p]

    # Find "SampleN" and return the part immediately before it (atlas root folder name)
    for i, part in enumerate(parts):
        if re.match(r"(?i)^sample\d+$", part):
            if i - 1 >= 0:
                name = parts[i - 1]
                return name if name else None
            return None

    return None

def normalize_atlas_arg(a: str) -> str:
    a_abs = os.path.abspath(a)
    if os.path.basename(a_abs).lower() == "atlas":
        if os.path.isfile(os.path.join(a_abs, "Atlas.dm")):
            return os.path.dirname(os.path.dirname(a_abs))
    elif os.path.basename(a_abs).lower() == "atlas.dm" and os.path.isfile(a_abs):
        adir = os.path.dirname(a_abs)
        if os.path.basename(adir).lower() == "atlas":
            return os.path.dirname(os.path.dirname(adir))
    return a_abs

def detect_atlas_root(
    session_dir: str,
    atlas_arg: Optional[str],
    summary_text: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Detect atlas root. Returns (atlas_root_path, atlas_source),
    where atlas_source is one of: 'cli', 'dm_atlasid', 'dm_hint', or None.
    """
    session_dir = os.path.abspath(session_dir)
    parent_dir = os.path.dirname(session_dir)

    if atlas_arg:
        chosen = normalize_atlas_arg(atlas_arg)
        return chosen, "cli"


    atlas_id_text = atlas_id_from_epu_dm(session_dir)
    if atlas_id_text:
        parsed = extract_sample_and_root_from_atlas_path(atlas_id_text)
        if parsed:
            sample_dir, atlas_root_name = parsed
            for base in (session_dir, parent_dir):
                candidate = os.path.join(base, atlas_root_name)
                if atlas_root_is_valid(candidate):
                    return candidate, "dm_atlasid"
        else:
            for base in (session_dir, parent_dir):
                candidate = os.path.join(base, atlas_id_text)
                if atlas_root_is_valid(candidate):
                    return candidate, "dm_atlasid"


    dm_name = atlas_name_from_epu_dm_path(session_dir)
    if dm_name:
        for base in (session_dir, parent_dir):
            candidate = os.path.join(base, dm_name)
            if atlas_root_is_valid(candidate):
                return candidate, "dm_hint"

    return None, None

def find_latest_atlas_jpg(atlas_root: str, session_dir: Optional[str] = None) -> Optional[str]:
    def collect_from_sample(sample: str) -> List[str]:
        adir = os.path.join(atlas_root, sample, "Atlas")
        if os.path.isdir(adir):
            try:
                return [
                    os.path.join(adir, n)
                    for n in os.listdir(adir)
                    if n.lower().startswith("atlas") and n.lower().endswith(".jpg")
                ]
            except Exception:
                return []
        return []

    preferred_sample = None
    if session_dir:
        txt = atlas_id_from_epu_dm(session_dir)
        if txt:
            parsed = extract_sample_and_root_from_atlas_path(txt)
            if parsed:
                preferred_sample, _ = parsed

    candidates: List[str] = []
    tried_samples: List[str] = []

    if preferred_sample:
        candidates = collect_from_sample(preferred_sample)
        tried_samples.append(preferred_sample)

    if not candidates:
        if "Sample0" not in tried_samples:
            candidates = collect_from_sample("Sample0")
            tried_samples.append("Sample0")

    if not candidates:
        try:
            sample_dirs = [n for n in os.listdir(atlas_root) if re.match(r"(?i)^sample\d+$", n)]
            for s in sample_dirs:
                if s in tried_samples:
                    continue
                files = collect_from_sample(s)
                if files:
                    candidates = files
                    break
        except Exception:
            pass

    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]

def find_fallback_atlas_jpgs(session_dir: str) -> List[str]:
    results: List[str] = []
    if not os.path.isdir(session_dir):
        return results

    try:
        for n in os.listdir(session_dir):
            if n.lower().endswith(".jpg") and "atlas" in n.lower():
                results.append(os.path.join(session_dir, n))
    except Exception:
        pass

    if os.path.basename(session_dir).lower() == "epu_out":
        parent = os.path.dirname(session_dir)
        if os.path.isdir(parent):
            try:
                for n in os.listdir(parent):
                    if n.lower().endswith(".jpg") and "atlas" in n.lower():
                        results.append(os.path.join(parent, n))
            except Exception:
                pass

    seen = set()
    deduped = []
    for p in results:
        if p not in seen:
            deduped.append(p)
            seen.add(p)
    return deduped

def compute_gridsquare_index_map(session_dir: str, atlas_root: Optional[str]) -> Dict[str, int]:
    if not atlas_root or map_grids_to_atlas is None or square_type_and_mtime is None:
        return {}
    try:
        df, _, _, _ = map_grids_to_atlas(atlas_root, session_dir, check_node_center=True, fill_rotation='median')
        if df is None or df.empty:
            return {}
        types, colors, mtimes = [], [], []
        for _, row in df.iterrows():
            color, typ, mt = square_type_and_mtime(row['folder'])
            types.append(typ)
            colors.append(color)
            mtimes.append(mt)
        df['square_type'] = types
        df['color'] = colors
        df['square_first_mtime'] = mtimes
        df = df.sort_values(by='square_first_mtime', ascending=True, na_position='last')
        df['grid_square_index'] = range(1, len(df) + 1)
        mapping = {}
        for _, row in df.iterrows():
            mapping[os.path.realpath(row['folder'])] = int(row['grid_square_index'])
        return mapping
    except Exception:
        return {}

def build_session_nodes(session_dir: str, atlas_root: Optional[str]):
    """
    Build a list of GridSquare nodes with children (FoilHoles).

    Filtering:
      1) If there is only 1 grid square: do nothing special.
      2) If multiple grid squares: find the first micrograph timestamp on "Grid Square 1".
         - If GS1 has no micrographs: do nothing special.
      3) For all OTHER grid squares: drop any FoilHole JPG whose timestamp is earlier
         than the first GS1 micrograph timestamp. These dropped FoilHoles:
            - are not shown as thumbnails
            - are not used for "selected" indexing
    """
    def _first_micrograph_dt_in_gridsquare(gs_dir: str) -> Optional[datetime]:
        data_dir = os.path.join(gs_dir, "Data")
        if not os.path.isdir(data_dir):
            return None
        earliest = None
        try:
            for name in os.listdir(data_dir):
                m = MICROGRAPH_RE.match(name)
                if not m:
                    continue
                dt = parse_datetime_tokens(m.group(2), m.group(3))
                if not isinstance(dt, datetime):
                    continue
                if earliest is None or dt < earliest:
                    earliest = dt
        except Exception:
            return None
        return earliest

    gs_index_map = compute_gridsquare_index_map(session_dir, atlas_root)
    gs_dirs = find_gridsquares(session_dir)
    acq_shifts = _parse_acquisition_area_shifts_from_dm(session_dir)
    n_acq_areas = len(acq_shifts) if len(acq_shifts) > 0 else 1
    nodes = []

    # --- Determine GS1 and cutoff timestamp to avoid showing template definition FoilHole images ---
    cutoff_dt: Optional[datetime] = None
    gs1_dir: Optional[str] = None

    if len(gs_dirs) > 1:
        # Prefer the grid square that atlas mapping assigns index 1
        if gs_index_map:
            for d in gs_dirs:
                if gs_index_map.get(os.path.realpath(d)) == 1:
                    gs1_dir = d
                    break

        # Fallback: first directory in sorted order
        if gs1_dir is None and gs_dirs:
            gs1_dir = gs_dirs[0]

        if gs1_dir is not None:
            cutoff_dt = _first_micrograph_dt_in_gridsquare(gs1_dir)
            # If cutoff_dt is None (no micrographs in GS1), proceed as usual.

    for gs_dir in gs_dirs:
        gs_name = os.path.basename(gs_dir)
        support_img_path, nonsupport_img_path = gridsquare_images(gs_dir)
        gs_img_path = support_img_path or nonsupport_img_path
        gs_epu = extract_epu_from_gridsquare_name(gs_name)
        gs_index = gs_index_map.get(os.path.realpath(gs_dir))

        foilholes_latest = latest_foilholes_per_key(gs_dir)

        # Build key -> (path, dt)
        fh_latest_map = {}
        for (key, path, date_str, time_str) in foilholes_latest:
            dt = parse_datetime_tokens(date_str, time_str)
            dt = dt if isinstance(dt, datetime) else None
            fh_latest_map[key] = (path, dt)

        # Convenience: key -> path only (after filtering)
        fh_path_map = {k: v[0] for k, v in fh_latest_map.items()}

        # Build per-key micrograph lists and apply suppression rule
        fh_info = {}  # key -> dict(fh_path, fh_dt, micro_list_all)
        kept_keys = []

        for k, (fh_path, fh_dt) in fh_latest_map.items():
            micro_list_all = find_matching_micrographs(gs_dir, k)

            # NEW suppression rule:
            if cutoff_dt is not None and fh_dt is not None and fh_dt < cutoff_dt and not micro_list_all:
                continue

            fh_info[k] = {"fh_path": fh_path, "fh_dt": fh_dt, "micro_list": micro_list_all}
            kept_keys.append(k)

        # selection order (your existing logic), but restricted to kept_keys
        if get_selected_holes_for_gridsquare is not None:
            try:
                sel_keys_order, _ = get_selected_holes_for_gridsquare(gs_dir, max_show=12)
                keys_selected = [k for k in sel_keys_order if k in kept_keys]
            except Exception:
                keys_selected = kept_keys
        else:
            keys_selected = kept_keys

        # Cap foil holes to 12, but show ALL micrographs for those selected foil holes
        keys_selected = keys_selected[:12]

        # re-index holes sequentially
        idx_map = {k: i + 1 for i, k in enumerate(keys_selected)}

        def _micro_sort_key(p: str):
            # Prefer timestamp embedded in filename; fallback to mtime
            dt = _dt_from_micrograph_filename(os.path.basename(p))
            if isinstance(dt, datetime):
                return dt
            try:
                return datetime.fromtimestamp(os.path.getmtime(p))
            except Exception:
                return datetime.min

        children = []
        for k in keys_selected:
            fh_path = fh_info[k]["fh_path"]

            # ALL micrographs for this foil hole
            micro_paths_all = [p for p in (fh_info[k]["micro_list"] or []) if os.path.isfile(p)]
            micro_paths_all.sort(key=_micro_sort_key)  # oldest -> newest

            # Label by acquisition area if multiple; otherwise keep UI unchanged by sending None
            if n_acq_areas > 1:
                micro_acq_areas = [((i % n_acq_areas) + 1) for i in range(len(micro_paths_all))]
            else:
                micro_acq_areas = [None] * len(micro_paths_all)

            children.append({
                "key": k,
                "index": idx_map[k],
                "foilhole_img_path": fh_path if fh_path and os.path.isfile(fh_path) else None,

                # Existing fields used by the app:
                "micrograph_img_paths": micro_paths_all,
                "micrograph_img_path": micro_paths_all[0] if micro_paths_all else None,

                # NEW fields for UI:
                "n_acq_areas": n_acq_areas,
                "micrograph_acq_areas": micro_acq_areas,  # aligns with micrograph_img_paths
            })

        nodes.append(
            {
                "gs_dir": gs_dir,
                "name": gs_name,
                "epu": gs_epu,
                "index": gs_index,
                "latest_img_path": gs_img_path if gs_img_path and os.path.isfile(gs_img_path) else None,
                "support_img_path": support_img_path if support_img_path and os.path.isfile(support_img_path) else None,
                "nonsupport_img_path": nonsupport_img_path if nonsupport_img_path and os.path.isfile(nonsupport_img_path) else None,
                "children": children,
            }
        )

    try:
        nodes.sort(key=lambda n: (n.get("index") is None, n.get("index", 10**9), n.get("name", "")))
    except Exception:
        pass

    return nodes