# annotate_gridsquare.py
import warnings
warnings.filterwarnings("ignore", message=r".*longdouble.*", category=Warning)

import os
import re
import glob
import random
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
import math

import numpy as np
from PIL import Image, ImageDraw

from epu.report_style import (
    COLOR_COLLECTION,
    COLOR_SCREENING,
    COLOR_SELECTED,
    LABEL_PAD_PX,
    FONT_SIZES,
    pil_font,
)
from epu.report_utils import measure_text, draw_bold_text
from epu.report_scale_bars import add_scale_bar_by_xml

RESAMPLE_LANCZOS = getattr(Image, "LANCZOS", getattr(Image, "ANTIALIAS", Image.BICUBIC))

NS = {
    "fei": "http://schemas.datacontract.org/2004/07/Fei.SharedObjects",
    "arr": "http://schemas.microsoft.com/2003/10/Serialization/Arrays",
    "types": "http://schemas.datacontract.org/2004/07/Fei.Types",
    "common": "http://schemas.datacontract.org/2004/07/Fei.Common.Types",
    "draw": "http://schemas.datacontract.org/2004/07/System.Drawing",
    "media": "http://schemas.datacontract.org/2004/07/System.Windows.Media",
    "epu": "http://schemas.datacontract.org/2004/07/Applications.Epu.Persistence",
}

GS_IMG_RE = re.compile(r"^GridSquare_(\d{8}_\d{6})\.jpg$", re.IGNORECASE)
GS_XML_RE = re.compile(r"^GridSquare_(\d{8}_\d{6})\.xml$", re.IGNORECASE)
GS_SUPPORT_IMG_RE = re.compile(r"^GridSquare_Support_(\d{8}_\d{6})\.jpg$", re.IGNORECASE)
GS_SUPPORT_XML_RE = re.compile(r"^GridSquare_Support_(\d{8}_\d{6})\.xml$", re.IGNORECASE)
FH_XML_RE = re.compile(r"^FoilHole_(.+)_(\d{8}_\d{6})\.xml$", re.IGNORECASE)

