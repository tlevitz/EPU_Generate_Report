# annotate_foilhole.py

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw

from epu.report_style import FONT_SIZES, pil_font, IMAGE_LAYOUT
from epu.report_utils import measure_text
from epu.annotate_gridsquare import (
    _ln,
    _to_float,
    load_session_holesize_from_dm,
    parse_readout_area,
    parse_pixelsize,
)
from epu.report_scale_bars import add_scale_bar_by_xml

FOILHOLE_RE = re.compile(r"^FoilHole_([A-Za-z0-9]+)_(\d{8})_(\d{6})\.jpg$", re.IGNORECASE)
MICROGRAPH_RE = re.compile(
    r"^FoilHole_([A-Za-z0-9]+)_Data_[^_]+_[^_]+_(\d{8})_(\d{6})\.jpg$",
    re.IGNORECASE,
)

NS = {
    "fei": "http://schemas.datacontract.org/2004/07/Fei.SharedObjects",
    "arr": "http://schemas.microsoft.com/2003/10/Serialization/Arrays",
    "types": "http://schemas.datacontract.org/2004/07/Fei.Types",
    "common": "http://schemas.datacontract.org/2004/07/Fei.Common.Types",
    "draw": "http://schemas.datacontract.org/2004/07/System.Drawing",
    "media": "http://schemas.datacontract.org/2004/07/System.Windows.Media",
    "epu": "http://schemas.datacontract.org/2004/07/Applications.Epu.Persistence",
}

def _parse_datetime_tokens(date_str: str, time_str: str):
    try:
        return datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    except Exception:
        return (date_str, time_str)

def _find_gridsquares(base_folder: str):
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

def _latest_foilhole_with_micrograph(gs_dir: str) -> Optional[Tuple[str, str]]:
    """
    Return (foilhole_jpg_path, micrograph_jpg_path) for the latest FoilHole
    in this GridSquare that has a matching micrograph, or None if none found.
    """
    holes_dir = os.path.join(gs_dir, "FoilHoles")
    data_dir = os.path.join(gs_dir, "Data")
    if not (os.path.isdir(holes_dir) and os.path.isdir(data_dir)):
        return None

    # Collect latest FoilHole JPG per key
    groups = {}
    for name in os.listdir(holes_dir):
        m = FOILHOLE_RE.match(name)
        if not m:
            continue
        key, date_str, time_str = m.group(1), m.group(2), m.group(3)
        path = os.path.join(holes_dir, name)
        dt = _parse_datetime_tokens(date_str, time_str)
        prev = groups.get(key)
        if prev is None or dt > prev[0]:
            groups[key] = (dt, path, date_str, time_str)

    if not groups:
        return None

    # For each key, see if there is a matching micrograph; keep latest by time
    candidates = []
    for key, (dt, fh_path, date_str, time_str) in groups.items():
        # Find matching micrograph in Data
        best_micro = None
        best_dt = None
        for name in os.listdir(data_dir):
            m = MICROGRAPH_RE.match(name)
            if not m:
                continue
            key_m, d_str, t_str = m.group(1), m.group(2), m.group(3)
            if key_m != key:
                continue
            dt_m = _parse_datetime_tokens(d_str, t_str)
            if best_micro is None or dt_m > best_dt:
                best_micro = os.path.join(data_dir, name)
                best_dt = dt_m
        if best_micro is not None:
            candidates.append((dt, fh_path, best_micro))

    if not candidates:
        return None

    # Latest by FoilHole time
    candidates.sort(key=lambda tup: tup[0], reverse=True)
    _, fh_path, micro_path = candidates[0]
    return fh_path, micro_path

