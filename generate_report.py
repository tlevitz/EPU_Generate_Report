#!/usr/bin/env python3
# coding: utf-8
"""
Generate a PDF report for a screening session with a compact, grid-like layout.

Usage:
python3.9 generate_report.py /path/to/session [optional:/path/to/atlas_or_name]

Outputs (written into the session folder):
- Imaging_Summary_<session>.pdf
- atlas_annotated.jpg (if an atlas is found and annotation succeeds)

Dependencies:
- numpy
- pandas
- pillow
- reportlab
"""

import os
import sys
import re
from datetime import datetime
from typing import Optional, List, Dict, Tuple
import xml.etree.ElementTree as ET

from PIL import Image

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Table,
    TableStyle,
    Spacer,
    PageBreak,
    Frame
)
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from report_style import (
    IMAGE_LAYOUT,
    RL_FONT_FAMILY,
    RL_FONT_FAMILY_BOLD,
    RL_FONT_FAMILY_ITALIC,
    FONT_SIZES,
)
from report_utils import (
    open_image_or_none,
    draw_heading,
    draw_page_number,
    draw_node_box,
    draw_frame_box,
    draw_image_fill_width_top_center,
)

# Imports from annotators
try:
    from annotate_atlas import annotate_atlas_pair, map_grids_to_atlas, square_type_and_mtime
except Exception:
    annotate_atlas_pair = None
    map_grids_to_atlas = None
    square_type_and_mtime = None

try:
    import annotate_gridsquare as ag
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

try:
    from annotate_foilhole import annotate_foilhole_template
except Exception:
    annotate_foilhole_template = None

from report_scale_bars import add_scale_bar_by_xml