def _ln(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else tag

def _to_float(val):
    try:
        return float(val)
    except Exception:
        return None

def _safe_dirnameN(path, n):
    d = os.path.abspath(path)
    for _ in range(n):
        d = os.path.dirname(d)
    return d

def parse_timestamp(ts_str):
    return datetime.strptime(ts_str, "%Y%m%d_%H%M%S")

# -------- Session-level: HoleSize and ImageShift calibrations from EpuSession.dm --------

def _search_xml_for_holesize(root):
    for elem in root.iter():
        if _ln(elem.tag).lower() == "holesize":
            f = _to_float(elem.text)
            if f is not None and f > 0:
                return f
    return None

def _search_text_for_holesize(text):
    float_re = re.compile(r'([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)')
    key_re = re.compile(r'(?i)hole\s*[_\-]?\s*size')
    for m in key_re.finditer(text):
        snippet = text[m.end():m.end() + 200]
        mnum = float_re.search(snippet)
        if mnum:
            f = _to_float(mnum.group(1))
            if f is not None and f > 0:
                return f
    return None

def load_session_holesize_from_dm(base_dir):
    dm_path = os.path.join(base_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return None, [f"Warning: EpuSession.dm not found at {dm_path}."]
    logs = []
    try:
        root = ET.parse(dm_path).getroot()
        f = _search_xml_for_holesize(root)
        if f is not None:
            logs.append(f"Hole size (session): {f} m")
            return f, logs
    except Exception:
        pass
    try:
        with open(dm_path, "rb") as fbin:
            text = fbin.read().decode("utf-8", errors="ignore")
        f = _search_text_for_holesize(text)
        if f is not None:
            logs.append(f"Hole size (session): {f} m")
            return f, logs
    except Exception:
        pass
    logs.append("Warning: Could not find HoleSize in EpuSession.dm.")
    return None, logs

def _find_children_by_localname(elem, name):
    return [c for c in list(elem) if _ln(c.tag) == name]

def _extract_imageshift_from_value(value_elem):
    direct = None
    for e in value_elem.iter():
        if _ln(e.tag) == "DirectCalibrations":
            direct = e
            break
    if direct is None:
        return (None, None)
    imgshift = None
    for e in direct.iter():
        if _ln(e.tag) == "ImageShift":
            imgshift = e
            break
    if imgshift is None:
        return (None, None)
    w = h = None
    for ch in imgshift.iter():
        ln = _ln(ch.tag)
        if ln == "width":
            w = _to_float(ch.text)
        elif ln == "height":
            h = _to_float(ch.text)
    return (w, h)

def _extract_microscope_settings_calibs_from_xml(root):
    calibs = {}
    for kv in root.iter():
        kv_ln = _ln(kv.tag)
        if not kv_ln.startswith("KeyValuePairOf"):
            continue
        key_elems = _find_children_by_localname(kv, "key")
        value_elems = _find_children_by_localname(kv, "value")
        if not key_elems or not value_elems:
            continue
        key_text = (key_elems[0].text or "").strip()
        value_elem = value_elems[0]
        width_m_per_unit, height_m_per_unit = _extract_imageshift_from_value(value_elem)
        if width_m_per_unit is None or height_m_per_unit is None:
            continue
        try:
            calibs[key_text] = np.array(
                [[width_m_per_unit, 0.0], [0.0, height_m_per_unit]], dtype=float
            )
        except Exception:
            continue
    return calibs

def _extract_microscope_settings_calibs_from_text(text):
    mode_pattern = r"(Atlas|GridSquare|Hole(?:/EucentricHeight)?|Acquisition|AutoFocus|DriftMeasurement|ThonRing)"
    pattern = re.compile(
        r"key\s*-\s*{mode}[\s\S]{{0,4000}}?DirectCalibrations[\s\S]{{0,2000}}?ImageShift[\s\S]{{0,500}}?"
        r"height\s*-\s*([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)"
        r"[\s\S]{{0,500}}?width\s*-\s*([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)".format(
            mode=mode_pattern
        ),
        re.IGNORECASE,
    )
    calibs = {}
    for m in pattern.finditer(text):
        mode = m.group(1)
        height = _to_float(m.group(2))
        width = _to_float(m.group(3))
        if width is None or height is None:
            continue
        calibs[mode] = np.array([[width, 0.0], [0.0, height]], dtype=float)
    return calibs

def load_imageshift_calibrations_from_dm(base_dir):
    dm_path = os.path.join(base_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return {}, [f"Warning: EpuSession.dm not found at {dm_path}."]
    logs = []
    calibs = {}
    try:
        root = ET.parse(dm_path).getroot()
        calibs = _extract_microscope_settings_calibs_from_xml(root)
    except Exception:
        calibs = {}
    if calibs:
        return calibs, logs
    try:
        with open(dm_path, "rb") as f:
            text = f.read().decode("utf-8", errors="ignore")
        calibs = _extract_microscope_settings_calibs_from_text(text)
        if calibs:
            logs.append(
                "Found ImageShift calibrations in DM (text) for modes: "
                + ", ".join(sorted(calibs.keys()))
            )
            return calibs, logs
    except Exception:
        pass
    logs.append("Warning: Could not find ImageShift calibrations in EpuSession.dm.")
    return {}, logs

def choose_calibration(calibs, preferred_modes):
    for name in preferred_modes:
        if name in calibs:
            return calibs[name]
    return None

# -------- XML parsers --------

def get_text_float(elem):
    if elem is None or elem.text is None:
        return None
    try:
        return float(elem.text)
    except Exception:
        return None

def parse_readout_area(root):
    width = height = None
    for elem in root.iter():
        if _ln(elem.tag) == "ReadoutArea":
            for ch in elem:
                lname = _ln(ch.tag)
                if lname == "width":
                    try:
                        width = int(ch.text)
                    except Exception:
                        pass
                elif lname == "height":
                    try:
                        height = int(ch.text)
                    except Exception:
                        pass
            if width is not None and height is not None:
                return width, height
    return width, height

def parse_pixelsize(root):
    px_x = get_text_float(root.find(".//fei:SpatialScale/fei:pixelSize/fei:x/fei:numericValue", NS))
    px_y = get_text_float(root.find(".//fei:SpatialScale/fei:pixelSize/fei:y/fei:numericValue", NS))
    return px_x, px_y

def parse_ref_matrix(root):
    m11 = get_text_float(root.find(".//fei:ReferenceTransformation/fei:matrix/media:_m11", NS))
    m12 = get_text_float(root.find(".//fei:ReferenceTransformation/fei:matrix/media:_m12", NS))
    m21 = get_text_float(root.find(".//fei:ReferenceTransformation/fei:matrix/media:_m21", NS))
    m22 = get_text_float(root.find(".//fei:ReferenceTransformation/fei:matrix/media:_m22", NS))
    if None in (m11, m12, m21, m22):
        return None
    return np.array([[m11, m12], [m21, m22]], dtype=float)

def parse_stage_xy(root, override=None):
    if override and ("stage_x" in override) and ("stage_y" in override):
        return override["stage_x"], override["stage_y"]
    sx = sy = None
    sx = get_text_float(root.find(".//fei:microscopeData/fei:stage/fei:Position/fei:X", NS))
    sy = get_text_float(root.find(".//fei:microscopeData/fei:stage/fei:Position/fei:Y", NS))
    if sx is not None and sy is not None:
        return sx, sy
    for e in root.iter():
        if _ln(e.tag) == "Position":
            for cc in e:
                l = _ln(cc.tag)
                if l == "X" and sx is None:
                    sx = _to_float(cc.text)
                elif l == "Y" and sy is None:
                    sy = _to_float(cc.text)
            if sx is not None and sy is not None:
                break
    return sx, sy

def parse_imageshift(root):
    x = get_text_float(root.find(".//fei:microscopeData/fei:optics/fei:ImageShift/types:_x", NS))
    y = get_text_float(root.find(".//fei:microscopeData/fei:optics/fei:ImageShift/types:_y", NS))
    return x, y

def find_first_float_by_local_name(root, local_name):
    for elem in root.iter():
        if _ln(elem.tag) == local_name:
            try:
                return float(elem.text)
            except Exception:
                continue
    return None

# -------- GridSquare metadata --------

def parse_gridsquare_meta(xml_path):
    root = ET.parse(xml_path).getroot()
    stage_x, stage_y = parse_stage_xy(root)
    px_x, px_y = parse_pixelsize(root)
    w, h = parse_readout_area(root)
    hole_size_m_xml = find_first_float_by_local_name(root, "HoleSize")
    refM = parse_ref_matrix(root)
    imgshift_x, imgshift_y = parse_imageshift(root)
    meta = {
        "stage_x": stage_x,
        "stage_y": stage_y,
        "px_x": px_x,
        "px_y": px_y,
        "width": w,
        "height": h,
        "hole_size_m_xml": hole_size_m_xml,
        "refM": refM,
        "imageshift": (imgshift_x, imgshift_y),
    }
    return meta

# -------- FoilHole DM override mapping --------

def extract_gridsquare_number_from_path(gs_xml_path):
    gs_dir = os.path.basename(os.path.dirname(gs_xml_path))
    m = re.match(r"GridSquare_(\d+)", gs_dir, flags=re.IGNORECASE)
    return m.group(1) if m else None

def find_gridsquare_dm_path(gs_xml_path):
    gs_id = extract_gridsquare_number_from_path(gs_xml_path)
    if not gs_id:
        return None
    session_root = _safe_dirnameN(gs_xml_path, 3)
    dm_path = os.path.join(session_root, "Metadata", f"GridSquare_{gs_id}.dm")
    return dm_path if os.path.isfile(dm_path) else None

def parse_dm_meta(gs_xml_path):
    dm_path = find_gridsquare_dm_path(gs_xml_path)
    if dm_path is None:
        return {}, [f"Warning: Could not locate GridSquare_<ID>.dm for {gs_xml_path}"]
    logs = []
    try:
        root = ET.parse(dm_path).getroot()
    except Exception as e:
        logs.append(f"Warning: Failed to parse DM at {dm_path}: {e}")
        return {}, logs

    tle_node = None
    for e in root.iter():
        if _ln(e.tag) == "TargetLocationsEfficient":
            tle_node = e
            break
    if tle_node is None:
        tle_node = root

    mapping = {}
    for kv in tle_node.iter():
        ln = _ln(kv.tag)
        if not ln.startswith("KeyValuePairOf") and "KeyValuePair" not in ln:
            continue
        key_elem = None
        val_elem = None
        for ch in kv:
            lnc = _ln(ch.tag)
            if lnc == "key":
                key_elem = ch
            elif lnc == "value":
                val_elem = ch
        if key_elem is None or val_elem is None:
            continue
        uniq_text = (key_elem.text or "").strip()
        if not uniq_text:
            continue
        sx = sy = None
        for node in val_elem.iter():
            if _ln(node.tag) == "StagePosition":
                for cc in node:
                    l = _ln(cc.tag)
                    if l == "X":
                        sx = _to_float(cc.text)
                    elif l == "Y":
                        sy = _to_float(cc.text)
                break
        if sx is not None and sy is not None:
            mapping[uniq_text] = {"stage_x": sx, "stage_y": sy}
    return mapping, logs

def canonicalize_uniq(u):
    if u is None:
        return None
    m = re.match(r"^(\d+)$", str(u))
    return m.group(1) if m else str(u)

# -------- Data / micrograph scanning --------

def scan_data_micrographs(gs_dir):
    data_dir = os.path.join(gs_dir, "Data")
    status = {}
    if not os.path.isdir(data_dir):
        return status

    for fname in os.listdir(data_dir):
        lower = fname.lower()
        if not (lower.endswith(".mrc") or lower.endswith(".tiff") or lower.endswith(".tif")):
            continue
        m = re.search(r"FoilHole_(\d+)", fname)
        if not m:
            continue
        uniq = canonicalize_uniq(m.group(1))
        if "fractions" in lower:
            status[uniq] = "collection"
        else:
            status.setdefault(uniq, "screening")
    return status

# -------- DM pixel centers --------

def parse_dm_pixelcenters_by_uniq(gs_xml_path):
    dm_path = find_gridsquare_dm_path(gs_xml_path)
    if dm_path is None or not os.path.isfile(dm_path):
        return {}

    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return {}

    result = {}
    for node in root.iter():
        pc = None
        pwh = None
        for ch in list(node):
            ln = _ln(ch.tag)
            if ln == "PixelCenter":
                pc = ch
            elif ln == "PixelWidthHeight":
                pwh = ch

        if pc is None:
            continue

        x = y = None
        for cc in list(pc):
            ln = _ln(cc.tag).lower()
            if ln == "x":
                x = _to_float(cc.text)
            elif ln == "y":
                y = _to_float(cc.text)
        if x is None or y is None:
            continue

        w = h = None
        if pwh is not None:
            for cc in list(pwh):
                ln = _ln(cc.tag).lower()
                if ln == "width":
                    w = _to_float(cc.text)
                elif ln == "height":
                    h = _to_float(cc.text)

        uniqs_here = set()
        for sub in node.iter():
            if _ln(sub.tag) == "BaseFileName" and sub.text:
                m = re.search(r"FoilHole_(\d+)", sub.text)
                if m:
                    uniqs_here.add(canonicalize_uniq(m.group(1)))

        for uq in uniqs_here:
            if uq not in result:
                result[uq] = {"x": x, "y": y, "width": w, "height": h}

    return result

def dm_center_to_jpg(x_mrc, y_mrc, gs_meta, W, H):
    if not gs_meta.get("width") or not gs_meta.get("height"):
        return None
    scale_x = W / float(gs_meta["width"])
    scale_y = H / float(gs_meta["height"])
    x_j = x_mrc * scale_x
    y_j = y_mrc * scale_y
    return x_j, y_j

def build_dm_pos_map(gs_xml, gs_meta, W, H):
    dm_centers = parse_dm_pixelcenters_by_uniq(gs_xml)
    dm_pos_map = {}
    for uq, info in dm_centers.items():
        x_mrc, y_mrc = info.get("x"), info.get("y")
        if x_mrc is None or y_mrc is None:
            continue
        pos = dm_center_to_jpg(x_mrc, y_mrc, gs_meta, W, H)
        if pos is None:
            continue
        xj, yj = pos
        if 0 <= xj < W and 0 <= yj < H:
            dm_pos_map[canonicalize_uniq(uq)] = (xj, yj)
    return dm_centers, dm_pos_map

# -------- Hole radius helpers --------

def hole_radii_on_jpg(hole_size_m, gs_meta, scales):
    if hole_size_m is None or gs_meta.get("px_x") is None or scales is None:
        return (None, None)
    radius_m = hole_size_m / 2.0
    px_x = gs_meta.get("px_x")
    px_y = gs_meta.get("px_y") or px_x
    scale_x, scale_y = scales
    r_mrc_x = radius_m / px_x
    r_mrc_y = radius_m / px_y
    rx = r_mrc_x * scale_x
    ry = r_mrc_y * scale_y
    return (rx, ry)

def compute_hole_radius_global(hole_size_m, gs_meta, W, H):
    if not gs_meta.get("width") or not gs_meta.get("height"):
        return None
    scales = (W / float(gs_meta["width"]), H / float(gs_meta["height"]))
    rx, ry = hole_radii_on_jpg(hole_size_m, gs_meta, scales) if (hole_size_m is not None) else (None, None)
    if rx is not None and ry is not None and np.isfinite(rx) and np.isfinite(ry):
        return float((rx + ry) / 2.0)
    return None

# -------- Marker drawing --------

def draw_marker_supersampled(od, x_c, y_c, color_rgb, radius_px_global, SUPERSAMPLE, fill_alpha=64):
    color_rgba = (color_rgb[0], color_rgb[1], color_rgb[2], 255)
    x_c_up = x_c * SUPERSAMPLE
    y_c_up = y_c * SUPERSAMPLE
    if radius_px_global is not None and np.isfinite(radius_px_global) and radius_px_global > 0:
        r_up = radius_px_global * SUPERSAMPLE
        bbox_up = [x_c_up - r_up, y_c_up - r_up, x_c_up + r_up, y_c_up + r_up]
        fill_rgba = (color_rgb[0], color_rgb[1], color_rgb[2], fill_alpha)
        od.ellipse(bbox_up, fill=fill_rgba, outline=color_rgb, width=1 * SUPERSAMPLE)
        return "circle", radius_px_global
    else:
        s_up = 6.0 * SUPERSAMPLE
        od.line([x_c_up - s_up, y_c_up, x_c_up + s_up, y_c_up], fill=color_rgba, width=1 * SUPERSAMPLE)
        od.line([x_c_up, y_c_up - s_up, x_c_up, y_c_up + s_up], fill=color_rgba, width=1 * SUPERSAMPLE)
        return "cross", 6.0

# -------- FoilHole discovery and GridSquare file pickers --------

def find_unique_foilhole_xmls_earliest_latest(foilholes_dir, min_ts: datetime | None = None):
    if not os.path.isdir(foilholes_dir):
        return []
    records = {}
    for fname in os.listdir(foilholes_dir):
        m = FH_XML_RE.match(fname)
        if not m:
            continue
        uniq, ts_str = m.group(1), m.group(2)
        try:
            ts = parse_timestamp(ts_str)
        except Exception:
            continue

        # NEW: filter out template-definition FoilHoles (too early)
        if min_ts is not None and ts < min_ts:
            continue

        full = os.path.join(foilholes_dir, fname)
        rec = records.get(uniq)
        if rec is None:
            records[uniq] = {
                "earliest": full,
                "latest": full,
                "earliest_ts": ts,
                "latest_ts": ts,
            }
        else:
            if ts < rec["earliest_ts"]:
                rec["earliest"] = full
                rec["earliest_ts"] = ts
            if ts > rec["latest_ts"]:
                rec["latest"] = full
                rec["latest_ts"] = ts
    sorted_records_list = sorted(records.items(), key=lambda item: item[1]["earliest_ts"])
    return sorted_records_list

def find_latest_gridsquare_files(gs_dir):
    jpgs = []
    xmls = {}

    for fname in os.listdir(gs_dir):
        full = os.path.join(gs_dir, fname)

        ts = None
        m_img = GS_SUPPORT_IMG_RE.match(fname)
        if m_img:
            try:
                ts = parse_timestamp(m_img.group(1))
            except Exception:
                ts = None
        else:
            m_img = GS_IMG_RE.match(fname)
            if m_img:
                try:
                    ts = parse_timestamp(m_img.group(1))
                except Exception:
                    ts = None

        if m_img and ts is not None:
            jpgs.append((ts, fname))

        m_xml = GS_SUPPORT_XML_RE.match(fname)
        if m_xml:
            try:
                ts_xml = parse_timestamp(m_xml.group(1))
            except Exception:
                ts_xml = None
        else:
            m_xml = GS_XML_RE.match(fname)
            if m_xml:
                try:
                    ts_xml = parse_timestamp(m_xml.group(1))
                except Exception:
                    ts_xml = None

        if m_xml and ts_xml is not None:
            xmls[m_xml.group(1)] = fname

    if not jpgs:
        return None, None, None

    jpgs.sort(key=lambda x: x[0])
    latest_ts, latest_jpg = jpgs[-1]
    ts_str = latest_ts.strftime("%Y%m%d_%H%M%S")
    xml_name = xmls.get(ts_str)
    if xml_name is None:
        return None, None, None

    return os.path.join(gs_dir, latest_jpg), os.path.join(gs_dir, xml_name), ts_str

def find_latest_gridsquare_jpg_relaxed(gs_dir):
    jpgs = []
    for fname in os.listdir(gs_dir):
        m = GS_IMG_RE.match(fname)
        ts = None
        if m:
            try:
                ts = parse_timestamp(m.group(1))
            except Exception:
                ts = None
        full = os.path.join(gs_dir, fname)
        if ts is not None:
            jpgs.append((ts, full))
        else:
            try:
                jpgs.append((datetime.fromtimestamp(os.path.getmtime(full)), full))
            except Exception:
                pass
    if not jpgs:
        return None
    jpgs.sort(key=lambda x: x[0], reverse=True)
    return jpgs[0][1]

def find_latest_gridsquare_xml_relaxed(gs_dir):
    xmls = []
    for fname in os.listdir(gs_dir):
        m = GS_XML_RE.match(fname)
        ts = None
        if m:
            try:
                ts = parse_timestamp(m.group(1))
            except Exception:
                ts = None
        full = os.path.join(gs_dir, fname)
        if fname.lower().endswith(".xml"):
            if ts is not None:
                xmls.append((ts, full))
            else:
                try:
                    xmls.append((datetime.fromtimestamp(os.path.getmtime(full)), full))
                except Exception:
                    pass
    if not xmls:
        return None
    xmls.sort(key=lambda x: x[0], reverse=True)
    return xmls[0][1]

def _parse_ts_from_name(fname, regex):
    m = regex.match(fname)
    if not m:
        return None
    try:
        return parse_timestamp(m.group(1))
    except Exception:
        return None

def find_best_gridsquare_xml_for_jpg(gs_dir, gs_jpg_path):
    jpg_name = os.path.basename(gs_jpg_path)
    ts_jpg = _parse_ts_from_name(jpg_name, GS_IMG_RE)
    candidates = []
    for fname in os.listdir(gs_dir):
        if not fname.lower().endswith(".xml"):
            continue
        full = os.path.join(gs_dir, fname)
        ts_xml = _parse_ts_from_name(fname, GS_XML_RE)
        if ts_xml is not None and ts_jpg is not None:
            diff = abs((ts_xml - ts_jpg).total_seconds())
            candidates.append((diff, full))
        else:
            try:
                mtime_xml = os.path.getmtime(full)
                mtime_jpg = os.path.getmtime(gs_jpg_path)
                diff = abs(mtime_xml - mtime_jpg)
                candidates.append((diff, full))
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]

def locate_gs_jpg_xml(gs_dir):
    gs_jpg, gs_xml, _ = find_latest_gridsquare_files(gs_dir)
    if gs_jpg is None:
        gs_jpg = find_latest_gridsquare_jpg_relaxed(gs_dir)
    if gs_xml is None and gs_jpg is not None:
        gs_xml = find_best_gridsquare_xml_for_jpg(gs_dir, gs_jpg)
    if gs_xml is None:
        gs_xml = find_latest_gridsquare_xml_relaxed(gs_dir)
    return gs_jpg, gs_xml

# -------- FoilHole metadata --------

def extract_foilhole_uniq_from_path(fh_xml_path):
    fname = os.path.basename(fh_xml_path)
    m = re.match(r"FoilHole_(.+)_(\d{8}_\d{6})\.xml$", fname, flags=re.IGNORECASE)
    return m.group(1) if m else None

def parse_foilhole_meta(fh_xml_path, dm_stage_lookup=None):
    root = ET.parse(fh_xml_path).getroot()
    w, h = parse_readout_area(root)
    px_x, px_y = parse_pixelsize(root)
    refM = parse_ref_matrix(root)
    isx, isy = parse_imageshift(root)
    uniq = extract_foilhole_uniq_from_path(fh_xml_path)

    sx_xml, sy_xml = parse_stage_xy(root)
    if dm_stage_lookup and uniq and uniq in dm_stage_lookup:
        sx = dm_stage_lookup[uniq]["stage_x"]
        sy = dm_stage_lookup[uniq]["stage_y"]
    else:
        sx, sy = sx_xml, sy_xml

    return {
        "width": w,
        "height": h,
        "px_x": px_x,
        "px_y": px_y,
        "stage_x": sx,
        "stage_y": sy,
        "imageshift": (isx, isy),
        "refM": refM,
        "uniq": uniq,
    }

# -------- Selection of holes --------

def _deterministic_seed_for_grid(gs_dir, uniqs_order):
    s = os.path.abspath(gs_dir) + "|" + "|".join(uniqs_order)
    digest = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)

def get_selected_holes_for_gridsquare(gs_dir, max_show=12, min_ts: datetime | None = None):
    gs_jpg, gs_xml = locate_gs_jpg_xml(gs_dir)
    if gs_jpg is None or gs_xml is None:
        return [], {}

    gs_meta = parse_gridsquare_meta(gs_xml)
    base_img = Image.open(gs_jpg).convert("RGB")
    W, H = base_img.size
    dm_centers, dm_pos_map = build_dm_pos_map(gs_xml, gs_meta, W, H)
    foilholes_dir = os.path.join(gs_dir, "FoilHoles")
    fh_map = find_unique_foilhole_xmls_earliest_latest(foilholes_dir, min_ts=min_ts)
    data_status = scan_data_micrographs(gs_dir)

    earliest_order = [
        canonicalize_uniq(uq) for (uq, _rec) in fh_map if canonicalize_uniq(uq) in dm_pos_map
    ]
    screening_list = [uq for uq in earliest_order if data_status.get(uq) == "screening"]
    selected_list = [uq for uq in earliest_order if data_status.get(uq) not in ("screening", "collection")]
    collection_list = [uq for uq in earliest_order if data_status.get(uq) == "collection"]

    seed = _deterministic_seed_for_grid(gs_dir, earliest_order)
    rng = random.Random(seed)
    chosen = []
    if len(screening_list) >= max_show:
        chosen = rng.sample(screening_list, max_show)
    else:
        chosen = list(screening_list)
        remaining = max_show - len(chosen)
        if len(collection_list) <= remaining:
            chosen.extend(collection_list)
        else:
            chosen.extend(rng.sample(collection_list, remaining))
        remaining = max_show - len(chosen)
        if remaining > 0:
            if len(selected_list) <= remaining:
                chosen.extend(selected_list)
            else:
                chosen.extend(rng.sample(selected_list, remaining))
    chosen_set = set(chosen)

    keys_selected_ordered = []
    idx_map = {}
    idx = 1
    for uq in earliest_order:
        if uq in chosen_set:
            keys_selected_ordered.append(uq)
            idx_map[uq] = idx
            idx += 1
            if idx > max_show:
                break
    return keys_selected_ordered, idx_map

def foilhole_color_for_uniq(gs_dir, uniq):
    """
    Determine hole color based on presence of micrograph files in Data:
        - White if no micrograph
        - Blue if Fractions exists
        - Orange if micrograph but no fractions exists
    """
    data_dir = os.path.join(gs_dir, "Data")
    if not os.path.isdir(data_dir):
        return COLOR_SELECTED
    frac = glob.glob(os.path.join(data_dir, f"FoilHole_{uniq}*Fractions.*"))
    plain = [p for p in glob.glob(os.path.join(data_dir, f"FoilHole_{uniq}*.*"))
                if "Fractions" not in os.path.basename(p)]
    if frac:
        return COLOR_COLLECTION
    if plain:
        return COLOR_SCREENING
    return COLOR_SELECTED

# -------- Comment helper --------

def append_comment_central(
    img,
    comment_text,
    comment_font,
    is_two_panel: bool,
    side_margin: int = 10,
    top_margin: int = 6,
    bottom_margin: int = 10,
):
    if img is None or not comment_text:
        return img

#    comment_font = pil_font(FONT_SIZES["note"], italic=True)

    W, H = img.size
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    try:
        bbox = tmp_draw.textbbox((0, 0), comment_text, font=comment_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_w, text_h = tmp_draw.textsize(comment_text, font=comment_font)

    if is_two_panel:
        W_out = W
    else:
        required_w = side_margin + text_w + side_margin
        W_out = max(W, required_w)

    block_h = top_margin + text_h + bottom_margin
    out = Image.new("RGB", (W_out, H + block_h), color=(255, 255, 255))

    x_img = (W_out - W) // 2
    out.paste(img, (x_img, 0))

    draw = ImageDraw.Draw(out)
    x_text = (W_out - text_w) // 2
    y_text = H + top_margin
    draw.text((x_text, y_text), comment_text, fill=(0, 0, 0), font=comment_font)

    return out

def add_plasmon_caption(img, caption_text: str):
    """
    Append a centered plasmon caption below the given image,
    using the same style as other GridSquare notes.
    """
    if img is None or not caption_text:
        return img

    # Use the same semantic size as the GridSquare note
    comment_font = pil_font(FONT_SIZES["note"], italic=True)

    return append_comment_central(
        img,
        caption_text,
        comment_font,
        is_two_panel=False,
        side_margin=10,
        top_margin=6,
        bottom_margin=10,
    )

# -------- Main annotators --------

def annotate_gridsquare_left(gs_dir, min_ts: datetime | None = None):
    """
    Build the left GridSquare panel: GS image with hole overlays and labels,
    but WITHOUT legend or comment.
    """
    gs_jpg, gs_xml = locate_gs_jpg_xml(gs_dir)
    if gs_jpg is None or gs_xml is None:
        return Image.open(gs_jpg).convert("RGB") if gs_jpg else None

    base_dir = _safe_dirnameN(gs_dir, 2)
    session_holesize_m, _ = load_session_holesize_from_dm(base_dir)

    gs_meta = parse_gridsquare_meta(gs_xml)
    dm_meta, _ = parse_dm_meta(gs_xml)

    foilholes_dir = os.path.join(gs_dir, "FoilHoles")
    fh_map = find_unique_foilhole_xmls_earliest_latest(foilholes_dir, min_ts=min_ts)

    img = Image.open(gs_jpg).convert("RGB")
    W, H = img.size
    if not gs_meta.get("width") or not gs_meta.get("height"):
        return img

    hole_size_m = session_holesize_m if (session_holesize_m is not None) else gs_meta.get("hole_size_m_xml")
    radius_px_global = compute_hole_radius_global(hole_size_m, gs_meta, W, H)

    SUPERSAMPLE = 3
    overlay_up = Image.new("RGBA", (W * SUPERSAMPLE, H * SUPERSAMPLE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay_up)

    labels_to_draw = []

    dm_centers, dm_pos_map = build_dm_pos_map(gs_xml, gs_meta, W, H)

    try:
        keys_selected, idx_map = get_selected_holes_for_gridsquare(gs_dir, max_show=12, min_ts=min_ts)
        chosen_set = set(keys_selected)
    except Exception:
        data_status = scan_data_micrographs(gs_dir)
        earliest_order = [
            canonicalize_uniq(uq) for (uq, _rec) in fh_map if canonicalize_uniq(uq) in dm_pos_map
        ]
        screening_list = [uq for uq in earliest_order if data_status.get(uq) == "screening"]
        selected_list = [uq for uq in earliest_order if data_status.get(uq) not in ("screening", "collection")]
        collection_list = [uq for uq in earliest_order if data_status.get(uq) == "collection"]
        MAX_SHOW = 12
        chosen = []
        if len(screening_list) >= MAX_SHOW:
            chosen = random.sample(screening_list, MAX_SHOW)
        else:
            chosen = list(screening_list)
            remaining = MAX_SHOW - len(chosen)
            if len(selected_list) <= remaining:
                chosen.extend(selected_list)
            else:
                chosen.extend(random.sample(selected_list, remaining))
            remaining = MAX_SHOW - len(chosen)
            if remaining > 0:
                if len(collection_list) <= remaining:
                    chosen.extend(collection_list)
                else:
                    chosen.extend(random.sample(collection_list, remaining))
        chosen_set = set(chosen)
        idx_map = {uq: i + 1 for i, uq in enumerate(earliest_order) if uq in chosen_set}

    for uniq, rec in fh_map:
        uniq_canon = canonicalize_uniq(uniq)
        if uniq_canon not in chosen_set:
            continue

        fh_xml = rec.get("earliest", None) or rec.get("latest", None)
        if fh_xml is None:
            continue
        fh_meta = parse_foilhole_meta(fh_xml, dm_stage_lookup=dm_meta)

        pos_dm = dm_pos_map.get(uniq_canon)
        if pos_dm is None:
            continue

        x_c, y_c = float(pos_dm[0]), float(pos_dm[1])
        if not (0 <= x_c < W and 0 <= y_c < H):
            continue

        color_rgb = foilhole_color_for_uniq(gs_dir, fh_meta.get("uniq"))
        shape, shape_size = draw_marker_supersampled(od, x_c, y_c, color_rgb, radius_px_global, SUPERSAMPLE)

        x_label_base = x_c + (shape_size + LABEL_PAD_PX) if shape == "circle" else x_c + shape_size
        x_label_base = max(2.0, min(x_label_base, W - 2.0))
        y_label_base = max(2.0, min(y_c, H - 2.0))

        label_num = str(idx_map.get(uniq_canon, ""))
        labels_to_draw.append((x_label_base, y_label_base, label_num, color_rgb))

    overlay = overlay_up.resize((W, H), resample=RESAMPLE_LANCZOS)
    base_rgba = img.convert("RGBA")
    base_rgba.alpha_composite(overlay)

    draw_text = ImageDraw.Draw(base_rgba)
    label_font = pil_font(FONT_SIZES["caption"], bold=True)
    for x_label, y_label, label, color_rgb in labels_to_draw:
        draw_bold_text(draw_text, (x_label, y_label), label, fill=color_rgb, font=label_font)
    base_rgba = add_scale_bar_by_xml(base_rgba, gs_xml, bar_um=10, align="left", font_size=FONT_SIZES["scale_bar"])

    return base_rgba.convert("RGB")

def annotate_gridsquare_right(gs_dir):
    """
    Build the right GridSquare panel showing collection holes only.
    Returns None if there are no collection holes.
    """
    gs_jpg, gs_xml = locate_gs_jpg_xml(gs_dir)
    if gs_jpg is None or gs_xml is None:
        return None

    data_status = scan_data_micrographs(gs_dir)
    collection_uniqs = {uq for uq, st in data_status.items() if st == "collection"}
    if not collection_uniqs:
        return None

    gs_meta = parse_gridsquare_meta(gs_xml)
    base_img = Image.open(gs_jpg).convert("RGB")
    W, H = base_img.size

    right_gs = Image.new("RGB", (W, H), color=(255, 255, 255))
    right_gs.paste(base_img, (0, 0))

    dm_centers, dm_pos_map = build_dm_pos_map(gs_xml, gs_meta, W, H)

    SUPERSAMPLE = 3
    overlay_up = Image.new("RGBA", (W * SUPERSAMPLE, H * SUPERSAMPLE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay_up)

    base_dir = _safe_dirnameN(gs_dir, 2)
    session_holesize_m, _ = load_session_holesize_from_dm(base_dir)
    hole_size_m = session_holesize_m if (session_holesize_m is not None) else gs_meta.get("hole_size_m_xml")
    radius_px_global_fallback = compute_hole_radius_global(hole_size_m, gs_meta, W, H)

    for uq in sorted(collection_uniqs):
        info = dm_centers.get(uq)
        pos = dm_pos_map.get(canonicalize_uniq(uq))
        if info is None or pos is None:
            continue
        x_jpg, y_jpg = pos
        if not (0 <= x_jpg < W and 0 <= y_jpg < H):
            continue
        w_px = info.get("width")
        h_px = info.get("height")
        if w_px and h_px and w_px > 0 and h_px > 0 and gs_meta.get("width") and gs_meta.get("height"):
            scale_x = W / float(gs_meta["width"])
            scale_y = H / float(gs_meta["height"])
            rx = (w_px / 2.0) * scale_x
            ry = (h_px / 2.0) * scale_y
            radius_px_global = (rx + ry) / 2.0
        else:
            radius_px_global = radius_px_global_fallback

        color_rgb = COLOR_COLLECTION
        draw_marker_supersampled(od, x_jpg, y_jpg, color_rgb, radius_px_global, SUPERSAMPLE)

    overlay = overlay_up.resize((W, H), resample=RESAMPLE_LANCZOS)
    right_rgba = right_gs.convert("RGBA")
    right_rgba.paste(overlay, (0, 0), overlay)
    right_gs = right_rgba.convert("RGB")

    return right_gs

def _add_gridsquare_legend_row(img):
    W_img, H_img = img.size

    legend_items = [
        (COLOR_COLLECTION, "Collection Hole"),
        (COLOR_SCREENING, "Screening Hole"),
        (COLOR_SELECTED, "Selected, No Micrographs"),
    ]
    legend_font = pil_font(FONT_SIZES["caption"], bold=False)
    swatch_size = 14
    swatch_gap = 8
    item_gap = 16
    side_margin = 10
    top_margin_row = 10
    bottom_margin_row = 10

    tmp_img = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    legend_item_widths = []
    legend_item_height = 0
    legend_text_heights = []
    for color, label in legend_items:
        tw, th = measure_text(tmp_draw, label, legend_font)
        legend_item_widths.append(swatch_size + swatch_gap + tw)
        legend_text_heights.append(th)
        legend_item_height = max(legend_item_height, max(th, swatch_size))
    legend_total_width = sum(legend_item_widths) + item_gap * (len(legend_items) - 1)

    legend_required_w = side_margin + legend_total_width + side_margin
    W_out = max(W_img, legend_required_w)

    row_content_h = legend_item_height
    row_total_h = top_margin_row + row_content_h + bottom_margin_row

    out = Image.new("RGB", (W_out, H_img + row_total_h), color=(255, 255, 255))
    x_img = (W_out - W_img) // 2
    out.paste(img, (x_img, 0))
    draw = ImageDraw.Draw(out)

    y_row_top = H_img + top_margin_row
    legend_x_start = max(side_margin, (W_out - legend_total_width) // 2)
    y_swatch_top = y_row_top + int((row_content_h - swatch_size) / 2)
    x = legend_x_start
    for (color, label), th in zip(legend_items, legend_text_heights):
        draw.rectangle(
            [x, y_swatch_top, x + swatch_size, y_swatch_top + swatch_size],
            fill=color,
            outline=(0, 0, 0),
        )
        x += swatch_size + swatch_gap
        tw, th = measure_text(draw, label, legend_font)
        y_text = y_swatch_top + (swatch_size - th) // 2
        draw.text((x, y_text), label, fill=(0, 0, 0), font=legend_font)
        x += tw + item_gap

    return out

def compile_gridsquare_images(gs_dir, left_img, right_img):
    if left_img is None:
        return None

    if right_img is None:
        comment_text = (
            "A maximum of 12 holes (priority: screening, selected no micrographs) "
            "are chosen for numbering and display"
        )
        left_with_legend = _add_gridsquare_legend_row(left_img)
        return append_comment_central(
            left_with_legend,
            comment_text,
            pil_font(FONT_SIZES["note"], italic=False),
            is_two_panel=False,
            side_margin=10,
            top_margin=6,
            bottom_margin=10,
        )

    Lw, Lh = left_img.size
    Rw, Rh = right_img.size

    tab_spaces = 4
    legend_font = pil_font(FONT_SIZES["caption"], bold=False)
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    gap_px = max(1, measure_text(tmp_draw, " " * tab_spaces, legend_font)[0])

    combined_w = Lw + gap_px + Rw
    combined_h = max(Lh, Rh)
    combined_gs = Image.new("RGB", (combined_w, combined_h), color=(255, 255, 255))
    combined_gs.paste(left_img, (0, 0))
    combined_gs.paste(right_img, (Lw + gap_px, 0))

    combined_with_legend = _add_gridsquare_legend_row(combined_gs)

    comment_text = (
        "A maximum of 12 holes (priority: screening, collection, selected no micrographs) "
        "are chosen for numbering and display"
    )
    final_with_comment = append_comment_central(
        combined_with_legend,
        comment_text,
        pil_font(FONT_SIZES["note"], italic=False),
        is_two_panel=True,
        side_margin=10,
        top_margin=6,
        bottom_margin=10,
    )
    return final_with_comment

def annotate_single_gridsquare_image(gs_dir, min_ts: datetime | None = None):
    left_gs = annotate_gridsquare_left(gs_dir, min_ts=min_ts)
    return compile_gridsquare_images(gs_dir, left_gs, right_img=None)

def annotate_gridsquare_image_or_pair(gs_dir, min_ts: datetime | None = None):
    left_gs = annotate_gridsquare_left(gs_dir, min_ts=min_ts)
    right_gs = annotate_gridsquare_right(gs_dir)  # right panel is "collection-only"; usually fine unfiltered
    return compile_gridsquare_images(gs_dir, left_gs, right_gs)