def _find_matching_foilhole_xml(fh_jpg_path: str) -> Optional[str]:
    """
    Given a FoilHole_XXX_YYYYMMDD_HHMMSS.jpg, find the corresponding XML
    in the same FoilHoles directory (matching uniq and timestamp).
    """
    fname = os.path.basename(fh_jpg_path)
    m = FOILHOLE_RE.match(fname)
    if not m:
        return None
    key, date_str, time_str = m.group(1), m.group(2), m.group(3)
    ts_str = f"{date_str}_{time_str}"
    holes_dir = os.path.dirname(fh_jpg_path)
    xml_name = f"FoilHole_{key}_{ts_str}.xml"
    xml_path = os.path.join(holes_dir, xml_name)
    if os.path.isfile(xml_path):
        return xml_path

    # Fallback: any FoilHole_<key>_*.xml with closest timestamp
    xml_candidates = []
    for name in os.listdir(holes_dir):
        if not name.lower().endswith(".xml"):
            continue
        if not name.lower().startswith(f"foilhole_{key.lower()}_"):
            continue
        full = os.path.join(holes_dir, name)
        try:
            # Expect FoilHole_<key>_YYYYMMDD_HHMMSS.xml
            parts = name.rsplit("_", 2)
            if len(parts) < 3:
                continue
            dt_str = parts[-2] + "_" + parts[-1].split(".")[0]
            dt = datetime.strptime(dt_str, "%Y%m%d_%H%M%S")
        except Exception:
            continue
        xml_candidates.append((dt, full))

    if not xml_candidates:
        return None

    xml_candidates.sort(key=lambda t: t[0], reverse=True)
    return xml_candidates[0][1]

def _parse_foilhole_center_from_xml(xml_path: str):
    """
    Parse FoilHole XML to get the hole center in image pixels.
    Prefer a dedicated center if present; otherwise fall back to image center.
    """
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return None, None, None, None, None, None

    # Readout area (image size)
    w, h = parse_readout_area(root)
    px_x, px_y = parse_pixelsize(root)

    # Try to find a center element (various schemas)
    cx = cy = None
    # 1) Look for something like FindFoilHoleCenterResults / Center / x,y
    for elem in root.iter():
        if _ln(elem.tag).lower() == "findfoilholecenterresults":
            for ch in elem.iter():
                ln = _ln(ch.tag).lower()
                if ln == "center":
                    # children x,y
                    for cc in ch:
                        l2 = _ln(cc.tag).lower()
                        if l2 == "x":
                            cx = _to_float(cc.text)
                        elif l2 == "y":
                            cy = _to_float(cc.text)
                    break

    # 2) Fallback: any element named Center with x,y children
    if cx is None or cy is None:
        for elem in root.iter():
            if _ln(elem.tag).lower() == "center":
                tmp_x = tmp_y = None
                for cc in elem:
                    l2 = _ln(cc.tag).lower()
                    if l2 == "x":
                        tmp_x = _to_float(cc.text)
                    elif l2 == "y":
                        tmp_y = _to_float(cc.text)
                if tmp_x is not None and tmp_y is not None:
                    cx, cy = tmp_x, tmp_y
                    break

    # 3) If still missing, use image center if we know width/height
    if (cx is None or cy is None) and (w is not None and h is not None):
        cx = w / 2.0
        cy = h / 2.0

    return cx, cy, w, h, px_x, px_y

def _compute_radius_pixels(hole_size_m: Optional[float],
                           px_x: Optional[float],
                           img_width: int,
                           W_xml: Optional[int] = None,
                           W_jpg: Optional[int] = None) -> Optional[float]:
    """
    Compute hole radius in pixels for the FoilHole JPG image.

    hole_size_m : physical hole diameter in meters
    px_x        : pixel size in meters/pixel (for the acquisition image, e.g. MRC/TIFF)
    img_width   : width of the JPG (for sanity checks)
    W_xml       : width of the acquisition image in pixels (from FoilHole XML ReadoutArea)
    W_jpg       : width of the JPG in pixels (if None, falls back to img_width)
    """
    if hole_size_m is None or px_x is None or px_x <= 0:
        return None

    radius_m = hole_size_m / 2.0
    # Radius in acquisition-image pixels
    radius_px_acq = radius_m / px_x

    # Scale to JPG pixels if we know the ratio
    if W_xml and W_xml > 0 and W_jpg and W_jpg > 0:
        scale_x = float(W_jpg) / float(W_xml)
        radius_px = radius_px_acq * scale_x
    else:
        # Fallback: assume 1:1 (may be wrong, but better than nothing)
        radius_px = radius_px_acq

    # Sanity check
    if radius_px <= 0 or radius_px > img_width * 2:
        return None
    return radius_px