# ---------- Patterns and helpers ----------
GRID_IMG_RE = re.compile(r"^GridSquare_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
GRID_SUPPORT_IMG_RE = re.compile(r"^GridSquare_Support_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
FOILHOLE_RE = re.compile(r"^FoilHole_([A-Za-z0-9]+)_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
MICROGRAPH_RE = re.compile(
    r"^FoilHole_([A-Za-z0-9]+)_Data_[^_]+_[^_]+_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE
)
GS_ID_RE = re.compile(r"grid\s*square[_\s-]*([0-9]+)", re.IGNORECASE)

def parse_datetime_tokens(date_str, time_str):
    try:
        return datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    except Exception:
        return (date_str, time_str)

def first_micrograph_dt_in_gridsquare(gs_dir: str) -> Optional[datetime]:
    data_dir = os.path.join(gs_dir, "Data")
    if not os.path.isdir(data_dir):
        return None
    earliest = None
    for name in os.listdir(data_dir):
        m = MICROGRAPH_RE.match(name)
        if not m:
            continue
        dt = parse_datetime_tokens(m.group(2), m.group(3))
        if isinstance(dt, datetime) and (earliest is None or dt < earliest):
            earliest = dt
    return earliest

def latest_gridsquare_image(gs_dir: str) -> Optional[str]:
    imgs = []
    try:
        for name in os.listdir(gs_dir):
            m = GRID_SUPPORT_IMG_RE.match(name)
            if m:
                date_str, time_str = m.group(1), m.group(2)
                imgs.append((os.path.join(gs_dir, name), date_str, time_str))
            else:
                m = GRID_IMG_RE.match(name)
                if m:
                    date_str, time_str = m.group(1), m.group(2)
                    imgs.append((os.path.join(gs_dir, name), date_str, time_str))
    except Exception:
        pass
    if not imgs:
        return None
    imgs.sort(key=lambda tup: parse_datetime_tokens(tup[1], tup[2]), reverse=True)
    return imgs[0][0]

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

def find_matching_micrograph(gs_dir: str, foilhole_key: str) -> Optional[str]:
    data_dir = os.path.join(gs_dir, "Data")
    if not os.path.isdir(data_dir):
        return None
    candidates = []
    for name in os.listdir(data_dir):
        m = MICROGRAPH_RE.match(name)
        if not m:
            continue
        key, date_str, time_str = m.group(1), m.group(2), m.group(3)
        if key != foilhole_key:
            continue
        candidates.append((os.path.join(data_dir, name), date_str, time_str))
    if not candidates:
        return None
    candidates.sort(key=lambda tup: parse_datetime_tokens(tup[1], tup[2]), reverse=True)
    return candidates[0][0]

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
    summary_text: str,
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
        df, _ = map_grids_to_atlas(atlas_root, session_dir, check_node_center=True, fill_rotation='median')
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

# ---------- Summary text reader and parser ----------

def read_summary_text(session_dir: str) -> str:
    
    folder_name = os.path.basename(session_dir)
    if folder_name == "EPU_Out":
        parent_folder = os.path.dirname(session_dir)
        folder_name = os.path.basename(parent_folder)
    
    filename1 = f"collection_stats_{folder_name}.txt"
    filename2 = f"screening_stats_{folder_name}.txt"
    p1 = os.path.join(session_dir, filename1)
    p2 = os.path.join(session_dir, filename2)
    path = p1 if os.path.isfile(p1) else (p2 if os.path.isfile(p2) else None)
    
    # Make robust for file naming convention from old script
    if path == None:
        p1 = os.path.join(session_dir, "collectionstats.txt")
        p2 = os.path.join(session_dir, "screeningstats.txt")
        path = p1 if os.path.isfile(p1) else (p2 if os.path.isfile(p2) else None)
            
    if not path:
        msg = f"No screening_stats_{session_dir}.txt, screeningstats.txt, collection_stats_{session_dir}.txt, or collectionstats.txt found in this session folder."
        print(msg)
        return msg
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        return text
    except Exception as e:
        err = f"Error reading {os.path.basename(path)}: {e}"
        print(err)
        return err
    
def extract_beam_size_m_from_summary(summary_text: str) -> Optional[float]:
    """
    Look for a line like:
    'Beam Size (um)                                    1.0'
    and return the value in meters (float) if found.
    """
    for line in summary_text.splitlines():
        if "Beam Size" in line:
            # split on 2+ spaces, like parse_summary_text_to_struct
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 2:
                val_str = parts[1].strip()
                # strip any trailing units, just in case
                val_str = re.split(r"\s+", val_str)[0]
                try:
                    beam_um = float(val_str)
                    return beam_um * 1e-6  # convert µm -> m
                except ValueError:
                    pass
    return None

def parse_summary_text_to_struct(summary_text: str):
    """
    Parse the summary text into:
      - rows: list of (key, value) for the main table
      - notes: list of bullet strings
    Assumes 'Notes:' starts the notes section.
    """
    rows = []
    notes = []
    in_notes = False

    for line in summary_text.splitlines():
                
        stripped = line.rstrip()
        if not stripped:
            continue

        if stripped.startswith("Notes:"):
            in_notes = True
            continue

        if in_notes:
            s = stripped.lstrip()
            if s.startswith("-"):
                note = s[1:].strip()
                if note:
                    notes.append(note)
            else:
                if notes:
                    notes[-1] += " " + s
            continue

        parts = re.split(r"\s{2,}", stripped)
        if len(parts) >= 2:
            key = parts[0].strip()
            value = parts[1].strip()
            rows.append((key, value))
    
    return rows, notes

# ---------- Platypus summary page ----------

def build_summary_data(folder_name: str, summary_text: str):
    """
    Return (rows, notes) for the summary, with '(um)' normalized to '(µm)'.
    """
    rows, notes = parse_summary_text_to_struct(summary_text)
    rows = [
        (k.replace("(um)", "(µm)"), v.replace("(um)", "(µm)"))
        for (k, v) in rows
    ]
    notes = [n.replace("(um)", "(µm)") for n in notes]
    return rows, notes

def chunk_table_rows(table_data, max_rows_per_page):
    """Yield chunks of table_data with at most max_rows_per_page rows."""
    for i in range(0, len(table_data), max_rows_per_page):
        yield table_data[i : i + max_rows_per_page]

# ---------- Child box helpers (for gridsquare children) ----------

def measure_child_box_height(c, foilhole_img: Optional[Image.Image], micro_img: Optional[Image.Image],
                             w: float, title_font_size: int, pad: float = 6.0) -> float:
    title_h = title_font_size + 4
    gap_t = 3
    h = pad + title_h + gap_t
    if foilhole_img is not None:
        fw, fh = foilhole_img.size
        if fw > 0 and fh > 0:
            scale = (w - 2 * pad) / fw
            h += fh * scale + 3
    if micro_img is not None:
        mw, mh = micro_img.size
        if mw > 0 and mh > 0:
            scale = (w - 2 * pad) / mw
            h += mh * scale + 3
    h += pad
    return h

def draw_child_box(c, x: float, y_top: float, w: float,
                   title: str, foilhole_img: Optional[Image.Image], micro_img: Optional[Image.Image],
                   title_font_size: int, pad: float = 6.0) -> float:
    title_h = title_font_size + 4
    gap_t = 3

    total_h = measure_child_box_height(c, foilhole_img, micro_img, w, title_font_size, pad)
    draw_node_box(
        c,
        x,
        y_top,
        w,
        total_h,
        title,
        pad=pad,
        title_align="center",
        font_name=RL_FONT_FAMILY_BOLD,
        font_size=title_font_size,
    )

    content_top = y_top - (pad + title_h + gap_t)

    if foilhole_img is not None:
        dh = draw_image_fill_width_top_center(c, foilhole_img, x, content_top, w, pad=pad)
        content_top -= (dh + 3)

    if micro_img is not None:
        dh = draw_image_fill_width_top_center(c, micro_img, x, content_top, w, pad=pad)
        content_top -= (dh + 3)

    return total_h

def render_fallback_atlas_images(c, fallback_imgs: List[str],
                                 x_left: float, y: float,
                                 width: float, height: float, margin: float) -> float:
    cfg = IMAGE_LAYOUT["atlas_fallback"]
    pad = cfg["frame_padding"]
    max_w = width - 2 * margin
    max_h = cfg["max_height"]
    caption_font = RL_FONT_FAMILY
    caption_size = FONT_SIZES["caption"]
    caption_gap = cfg["caption_gap"]
    extra_gap = cfg["extra_gap"]

    for img_path in fallback_imgs:
        img = open_image_or_none(img_path)
        if img is None:
            continue
        iw, ih = img.size
        scale = min((max_w - 2 * pad) / max(iw, 1), (max_h - 2 * pad) / max(ih, 1))
        dw, dh = iw * scale, ih * scale
        total_h = pad + dh + pad
        caption_h = caption_size + caption_gap
        needed_h = total_h + caption_h

        if y - needed_h < margin:
            c.showPage()
            y = height - margin
            y = draw_heading(c, "Atlas (cont.)", x_left, y, level="section", page_height=height, margin=margin)

        draw_frame_box(c, x_left, y, max_w, total_h)
        img_reader = ImageReader(img)
        x_img = x_left + pad + (max_w - 2 * pad - dw) / 2.0
        y_img = y - pad - dh
        c.drawImage(img_reader, x_img, y_img, width=dw, height=dh, preserveAspectRatio=True, mask='auto')

        c.setFont(caption_font, caption_size)
        caption_y = y - total_h - caption_gap - caption_size
        c.drawCentredString(x_left + max_w / 2.0, caption_y, os.path.basename(img_path))

        y -= (needed_h + extra_gap)

    return y

# ---------- Main report generation ----------

def build_report(session_dir: str, atlas_arg: Optional[str]) -> int:
    session_dir = os.path.abspath(session_dir)
    if not os.path.isdir(session_dir):
        print(f"Error: session directory not found: {session_dir}")
        return 2

    folder_name = os.path.basename(session_dir)
    if folder_name == "EPU_Out":
        parent_folder = os.path.dirname(session_dir)
        folder_name = os.path.basename(parent_folder)

    # 1) Summary text
    summary_text = read_summary_text(session_dir)
    beam_size_m = extract_beam_size_m_from_summary(summary_text)

    # 2) Detect atlas root
    atlas_root, atlas_source = detect_atlas_root(session_dir, atlas_arg, summary_text)

    # 3) Annotated atlas image
    atlas_annotated_path = None
    if atlas_root and annotate_atlas_pair is not None and atlas_source in ("dm_atlasid", "dm_hint", "cli"):
        try:
            atlas_img = annotate_atlas_pair(session_dir, atlas_root)
            atlas_annotated_path = os.path.join(session_dir, f"atlas_annotated_{folder_name}.jpg")
            atlas_img.save(atlas_annotated_path, format="JPEG", quality=90)
        except Exception as e:
            print(f"Warning: failed to generate annotated atlas: {e}")

    # 4) GridSquare index map
    gs_index_map = compute_gridsquare_index_map(session_dir, atlas_root)

    # 5) Build nodes (GridSquares -> FoilHoles)
    gs_dirs = find_gridsquares(session_dir)
    nodes = []
    
    cutoff_dt = None
    gs1_dir = None

    # Only if multiple grid squares
    if len(gs_dirs) > 1:
        # pick the grid square whose atlas-derived index is 1, otherwise fallback to first
        for d in gs_dirs:
            if gs_index_map.get(os.path.realpath(d)) == 1:
                gs1_dir = d
                break
        if gs1_dir is None:
            gs1_dir = gs_dirs[0]

        cutoff_dt = first_micrograph_dt_in_gridsquare(gs1_dir)

    for gs_dir in gs_dirs:
        gs_name = os.path.basename(gs_dir)
        support_img_path, nonsupport_img_path = gridsquare_images(gs_dir)
        gs_img_path = support_img_path or nonsupport_img_path
        gs_epu = extract_epu_from_gridsquare_name(gs_name)
        gs_index = gs_index_map.get(os.path.realpath(gs_dir))

        foilholes_latest = latest_foilholes_per_key(gs_dir)

        # NEW: if cutoff_dt exists and this is NOT GS1, drop early FoilHoles
        if cutoff_dt is not None and gs1_dir is not None and gs_dir != gs1_dir:
            filtered = []
            for (key, path, date_str, time_str) in foilholes_latest:
                dt = parse_datetime_tokens(date_str, time_str)
                if isinstance(dt, datetime) and dt < cutoff_dt:
                    continue
                filtered.append((key, path, date_str, time_str))
            foilholes_latest = filtered

        fh_latest_map = {key: path for (key, path, _d, _t) in foilholes_latest}

        # Pass same cutoff into "selected holes" logic (so indexing matches what you display)
        min_ts = cutoff_dt if (cutoff_dt is not None and gs1_dir is not None and gs_dir != gs1_dir) else None

        if get_selected_holes_for_gridsquare is not None:
            try:
                sel_keys_order, sel_idx_map = get_selected_holes_for_gridsquare(gs_dir, max_show=12, min_ts=min_ts)
                keys_selected = [k for k in sel_keys_order if k in fh_latest_map]
                idx_map = sel_idx_map
            except Exception:
                keys_selected = fh_latest_map
                idx_map = {k: i + 1 for i, k in enumerate(fh_latest_map)}
        else:
            keys_selected = fh_latest_map
            idx_map = {k: i + 1 for i, k in enumerate(fh_latest_map)}

        children = []
        for key in keys_selected:
            fh_path = fh_latest_map.get(key)
            micro = find_matching_micrograph(gs_dir, key)
            child = {
                "key": key,
                "index": idx_map.get(key),
                "foilhole_img_path": fh_path if fh_path and os.path.isfile(fh_path) else None,
                "micrograph_img_path": micro if micro and os.path.isfile(micro) else None,
            }

            child["foilhole_img"] = open_image_or_none(child["foilhole_img_path"])
            if child["foilhole_img"] is not None and child["foilhole_img_path"]:
                child["foilhole_img"] = add_scale_bar_by_xml(
                    child["foilhole_img"],
                    child["foilhole_img_path"],
                    bar_um=1.0,
                    align="left",
                    font_size=FONT_SIZES["defocus"],
                )

            child["micro_img"] = open_image_or_none(child["micrograph_img_path"])
            if child["micro_img"] is not None and child["micrograph_img_path"]:
                child["micro_img"] = add_scale_bar_by_xml(
                    child["micro_img"],
                    child["micrograph_img_path"],
                    bar_nm=50.0,
                    align="left",
                    add_defocus=True,
                    font_size=FONT_SIZES["defocus"],
                )

            children.append(child)

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
        
    # 5) Find cutoff dt to remove template definition images
    cutoff_dt = None
    if len(nodes) > 1:
        # find the folder whose atlas-based index is 1
        gs1 = next((gs for gs in nodes if gs.get("index") == 1), None)
        if gs1:
            cutoff_dt = first_micrograph_dt_in_gridsquare(gs1["gs_dir"])

    try:
        nodes.sort(key=lambda n: (n.get("index") is None, n.get("index", 10**9), n.get("name", "")))
    except Exception:
        pass
   
    # 6) Build PDF manually, including paginated summary
    file_name = f"Imaging_Summary_{folder_name}.pdf"
    pdf_path = os.path.join(session_dir, file_name)

    c = rl_canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    margin = 0.5 * inch
    page_num = 1

    # --- Summary pages (manual pagination) ---
    rows, notes = build_summary_data(folder_name, summary_text)

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=RL_FONT_FAMILY,
        fontSize=FONT_SIZES["body"],
        leading=FONT_SIZES["body"] * 1.2,
    )
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontName=RL_FONT_FAMILY_BOLD,
        fontSize=FONT_SIZES["title"],
        leading=FONT_SIZES["title"] * 1.2,
        spaceAfter=12,
    )
    key_style = ParagraphStyle(
        "Key",
        parent=body_style,
        fontName=RL_FONT_FAMILY_BOLD,
    )
    val_style = body_style

    notes_title_style = ParagraphStyle(
        "NotesTitle",
        parent=body_style,
        fontName=RL_FONT_FAMILY_BOLD,
        fontSize=FONT_SIZES["body"],
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        parent=body_style,
        leftIndent=12,
        bulletIndent=0,
        bulletFontName=RL_FONT_FAMILY,
        bulletFontSize=FONT_SIZES["body"],
    )

    # Build full table_data
    table_data = [
        [Paragraph(key, key_style), Paragraph(value, val_style)]
        for key, value in rows
    ]

    # Build full table_data
    table_data = [
        [Paragraph(key, key_style), Paragraph(value, val_style)]
        for key, value in rows
    ]

    # Conservative fixed limits; adjust as needed
    max_rows_first_page = 50
    max_rows_other_pages = 55

    # Split table_data into chunks
    chunks = []
    remaining = table_data
    if remaining:
        chunks.append(remaining[:max_rows_first_page])
        remaining = remaining[max_rows_first_page:]
    while remaining:
        chunks.append(remaining[:max_rows_other_pages])
        remaining = remaining[max_rows_other_pages:]

    for i, chunk in enumerate(chunks or [[]]):  # ensure at least one page
        story = []

        # Title only on first summary page
        if i == 0:
            story.append(Paragraph(f"Imaging Summary: {folder_name}", title_style))
            story.append(Spacer(1, 0.2 * inch))

        if chunk:
            table = Table(
                chunk,
                colWidths=[3.25 * inch, 4.25 * inch],
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ]
                )
            )
            story.append(table)

        # Only try to put notes on the last summary page
        if i == len(chunks) - 1 and notes:
            # Try to add notes; if they don't fit, we'll put them on a new page
            story_with_notes = list(story)
            story_with_notes.append(Spacer(1, 0.3 * inch))
            story_with_notes.append(Paragraph("Notes", notes_title_style))
            for note in notes:
                story_with_notes.append(Paragraph(note, bullet_style, bulletText="•"))

            frame = Frame(
                margin,
                margin,
                width - 2 * margin,
                height - 2 * margin,
                showBoundary=0,
            )
            remaining_story = list(story_with_notes)
            frame.addFromList(remaining_story, c)
            draw_page_number(c, page_num, width, margin)
            c.showPage()
            page_num += 1

            # If anything is left, put it on a new page
            if remaining_story:
                frame = Frame(
                    margin,
                    margin,
                    width - 2 * margin,
                    height - 2 * margin,
                    showBoundary=0,
                )
                frame.addFromList(remaining_story, c)
                draw_page_number(c, page_num, width, margin)
                c.showPage()
                page_num += 1

            # Done with summary; break out of the loop
            break

        else:
            # No notes on this page
            frame = Frame(
                margin,
                margin,
                width - 2 * margin,
                height - 2 * margin,
                showBoundary=0,
            )
            frame.addFromList(story, c)
            draw_page_number(c, page_num, width, margin)
            c.showPage()
            page_num += 1

    # After summary pages, continue with your existing manual drawing
    x_left = margin
    y = height - margin

    # --- Atlas page(s) ---
    atlas_cfg = IMAGE_LAYOUT["atlas"]
    y = draw_heading(c, "Atlas", x_left, y, level="section", page_height=height, margin=margin)
    if atlas_root:
        atlas_base = find_latest_atlas_jpg(atlas_root, session_dir=session_dir)
        if atlas_base:
            atlas_img_path = (
                atlas_annotated_path
                if (atlas_annotated_path and os.path.isfile(atlas_annotated_path))
                else atlas_base
            )
            atlas_img = open_image_or_none(atlas_img_path)
            if atlas_img is not None:
                pad = atlas_cfg["frame_padding"]
                max_w = width - 2 * margin
                max_h = atlas_cfg["max_height"]
                iw, ih = atlas_img.size
                scale = min((max_w - 2 * pad) / iw, (max_h - 2 * pad) / ih)
                dw, dh = iw * scale, ih * scale
                total_h = pad + dh + pad
                if y - total_h < margin:
                    draw_page_number(c, page_num, width, margin)
                    c.showPage()
                    page_num += 1
                    y = height - margin
                    y = draw_heading(c, "Atlas (cont.)", x_left, y, level="section", page_height=height, margin=margin)
                draw_frame_box(c, x_left, y, max_w, total_h)
                img_reader = ImageReader(atlas_img)
                x_img = x_left + pad + (max_w - 2 * pad - dw) / 2.0
                y_img = y - pad - dh
                c.drawImage(img_reader, x_img, y_img, width=dw, height=dh, preserveAspectRatio=True, mask='auto')
                y -= (total_h + atlas_cfg["after_box_gap"])
            else:
                fallback_imgs = find_fallback_atlas_jpgs(session_dir)
                if fallback_imgs:
                    note = (
                        "Atlas file not found in expected directory structure, "
                        "automatically-detected atlas image(s) shown here. Ignore if irrelevant."
                    )
                    if y - 0.3 * inch < margin:
                        draw_page_number(c, page_num, width, margin)
                        c.showPage()
                        page_num += 1
                        y = height - margin
                        y = draw_heading(c, "Atlas (cont.)", x_left, y, level="section", page_height=height, margin=margin)
                    c.setFont(RL_FONT_FAMILY, FONT_SIZES["note"])
                    c.drawString(x_left, y, note)
                    y -= 0.5 * inch
                    y = render_fallback_atlas_images(c, fallback_imgs, x_left, y, width, height, margin)
                else:
                    c.setFont(RL_FONT_FAMILY, FONT_SIZES["note"])
                    c.drawString(x_left, y, "No matching atlas found in session or parent folder")
                    y -= 0.5 * inch
        else:
            fallback_imgs = find_fallback_atlas_jpgs(session_dir)
            if fallback_imgs:
                note = (
                    "Atlas file not found in expected directory structure, "
                    "automatically-detected atlas image(s) shown here. Ignore if irrelevant."
                )
                if y - 0.3 * inch < margin:
                    draw_page_number(c, page_num, width, margin)
                    c.showPage()
                    page_num += 1
                    y = height - margin
                    y = draw_heading(c, "Atlas (cont.)", x_left, y, level="section", page_height=height, margin=margin)
                c.setFont(RL_FONT_FAMILY, FONT_SIZES["note"])
                c.drawString(x_left, y, note)
                y -= 0.5 * inch
                y = render_fallback_atlas_images(c, fallback_imgs, x_left, y, width, height, margin)
            else:
                c.setFont(RL_FONT_FAMILY, FONT_SIZES["note"])
                c.drawString(x_left, y, "No matching atlas found in session or parent folder")
                y -= 0.5 * inch
    else:
        fallback_imgs = find_fallback_atlas_jpgs(session_dir)
        if fallback_imgs:
            note = (
                "Atlas file not found in expected directory structure, "
                "automatically-detected atlas shown here. Ignore if irrelevant."
            )
            if y - 0.3 * inch < margin:
                draw_page_number(c, page_num, width, margin)
                c.showPage()
                page_num += 1
                y = height - margin
                y = draw_heading(c, "Atlas (cont.)", x_left, y, level="section", page_height=height, margin=margin)
            c.setFont(RL_FONT_FAMILY, FONT_SIZES["note"])
            c.drawString(x_left, y, note)
            y -= 0.2 * inch
            y = render_fallback_atlas_images(c, fallback_imgs, x_left, y, width, height, margin)
        else:
            c.setFont(RL_FONT_FAMILY, FONT_SIZES["note"])
            c.drawString(
                x_left,
                y,
                "No matching atlas folder or potential atlas images found in session folder",
            )
            y -= 0.5 * inch

    # --- Template Definition (FoilHole) ---
    template_img = None
    if annotate_foilhole_template is not None:
        try:
            template_img = annotate_foilhole_template(session_dir, beam_diameter_stats_m=beam_size_m)
        except Exception:
            template_img = None
            
    if template_img is not None:
        tpl_cfg = IMAGE_LAYOUT["template"]
        pad = tpl_cfg["frame_padding"]
        max_w = width - 2 * margin
        heading_height = tpl_cfg["heading_height"]

        iw, ih = template_img.size
        scale = min(
            (max_w - 2 * pad) / max(iw, 1),
            tpl_cfg["max_image_height"] / max(ih, 1),
        )
        dw = iw * scale
        dh = ih * scale

        needed_h = heading_height + dh + tpl_cfg["after_box_gap"]

        if y - needed_h < margin:
            draw_page_number(c, page_num, width, margin)
            c.showPage()
            page_num += 1
            y = height - margin

        y = draw_heading(c, "Template Definition", x_left, y, level="section", page_height=height, margin=margin)

        total_h = pad + dh + pad
        box_top_y = y
        draw_frame_box(c, x_left, box_top_y, max_w, total_h)

        img_reader = ImageReader(template_img)
        x_img = x_left + pad + (max_w - 2 * pad - dw) / 2.0
        y_img = box_top_y - pad - dh
        c.drawImage(
            img_reader,
            x_img,
            y_img,
            width=dw,
            height=dh,
            preserveAspectRatio=True,
            mask="auto",
        )

        y = box_top_y - total_h - tpl_cfg["after_box_gap"]

    # --- GridSquares ---
    gs_cfg = IMAGE_LAYOUT["gridsquare"]
    child_cfg = IMAGE_LAYOUT["child"]

    parent_max_h = gs_cfg["max_image_height"]
    col_gap = child_cfg["col_gap"]
    row_gap = child_cfg["row_gap"]
    columns = child_cfg["columns"]
    avail_w = width - 2 * margin
    child_w = (avail_w - (columns - 1) * col_gap) / columns
    child_title_font_size = FONT_SIZES["hole_title"]
    pad = gs_cfg["frame_padding"]

    gs_title_font_name = RL_FONT_FAMILY_BOLD
    gs_title_font_size = FONT_SIZES["gs_title"]
    gs_caption_font_name = RL_FONT_FAMILY_ITALIC
    gs_caption_font_size = FONT_SIZES["note"]

    def new_page():
        nonlocal page_num, y
        draw_page_number(c, page_num, width, margin)
        c.showPage()
        page_num += 1
        y = height - margin

    for gs in nodes:
        # New page for each GridSquare
        new_page()

        if gs.get("index") is not None and gs.get("epu"):
            label = f"Grid Square {gs['index']} (EPU {gs['epu']})"
        elif gs.get("index") is not None:
            label = f"Grid Square {gs['index']}"
        else:
            label = gs['name']

        # --- Determine main GS image and its underlying file path ---
        gs_img_pil = None
        main_base_path = None  # path of the image used as base for the main GS image

        # 1) Try annotated pair
        if annotate_gridsquare_image_or_pair is not None:
            try:
                min_ts = None
                if cutoff_dt is not None and gs.get("index") != 1:
                    min_ts = cutoff_dt

                gs_img_pil = annotate_gridsquare_image_or_pair(gs["gs_dir"], min_ts=min_ts)

                # assume pair uses support image if present, else latest
                main_base_path = gs.get("support_img_path") or gs.get("latest_img_path")
            except Exception:
                gs_img_pil = None

        # 2) Try single annotated image
        if gs_img_pil is None and annotate_single_gridsquare_image is not None:
            try:
                gs_img_pil = annotate_single_gridsquare_image(gs["gs_dir"])
                main_base_path = gs.get("latest_img_path")
            except Exception:
                gs_img_pil = None

        # 3) Fallback: raw latest image
        if gs_img_pil is None and gs.get("latest_img_path"):
            main_base_path = gs.get("latest_img_path")
            gs_img_pil = open_image_or_none(main_base_path)

        # --- Optional plasmon (non-support) image: only if different from main_base_path ---
        plasmon_img = None
        plasmon_path = gs.get("nonsupport_img_path")

        if plasmon_path and main_base_path:
            same_file = (os.path.realpath(plasmon_path) == os.path.realpath(main_base_path))
        else:
            same_file = False

        if plasmon_path and os.path.isfile(plasmon_path) and not same_file:
            raw_plasmon = open_image_or_none(plasmon_path)
            if raw_plasmon is not None:
                caption_text = "Energy filter plasmon image: black = empty hole"
                plasmon_img = add_plasmon_caption(raw_plasmon, caption_text)

        # --- Compute a single scale factor for both images ---
        scale = None
        main_img_h_est = 0.0
        plasmon_img_h_est = 0.0

        # Prefer main image to define scale; fall back to plasmon if main missing
        ref_img = gs_img_pil or plasmon_img
        if ref_img is not None:
            rw, rh = ref_img.size
            scale = min((avail_w - 2 * pad) / max(rw, 1), parent_max_h / max(rh, 1))

        if scale is None:
            scale = 1.0  # no images; scale won't be used

        # Estimated heights using the shared scale
        if gs_img_pil is not None:
            gw, gh = gs_img_pil.size
            main_img_h_est = gh * scale

        if plasmon_img is not None:
            pw, ph = plasmon_img.size
            plasmon_img_h_est = ph * scale

        title_h = gs_title_font_size
        parent_h = pad + title_h + 8 + main_img_h_est
        
        if plasmon_img is not None:
            parent_h += 6 + plasmon_img_h_est
        parent_h += pad

        # Page break if needed for the whole box
        if y - parent_h < margin:
            new_page()

        # Draw parent node (full width), title centered
        draw_node_box(
            c, x_left, y, avail_w, parent_h, label,
            font_name=gs_title_font_name, font_size=gs_title_font_size,
            pad=pad, title_align="center"
        )

        parent_top = y
        content_top = parent_top - (pad + title_h + gs_cfg["title_gap"])

        # --- Draw main GS image with shared scale ---
        if gs_img_pil is not None:
            iw, ih = gs_img_pil.size
            dw, dh = iw * scale, ih * scale
            x_img = x_left + pad + (avail_w - 2 * pad - dw) / 2.0
            y_img = content_top - dh
            img_reader = ImageReader(gs_img_pil)
            c.drawImage(
                img_reader,
                x_img,
                y_img,
                width=dw,
                height=dh,
                preserveAspectRatio=False,
                mask="auto",
            )
            content_top -= dh

        # --- Draw plasmon image + caption with the same scale ---
        if plasmon_img is not None:
            content_top -= 6  # small gap
            iw, ih = plasmon_img.size
            dw, dh = iw * scale, ih * scale
            x_img = x_left + pad + (avail_w - 2 * pad - dw) / 2.0
            y_img = content_top - dh
            img_reader = ImageReader(plasmon_img)
            c.drawImage(
                img_reader,
                x_img,
                y_img,
                width=dw,
                height=dh,
                preserveAspectRatio=False,
                mask="auto",
            )
            content_top -= dh + 4

        parent_bottom_y = parent_top - parent_h
        y = parent_bottom_y - gs_cfg["after_box_gap"]  # small gap under GS box

        children = gs["children"]
        if not children:
            continue
        rows = [children[i : i + columns] for i in range(0, len(children), columns)]

        child_pad = child_cfg["frame_padding"]
        
        for row in rows:
            row_heights = []
            for ch in row:
                fh_img = ch.get("foilhole_img")
                mg_img = ch.get("micro_img")
                row_heights.append(
                    measure_child_box_height(
                        c,
                        fh_img,
                        mg_img,
                        child_w,
                        title_font_size=child_title_font_size,
                        pad=child_pad,
                    )
                )
            row_h = max(row_heights) if row_heights else 0.0

            if y - row_h < margin:
                draw_page_number(c, page_num, width, margin)
                c.showPage()
                page_num += 1
                y = height - margin
                c.setFont(gs_title_font_name, gs_title_font_size)
                c.drawString(x_left, y, label + " (cont.)")
                y -= 0.18 * inch

            for idx, ch in enumerate(row):
                cx = x_left + (child_w + col_gap) * idx
                if ch.get("index"):
                    child_title = f"Foil Hole {ch['index']} (EPU {ch['key']})"
                else:
                    child_title = f"FoilHole_{ch['key']}"
                fh_img = ch.get("foilhole_img")
                mg_img = ch.get("micro_img")
                _h = draw_child_box(
                    c,
                    cx,
                    y,
                    child_w,
                    child_title,
                    fh_img,
                    mg_img,
                    title_font_size=child_title_font_size,
                    pad=child_pad,
                )

            y -= (row_h + row_gap)

        y -= 0.08 * inch

    draw_page_number(c, page_num, width, margin)
    c.save()

    if atlas_root and atlas_source in ("dm_atlasid", "dm_hint", "cli") and os.path.isfile(atlas_annotated_path):
        annotated_atlas_filename = os.path.basename(atlas_annotated_path)
        print(f"Saved {file_name} and {annotated_atlas_filename} in session directory")
    else:
        fallbacks = find_fallback_atlas_jpgs(session_dir)
        if fallbacks:
            print(
                f"Wrote {file_name}; Atlas not found in expected directory structure, "
                f"so automatically-detected atlas JPG(s) from session or parent with no annotation was displayed."
            )
        else:
            print(
                f"Wrote {file_name}; Atlas image skipped because no matching atlas was found in session or parent folder."
            )
    return 0

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python3.9 generate_report.py /path/to/session [optional:/path/to/atlas_or_name]")
        sys.exit(1)
    session_dir = sys.argv[1]
    atlas_arg = sys.argv[2] if len(sys.argv) == 3 else None
    rc = build_report(session_dir, atlas_arg)
    sys.exit(rc)

if __name__ == "__main__":
    main()