def _parse_template_areas_from_dm(session_dir: str):
    """
    Parse EpuSession.dm and return:
      - template_px_size: (px_width_m, px_height_m) or (None, None)
      - autofocus_shift: (dx_px, dy_px) or None
      - acquisition_shifts: list of (dx_px, dy_px)
      - drift_shift: (dx_px, dy_px) or None
    """
    dm_path = os.path.join(session_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return (None, None), None, [], None

    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return (None, None), None, [], None

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
                dx = _to_float(ch.text)
            elif ln == "height":
                dy = _to_float(ch.text)
        if dx is None or dy is None:
            return None
        return (dx, dy)

    # TemplateImagePixelSize
    px_w = px_h = None
    tip = find_first(root, "TemplateImagePixelSize")
    if tip is not None:
        for ch in tip.iter():
            ln = _ln(ch.tag).lower()
            if ln == "width":
                px_w = _to_float(ch.text)
            elif ln == "height":
                px_h = _to_float(ch.text)

    # AutoFocusArea
    af_node = find_first(root, "AutoFocusArea")
    af_shift = None
    if af_node is not None:
        sh = find_first(af_node, "ShiftInPixels")
        af_shift = parse_shift(sh)

    # DataAcquisitionAreas
    acq_shifts: List[Tuple[float, float]] = []
    daa_node = find_first(root, "DataAcquisitionAreas")
    if daa_node is not None:
        for kv in daa_node.iter():
            ln = _ln(kv.tag)
            if "KeyValuePair" not in ln:
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
                acq_shifts.append(s)

    # DriftStabilizationArea
    drift_node = find_first(root, "DriftStabilizationArea")
    drift_shift = None
    if drift_node is not None:
        sh = find_first(drift_node, "ShiftInPixels")
        drift_shift = parse_shift(sh)

    return (px_w, px_h), af_shift, acq_shifts, drift_shift

def _parse_micrograph_settings_from_dm(session_dir: str):
    """
    Parse EpuSession.dm to get micrograph acquisition settings for the main
    DataAcquisition / DriftMeasurement mode.

    Returns a dict with:
      {
        "beam_diameter_m": float or None,
        "cam_width_px": int or None,
        "cam_height_px": int or None,
        "binning_x": int or None,
        "binning_y": int or None,
      }
    """
    dm_path = os.path.join(session_dir, "EpuSession.dm")
    out = {
        "beam_diameter_m": None,
        "cam_width_px": None,
        "cam_height_px": None,
        "binning_x": None,
        "binning_y": None,
    }
    if not os.path.isfile(dm_path):
        return out

    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return out

    def localname(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    # Find MicroscopeSettings
    ms_root = None
    for elem in root.iter():
        if localname(elem.tag) == "MicroscopeSettings":
            ms_root = elem
            break
    if ms_root is None:
        return out

    # Iterate KeyValuePairOfExperimentSettingsIdMicroscopeSettings...
    for kv in ms_root.iter():
        ln = localname(kv.tag)
        if "KeyValuePairOfExperimentSettingsIdMicroscopeSettings" not in ln:
            continue
        key_elem = None
        val_elem = None
        for ch in kv:
            lnc = localname(ch.tag)
            if lnc == "key":
                key_elem = ch
            elif lnc == "value":
                val_elem = ch
        if key_elem is None or val_elem is None:
            continue
        key_text = (key_elem.text or "").strip()

        # Heuristic: pick DataAcquisition or DriftMeasurement as the main micrograph mode
        if key_text not in ("DataAcquisition", "DriftMeasurement", "Drift Measurement"):
            continue

        acq = None
        optics = None
        for ch in val_elem:
            lnc = localname(ch.tag)
            if lnc == "Acquisition":
                acq = ch
            elif lnc == "Optics":
                optics = ch

        # Beam diameter
        if optics is not None:
            for ch in optics.iter():
                if localname(ch.tag) == "BeamDiameter":
                    try:
                        out["beam_diameter_m"] = float(ch.text)
                    except Exception:
                        pass

        # Camera readout + binning
        if acq is not None:
            cam = None
            for ch in acq:
                if localname(ch.tag) == "camera":
                    cam = ch
                    break
            if cam is not None:
                # Binning
                for ch in cam.iter():
                    if localname(ch.tag) == "Binning":
                        bx = by = None
                        for cc in ch:
                            ln2 = localname(cc.tag)
                            if ln2 == "x":
                                try:
                                    bx = int(cc.text)
                                except Exception:
                                    pass
                            elif ln2 == "y":
                                try:
                                    by = int(cc.text)
                                except Exception:
                                    pass
                        out["binning_x"] = bx
                        out["binning_y"] = by
                    if localname(ch.tag) == "ReadoutArea":
                        w = h = None
                        for cc in ch:
                            ln2 = localname(cc.tag)
                            if ln2 == "width":
                                try:
                                    w = int(cc.text)
                                except Exception:
                                    pass
                            elif ln2 == "height":
                                try:
                                    h = int(cc.text)
                                except Exception:
                                    pass
                        out["cam_width_px"] = w
                        out["cam_height_px"] = h

        break  # stop after first matching mode

    return out

def _find_matching_micrograph_xml(micro_jpg_path: str) -> Optional[str]:
    """
    Given a FoilHole_XXX_Data_..._YYYYMMDD_HHMMSS.jpg micrograph,
    find the corresponding XML in the same directory (matching timestamp).
    """
    fname = os.path.basename(micro_jpg_path)
    m = MICROGRAPH_RE.match(fname)
    if not m:
        return None
    key, date_str, time_str = m.group(1), m.group(2), m.group(3)
    data_dir = os.path.dirname(micro_jpg_path)

    candidates = []
    for name in os.listdir(data_dir):
        if not name.lower().endswith(".xml"):
            continue
        if not name.lower().startswith(f"foilhole_{key.lower()}_"):
            continue
        full = os.path.join(data_dir, name)
        parts = name.rsplit("_", 2)
        if len(parts) < 3:
            continue
        dt_str = parts[-2] + "_" + parts[-1].split(".")[0]
        try:
            dt = datetime.strptime(dt_str, "%Y%m%d_%H%M%S")
        except Exception:
            continue
        candidates.append((dt, full))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]

def _parse_micrograph_meta(xml_path: str):
    """
    Parse micrograph XML to get readout area and pixel size.
    Returns (w_px, h_px, px_x_m, px_y_m) or (None, None, None, None) on failure.
    """
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return None, None, None, None

    w, h = parse_readout_area(root)
    px_x, px_y = parse_pixelsize(root)
    return w, h, px_x, px_y

def _draw_dashed_circle(draw: ImageDraw.ImageDraw, center, radius, color, width=6, dash_length=18, gap_length=12):
    """
    Draw a dashed circle by approximating with short line segments.
    """
    import math

    cx, cy = center
    circumference = 2 * math.pi * radius
    dash_cycle = dash_length + gap_length
    n_dashes = max(12, int(circumference / dash_cycle))

    for i in range(n_dashes):
        theta1 = 2 * math.pi * i / n_dashes
        theta2 = 2 * math.pi * (i + 0.5) / n_dashes  # half cycle as dash
        x1 = cx + radius * math.cos(theta1)
        y1 = cy + radius * math.sin(theta1)
        x2 = cx + radius * math.cos(theta2)
        y2 = cy + radius * math.sin(theta2)
        draw.line((x1, y1, x2, y2), fill=color, width=width)

def annotate_foilhole_template(session_dir: str, beam_diameter_stats_m: Optional[float] = None) -> Optional[Image.Image]:
    """
    Main entry point:

    - Iterate GridSquares from latest to earliest.
    - For each, find latest FoilHole JPG with matching micrograph.
    - Draw:
        * yellow circle for the hole (HoleSize from EpuSession.dm),
        * green/blue/purple circles+rectangles for acquisition/autofocus/drift areas
          based on micrograph beam diameter and camera size.
        * if BeamDiameter is missing or zero, circles are dashed at 1.1 µm diameter.
    - Add legend and optional italic note.
    - Add 1 µm scale bar via add_scale_bar_by_xml.
    - Return annotated PIL.Image or None if not possible.
    """
    session_dir = os.path.abspath(session_dir)
    gs_dirs = _find_gridsquares(session_dir)
    if not gs_dirs:
        return None

    fh_jpg_path = None
    micro_path = None
    fh_xml_path = None

    # Try GridSquares from latest to earliest
    for gs_dir in reversed(gs_dirs):
        fh_pair = _latest_foilhole_with_micrograph(gs_dir)
        if fh_pair is None:
            continue
        fh_jpg_path, micro_path = fh_pair
        fh_xml_path = _find_matching_foilhole_xml(fh_jpg_path)
        if fh_xml_path is not None and os.path.isfile(fh_xml_path):
            break
        # If XML missing, keep searching earlier GridSquares
        fh_jpg_path = None
        micro_path = None
        fh_xml_path = None

    if fh_jpg_path is None or fh_xml_path is None:
        return None

    # Load FoilHole image
    try:
        img = Image.open(fh_jpg_path).convert("RGB")
    except Exception:
        return None
    W, H = img.size
    
    # Supersampling factor for smoother circles/rectangles
    SS = 3

    # High‑res overlay for semi‑transparent fills and outlines
    overlay_hi = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    draw_overlay_hi = ImageDraw.Draw(overlay_hi)

    # Base draw for non‑supersampled elements (e.g. scale bar)
    draw = ImageDraw.Draw(img)

    # Hole size from EpuSession.dm (same logic as annotate_gridsquare)
    hole_size_m, _logs = load_session_holesize_from_dm(session_dir)

    # FoilHole center and pixel size from XML
    cx, cy, w_xml, h_xml, px_x, px_y = _parse_foilhole_center_from_xml(fh_xml_path)
    if cx is None or cy is None:
        # Fallback: center of the actual JPG
        cx, cy = W / 2.0, H / 2.0
    else:
        # If XML readout area differs from JPG size, scale coordinates
        if w_xml and h_xml and w_xml > 0 and h_xml > 0:
            scale_x = W / float(w_xml)
            scale_y = H / float(h_xml)
            cx *= scale_x
            cy *= scale_y

    # Hole radius in JPG pixels (from HoleSize in EpuSession.dm)
    hole_radius_px = _compute_radius_pixels(
        hole_size_m=hole_size_m,
        px_x=px_x,
        img_width=W,
        W_xml=w_xml,
        W_jpg=W,
    )

    # Micrograph acquisition settings (beam + camera)
    micro_settings = _parse_micrograph_settings_from_dm(session_dir)

    # Beam radius from micrograph beam diameter
    beam_diameter_m = micro_settings.get("beam_diameter_m")
    beam_default_used = False
    radius_px = None
    
    # Prioritize beam diameter from EpuSession.dm
    if (
        beam_diameter_m is not None
        and beam_diameter_m > 0
    ):
        beam_diameter_m = beam_diameter_m

    # Then use beam diameter from stats, if present
    elif (
        beam_diameter_stats_m is not None
        and beam_diameter_stats_m > 0
    ):
        beam_diameter_m = beam_diameter_stats_m

    # Or use default 1.0 um diameter if beam diameter is still unknown or zero
    else:
        beam_default_used = True
        beam_diameter_m = 1e-6

    # Compute beam size
    if (
        beam_diameter_m is not None
        and px_x is not None
        and px_x > 0        
    ):
        radius_px_acq = (beam_diameter_m / 2.0) / px_x
        if w_xml and w_xml > 0:
            scale_x = W / float(w_xml)
        else:
            scale_x = 1.0
        radius_px = radius_px_acq * scale_x
    else:
        radius_px = None

    # --- Camera rectangle size from micrograph FOV ---
    rect_w_px = None
    rect_h_px = None

    micro_xml_path = _find_matching_micrograph_xml(micro_path) if micro_path else None
    if micro_xml_path and os.path.isfile(micro_xml_path):
        mw, mh, m_px_x, m_px_y = _parse_micrograph_meta(micro_xml_path)
        # We need both micrograph and FoilHole pixel sizes to map FOV
        if mw and mh and m_px_x and px_x and m_px_x > 0 and px_x > 0:
            # Physical FOV of micrograph in meters
            fov_x_m = mw * m_px_x
            fov_y_m = mh * (m_px_y or m_px_x)

            # Convert physical FOV to FoilHole acquisition pixels
            fh_px_x = px_x
            fh_px_y = px_y or px_x
            fov_x_fh_px = fov_x_m / fh_px_x
            fov_y_fh_px = fov_y_m / fh_px_y

            # Scale FoilHole acquisition pixels to FoilHole JPG pixels
            if w_xml and w_xml > 0 and h_xml and h_xml > 0:
                scale_x = W / float(w_xml)
                scale_y = H / float(h_xml)
            else:
                scale_x = scale_y = 1.0

            rect_w_px = fov_x_fh_px * scale_x
            rect_h_px = fov_y_fh_px * scale_y

    # Fallback if we couldn't compute from micrograph
    if rect_w_px is None or rect_h_px is None or rect_w_px <= 0 or rect_h_px <= 0:
        rect_w_px = None
        rect_h_px = None

    # Base draw for non‑alpha elements (e.g. hole circle, rectangles)
    draw = ImageDraw.Draw(img)

    # Template areas (acquisition/autofocus/drift)
    template_px_size, af_shift, acq_shifts, drift_shift = _parse_template_areas_from_dm(session_dir)

    # Scale factors from template pixels to FoilHole JPG pixels:
    # We assume template pixels correspond to FoilHole acquisition pixels,
    # so we reuse the FoilHole XML readout scaling.
    if w_xml and h_xml and w_xml > 0 and h_xml > 0:
        scale_tx = W / float(w_xml)
        scale_ty = H / float(h_xml)
    else:
        scale_tx = scale_ty = 1.0

    def draw_area_fill(center_shift, circle_color, circle_radius_px, rect_w_px, rect_h_px, fill_alpha=64):
        """
        First pass: draw only the semi‑transparent fill for the circle.
        """
        if center_shift is None or circle_radius_px is None:
            return
        dx_px, dy_px = center_shift
        x_center = cx + dx_px * scale_tx
        y_center = cy + dy_px * scale_ty

        # Supersampled coordinates
        x_c_hi = x_center * SS
        y_c_hi = y_center * SS
        r_hi = circle_radius_px * SS

        bbox_circ_hi = [
            x_c_hi - r_hi,
            y_c_hi - r_hi,
            x_c_hi + r_hi,
            y_c_hi + r_hi,
        ]

        fill_rgba = (circle_color[0], circle_color[1], circle_color[2], fill_alpha)
        try:
            draw_overlay_hi.ellipse(bbox_circ_hi, fill=fill_rgba)
        except TypeError:
            draw_overlay_hi.ellipse(bbox_circ_hi, fill=fill_rgba)

    def draw_area_outline(center_shift, circle_color, rect_color,
                            circle_radius_px, rect_w_px, rect_h_px,
                            width=2, dashed=False):
        """
        Second pass: draw solid outlines for circle and rectangle on top of all fills.
        """
        if center_shift is None or circle_radius_px is None:
            return
        dx_px, dy_px = center_shift
        x_center = cx + dx_px * scale_tx
        y_center = cy + dy_px * scale_ty

        # Supersampled coordinates
        x_c_hi = x_center * SS
        y_c_hi = y_center * SS
        r_hi = circle_radius_px * SS
        rw_hi = (rect_w_px or 0) * SS
        rh_hi = (rect_h_px or 0) * SS

        bbox_circ_hi = [
            x_c_hi - r_hi,
            y_c_hi - r_hi,
            x_c_hi + r_hi,
            y_c_hi + r_hi,
        ]
        w_hi = max(1, width * SS)

        # Circle outline
        if dashed:
            _draw_dashed_circle(draw_overlay_hi, (x_c_hi, y_c_hi), r_hi, circle_color, width=w_hi)
        else:
            try:
                draw_overlay_hi.ellipse(bbox_circ_hi, outline=circle_color + (255,), width=w_hi)
            except TypeError:
                draw_overlay_hi.ellipse(bbox_circ_hi, outline=circle_color + (255,))

        # Rectangle outline (only if we have a rectangle size)
        if rect_w_px and rect_h_px and rect_w_px > 0 and rect_h_px > 0:
            bbox_rect_hi = [
                x_c_hi - rw_hi / 2.0,
                y_c_hi - rh_hi / 2.0,
                x_c_hi + rw_hi / 2.0,
                y_c_hi + rh_hi / 2.0,
            ]
            try:
                draw_overlay_hi.rectangle(bbox_rect_hi, outline=rect_color + (255,), width=w_hi)
            except TypeError:
                draw_overlay_hi.rectangle(bbox_rect_hi, outline=rect_color + (255,))

    dashed = beam_default_used

    # --- First pass: fills ---

    # Acquisition fills: green
    for s in acq_shifts:
        draw_area_fill(
            center_shift=s,
            circle_color=(0, 128, 0),
            circle_radius_px=radius_px,
            rect_w_px=rect_w_px,
            rect_h_px=rect_h_px,
            fill_alpha=64,
        )

    # Autofocus fill: blue
    draw_area_fill(
        center_shift=af_shift,
        circle_color=(0, 0, 255),
        circle_radius_px=radius_px,
        rect_w_px=rect_w_px,
        rect_h_px=rect_h_px,
        fill_alpha=64,
    )

    # Drift fill: purple
    draw_area_fill(
        center_shift=drift_shift,
        circle_color=(128, 0, 128),
        circle_radius_px=radius_px,
        rect_w_px=rect_w_px,
        rect_h_px=rect_h_px,
        fill_alpha=64,
    )

    # --- Second pass: outlines (yellow + areas) ---

    # Yellow hole outline (uses hole size, not beam size)
    if hole_radius_px is not None:
        draw_area_outline(
            center_shift=(0.0, 0.0),
            circle_color=(255, 255, 0),
            rect_color=(255, 255, 0),
            circle_radius_px=hole_radius_px,
            rect_w_px=None,
            rect_h_px=None,
            width=2,
            dashed=False,
        )

    # Acquisition outlines: green
    for s in acq_shifts:
        draw_area_outline(
            center_shift=s,
            circle_color=(0, 128, 0),
            rect_color=(0, 128, 0),
            circle_radius_px=radius_px,
            rect_w_px=rect_w_px,
            rect_h_px=rect_h_px,
            width=2,
            dashed=dashed,
        )

    # Autofocus outline: blue
    draw_area_outline(
        center_shift=af_shift,
        circle_color=(0, 0, 255),
        rect_color=(0, 0, 255),
        circle_radius_px=radius_px,
        rect_w_px=rect_w_px,
        rect_h_px=rect_h_px,
        width=2,
        dashed=dashed,
    )

    # Drift outline: purple
    draw_area_outline(
        center_shift=drift_shift,
        circle_color=(128, 0, 128),
        rect_color=(128, 0, 128),
        circle_radius_px=radius_px,
        rect_w_px=rect_w_px,
        rect_h_px=rect_h_px,
        width=2,
        dashed=dashed,
    )

    # Downsample and composite overlay onto base image
    overlay = overlay_hi.resize((W, H), resample=Image.LANCZOS)
    img_rgba = img.convert("RGBA")
    img_rgba.alpha_composite(overlay)
    img = img_rgba.convert("RGB")

    # Label acquisition area rectangles if there are multiple
    if rect_w_px and rect_h_px and rect_w_px > 0 and rect_h_px > 0 and len(acq_shifts) > 1:
        draw_txt = ImageDraw.Draw(img)
        label_font = pil_font(FONT_SIZES["caption"], bold=True)
        label_color = (0, 128, 0)

        for i, s in enumerate(acq_shifts, start=1):
            if s is None:
                continue
            dx_px, dy_px = s
            x_center = cx + dx_px * scale_tx
            y_center = cy + dy_px * scale_ty

            label = str(i)
            tw, th = measure_text(draw_txt, label, label_font)
            draw_txt.text(
                (x_center - tw / 2.0, y_center - th / 2.0),
                label,
                fill=label_color,
                font=label_font,
            )

    # Add 1 µm scale bar using the same helper as other FoilHole images
    img = add_scale_bar_by_xml(img, fh_jpg_path, bar_um=1.0, align="left", font_size=FONT_SIZES["scale_bar"])
    W, H = img.size

    legend_items = [
        ((0, 128, 0), "Acquisition Area(s)"),
        ((0, 0, 255), "Autofocus Area"),
        ((128, 0, 128), "Drift Measurement Area (If Present)"),
    ]
    
    swatch_size = 14
    swatch_gap = 8
    item_gap = 16
    side_margin = 10
    top_margin_legend = 6
    bottom_margin_legend = 6
    gap_above_legend = 10

    legend_font = pil_font(FONT_SIZES["caption"], bold=False)
    note_font = pil_font(FONT_SIZES["note"], italic=True)

    tmp_img = Image.new("RGB", (W, 50))
    tmp_draw = ImageDraw.Draw(tmp_img)
    legend_item_widths = []
    legend_item_height = 0
    for color, label in legend_items:
        tw, th = measure_text(tmp_draw, label, legend_font)
        legend_item_widths.append(swatch_size + swatch_gap + tw)
        legend_item_height = max(legend_item_height, max(th, swatch_size))
    legend_total_width = sum(legend_item_widths) + item_gap * (len(legend_items) - 1)
    legend_height = top_margin_legend + legend_item_height + bottom_margin_legend

    note_text = None
    if beam_default_used:
        note_text = "Beam diameter could not be automatically determined and so is displayed at 1.0 µm"

    note_height = 0
    note_width = 0
    if note_text:
        note_width, note_height = measure_text(tmp_draw, note_text, note_font)
        note_height += 4

    required_w = max(
        W,
        side_margin + legend_total_width + side_margin,
        side_margin + note_width + side_margin if note_text else W,
    )

    extra_h = gap_above_legend + legend_height + note_height + gap_above_legend
    new_img = Image.new("RGB", (required_w, H + extra_h), color=(255, 255, 255))
    new_img.paste(img, (0, 0))
    draw_leg = ImageDraw.Draw(new_img)

    legend_x_start = max(side_margin, (required_w - legend_total_width) // 2)
    y_leg_top = H + gap_above_legend + top_margin_legend
    x = legend_x_start
    for color, label in legend_items:
        draw_leg.rectangle(
            [x, y_leg_top, x + swatch_size, y_leg_top + swatch_size],
            fill=color,
            outline=(0, 0, 0),
        )
        x += swatch_size + swatch_gap
        tw, th = measure_text(draw_leg, label, legend_font)
        y_text = y_leg_top + (swatch_size - th) // 2
        draw_leg.text((x, y_text), label, fill=(0, 0, 0), font=legend_font)
        x += tw + item_gap

    if note_text:
        tw, th = measure_text(draw_leg, note_text, note_font)
        x_note = (required_w - tw) // 2
        y_note = H + gap_above_legend + legend_height + gap_above_legend
        draw_leg.text((x_note, y_note), note_text, fill=(0, 0, 0), font=note_font)

    return new_img

