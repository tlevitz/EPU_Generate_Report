#!/usr/bin/env python3
# coding: utf-8
"""
Generate a PDF report for an EPU session (screening or collection), using the same
EPU parsing + annotation logic as the Flask web app.

Usage:
  python3 generate_report.py /path/to/session
  python3 generate_report.py /path/to/session /optional/path/to/atlas_root_or_atlas
  python3 generate_report.py /path/to/session --pixel-table /path/to/pixelsizes.txt
  python3 generate_report.py /path/to/session /optional/atlas --pixel-table /path/to/pixelsizes.txt

Outputs (written into the session folder):
  - Imaging_Summary_<session>.pdf
  - <mode>_stats_<session>.txt     (mode is screening or collection)
  - atlas_annotated_<session>.jpg  (if atlas found and annotation succeeds)

Notes:
  - Uses epu/ package in the same directory as this script.
  - Uses session_layout.py in the same directory as this script.
  - PDF layout utilities still come from local report_style.py / report_utils.py.
"""

import os
import re
import sys
import argparse
from datetime import datetime
from typing import Optional, List, Tuple

from PIL import Image

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph, Table, TableStyle, Spacer, Frame
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# ---- Local (PDF) layout helpers ----
from epu.report_style import (
    IMAGE_LAYOUT,
    RL_FONT_FAMILY,
    RL_FONT_FAMILY_BOLD,
    RL_FONT_FAMILY_ITALIC,
    FONT_SIZES as PDF_FONT_SIZES,
)
from epu.report_utils import (
    open_image_or_none,
    draw_heading,
    draw_page_number,
    draw_node_box,
    draw_frame_box,
    draw_image_fill_width_top_center,
)

# ---- Shared logic (same as app) ----
from epu.epu_stats import (
    load_calibration_table,
    process_directory_screening,
    process_directory_collection,
)

from session_layout import (
    detect_atlas_root,
    find_latest_atlas_jpg,
    find_fallback_atlas_jpgs,
    build_session_nodes,
    choose_micrographs_for_display,
)

from epu.annotate_atlas import annotate_atlas_pair
from epu.annotate_foilhole import annotate_foilhole_template
from epu.report_scale_bars import add_scale_bar_by_xml
from epu.report_style import FONT_SIZES as EPU_FONT_SIZES

# Gridsquare annotators + plasmon detection (same family as app)
import epu.annotate_gridsquare as ag
annotate_gridsquare_image_or_pair = getattr(ag, "annotate_gridsquare_image_or_pair", None)
annotate_single_gridsquare_image = getattr(ag, "annotate_single_gridsquare_image", None)
find_latest_gridsquare_support_and_nonsupport = getattr(ag, "find_latest_gridsquare_support_and_nonsupport", None)
add_plasmon_caption = getattr(ag, "add_plasmon_caption", None)


# ----------------------------
# App-matching helpers
# ----------------------------

def is_collection_session(session_dir: str) -> bool:
    """
    Same as app.py: if any '*Fractions*' file exists under Images-Disc1, treat as collection.
    """
    images_root = os.path.join(session_dir, "Images-Disc1")
    if not os.path.isdir(images_root):
        return False
    for root, _dirs, files in os.walk(images_root):
        for f in files:
            if "fractions" in f.lower():
                return True
    return False


def _format_defocus_values(val):
    """
    Expect val like [[-1.0, -1.5], [-2.0, -2.5]].
    Convert each inner list to its string form, then join with ", ".
    """
    if not isinstance(val, list):
        return val
    inner_strs = [str(inner) for inner in val]
    return ", ".join(inner_strs)


def build_summary_rows(df_all, instrument_model: Optional[str], mode: str):
    """
    Copy of app.py logic:
    - Use same column lists and order.
    - Apply Defocus Values (um) formatting.
    """
    row = df_all.iloc[0].copy()
    camera_string = str(row.get("Camera", "")).strip()

    if mode == "screening":
        if instrument_model and "TUNDRA" in instrument_model.upper() and camera_string == "Ceta-F":
            cols = [
                "Date", "Folder", "Atlas Path", "Start Time", "End Time", "Total Time (hrs)",
                "Grid Squares Screened", "Total Micrographs",
                "Average Micrographs per Grid Square",
                "Microscope", "Acceleration Voltage (kV)", "Extractor Voltage (V)",
                "Spherical Aberration (mm)", "Gun Lens", "Spot Size", "Intensity",
                "EPU Version", "C2 Aperture (um)", "Objective Aperture (um)",
                "Camera", "Image Dimensions (pixels)", "Nominal Magnification",
                "EPU Pixel Size (A/pix)", "Calibrated Pixel Size (A/pix)",
                "Calibrated Beam Diameter (um)", "Pixel and Beam Size Calibration Date",
                "Exposure Time (s)", "Approx. Total Dose (e/pix)",
                "Approx. Total Dose (e/A2)", "Approx. Dose Rate (e/pix/s)",
                "Grid Type", "Grid Geometry", "EPU Measured Hole Size (um)",
                "EPU Measured Hole Center-to-Center Distance (um)",
                "Best Guess Hole Size and Spacing (um)",
                "Number of Acquisition Areas (Shots Per Hole)",
                "AFIS", "AFIS Clustering Distance (um)",
                "Number of Fractions", "Defocus Values (um)",
            ]
        elif instrument_model and "TUNDRA" in instrument_model.upper():
            cols = [
                "Date", "Folder", "Atlas Path", "Gain Reference File", "Start Time", "End Time",
                "Total Time (hrs)", "Grid Squares Screened", "Total Micrographs",
                "Average Micrographs per Grid Square",
                "Microscope", "Acceleration Voltage (kV)", "Extractor Voltage (V)",
                "Spherical Aberration (mm)", "Gun Lens", "Spot Size", "Intensity",
                "EPU Version", "C2 Aperture (um)", "Objective Aperture (um)",
                "Camera", "Camera Mode", "Image Dimensions (pixels)", "Nominal Magnification",
                "EPU Pixel Size (A/pix)", "Calibrated Pixel Size (A/pix)",
                "Calibrated Beam Diameter (um)", "Pixel and Beam Size Calibration Date",
                "Exposure Time (s)", "Approx. Total Dose (e/pix)",
                "Approx. Total Dose (e/A2)", "Approx. Dose Rate (e/pix/s)",
                "Grid Type", "Grid Geometry", "EPU Measured Hole Size (um)",
                "EPU Measured Hole Center-to-Center Distance (um)",
                "Best Guess Hole Size and Spacing (um)",
                "Number of Acquisition Areas (Shots Per Hole)",
                "AFIS", "AFIS Clustering Distance (um)",
                "Number of Fractions", "Defocus Values (um)",
            ]
        else:
            cols = [
                "Date", "Folder", "Atlas Path", "Start Time", "End Time", "Total Time (hrs)",
                "Grid Squares Screened", "Total Micrographs",
                "Average Micrographs per Grid Square", "Gain Reference File",
                "EPU Version", "Start Time", "End Time", "Total Time (hrs)",
                "Grid Squares Collected", "Total Movies",
                "Average Movies per Grid Square", "Movies per Hour",
                "Stage Tilt (Degrees)", "Microscope",
                "Acceleration Voltage (kV)", "Extractor Voltage (V)",
                "Spherical Aberration (mm)", "Gun Lens", "Spot Size",
                "Beam Diameter (um)", "C2 Aperture (um)", "C3 Aperture (um)",
                "Objective Aperture (um)", "Energy Filter",
                "Energy Filter Slit Width (eV)", "Illumination Mode",
                "Camera", "Camera Mode", "Image Dimensions (pixels)",
                "Nominal Magnification", "EPU Pixel Size (A/pix)",
                "Calibrated Pixel Size (A/pix)", "Pixel Size Calibration Date",
                "Exposure Time (s)", "Approx. Total Dose (e/pix)",
                "Approx. Total Dose (e/A2)", "Approx. Dose Rate (e/pix/s)",
                "Grid Type", "Grid Geometry", "EPU Measured Hole Size (um)",
                "EPU Measured Hole Center-to-Center Distance (um)",
                "Best Guess Hole Size and Spacing (um)",
                "Number of Acquisition Areas (Shots Per Hole)",
                "AFIS", "AFIS Clustering Distance (um)",
                "Number of Fractions", "Defocus Values (um)",
            ]
    else:
        if instrument_model and "TUNDRA" in instrument_model.upper() and camera_string == "Ceta-F":
            cols = [
                "Date", "Folder", "Atlas Path", "Start Time", "End Time", "Total Time (hrs)",
                "Grid Squares Collected", "Total Movies",
                "Average Movies per Grid Square", "Movies per Hour",
                "Microscope", "Acceleration Voltage (kV)", "Extractor Voltage (V)",
                "Spherical Aberration (mm)", "Gun Lens", "Spot Size", "Intensity",
                "EPU Version", "C2 Aperture (um)", "Objective Aperture (um)",
                "Camera", "Image Dimensions (pixels)", "Nominal Magnification",
                "EPU Pixel Size (A/pix)", "Calibrated Pixel Size (A/pix)",
                "Beam Size (um)", "Pixel and Beam Size Calibration Date",
                "Exposure Time (s)", "Approx. Total Dose (e/pix)",
                "Approx. Total Dose (e/A2)", "Approx. Dose Rate (e/pix/s)",
                "Grid Type", "Grid Geometry", "EPU Measured Hole Size (um)",
                "EPU Measured Hole Center-to-Center Distance (um)",
                "Best Guess Hole Size and Spacing (um)",
                "Number of Acquisition Areas (Shots Per Hole)",
                "AFIS", "AFIS Clustering Distance (um)",
                "Number of Fractions", "Defocus Values (um)",
            ]
        elif instrument_model and "TUNDRA" in instrument_model.upper():
            cols = [
                "Date", "Folder", "Atlas Path", "Gain Reference File", "Start Time", "End Time",
                "Total Time (hrs)", "Grid Squares Collected", "Total Movies",
                "Average Movies per Grid Square", "Movies per Hour",
                "Microscope", "Acceleration Voltage (kV)", "Extractor Voltage (V)",
                "Spherical Aberration (mm)", "Gun Lens", "Spot Size", "Intensity",
                "EPU Version", "C2 Aperture (um)", "Objective Aperture (um)",
                "Camera", "Camera Mode", "Image Dimensions (pixels)", "Nominal Magnification",
                "EPU Pixel Size (A/pix)", "Calibrated Pixel Size (A/pix)",
                "Beam Size (um)", "Pixel and Beam Size Calibration Date",
                "Exposure Time (s)", "Approx. Total Dose (e/pix)",
                "Approx. Total Dose (e/A2)", "Approx. Dose Rate (e/pix/s)",
                "Grid Type", "Grid Geometry", "EPU Measured Hole Size (um)",
                "EPU Measured Hole Center-to-Center Distance (um)",
                "Best Guess Hole Size and Spacing (um)",
                "Number of Acquisition Areas (Shots Per Hole)",
                "AFIS", "AFIS Clustering Distance (um)",
                "Number of Fractions", "Defocus Values (um)",
            ]
        else:
            cols = [
                "Date", "Folder", "Atlas Path", "Gain Reference File",
                "EPU Version", "Start Time", "End Time", "Total Time (hrs)",
                "Grid Squares Collected", "Total Movies",
                "Average Movies per Grid Square", "Movies per Hour",
                "Stage Tilt (Degrees)", "Microscope",
                "Acceleration Voltage (kV)", "Extractor Voltage (V)",
                "Spherical Aberration (mm)", "Gun Lens", "Spot Size",
                "Beam Diameter (um)", "C2 Aperture (um)", "C3 Aperture (um)",
                "Objective Aperture (um)", "Energy Filter",
                "Energy Filter Slit Width (eV)", "Illumination Mode",
                "Camera", "Camera Mode", "Image Dimensions (pixels)",
                "Nominal Magnification", "EPU Pixel Size (A/pix)",
                "Calibrated Pixel Size (A/pix)", "Pixel Size Calibration Date",
                "Exposure Time (s)", "Approx. Total Dose (e/pix)",
                "Approx. Total Dose (e/A2)", "Approx. Dose Rate (e/pix/s)",
                "Grid Type", "Grid Geometry", "EPU Measured Hole Size (um)",
                "EPU Measured Hole Center-to-Center Distance (um)",
                "Best Guess Hole Size and Spacing (um)",
                "Number of Acquisition Areas (Shots Per Hole)",
                "AFIS", "AFIS Clustering Distance (um)",
                "Number of Fractions", "Defocus Values (um)",
            ]

    cols = [c for c in cols if c in df_all.columns]

    if "Defocus Values (um)" in row.index:
        row["Defocus Values (um)"] = _format_defocus_values(row["Defocus Values (um)"])

    return [(c, str(row[c])) for c in cols]


def build_notes(mode: str) -> List[str]:
    # Same text as app.py
    if mode == "screening":
        return [
            "These statistics are for the first image taken in the screening set. "
            "If you took images at different microscope settings, this will not be correct for all images.",
            "The dose is approximated from the first micrograph. The total dose on specimen is slightly higher.",
            "The hole size and spacing is guessed based on the measure hole size function in EPU. "
            "If you are using an uncommon hole size/spacing, it may misidentify it.",
            "Pixel size is listed both as the pixel size automatically coded in EPU as well as the experimentally-calibrated pixel size. "
            "I advise that you use the calibrated pixel size in processing.",
            "Please contact Talya if any of these numbers appear to be incorrect! The script may need updating.",
        ]
    return [
        "If you took images at different microscope settings, this will not be correct for all images.",
        "The dose is approximated from the first movie. The total dose on specimen is slightly higher.",
        "The hole size and spacing is guessed based on the measure hole size function in EPU. "
        "If you are using an uncommon hole size/spacing, it may misidentify it.",
        "Pixel size is listed both as the pixel size automatically coded in EPU as well as the experimentally-calibrated pixel size. "
        "I advise that you use the calibrated pixel size in processing.",
        "Please contact Talya if any of these numbers appear to be incorrect! The script may need updating.",
    ]


def write_stats_txt(session_dir: str, folder_name: str, mode: str, summary_rows, notes) -> str:
    out_path = os.path.join(session_dir, f"{mode}_stats_{folder_name}.txt")
    key_w = 52
    with open(out_path, "w", encoding="utf-8") as f:
        for k, v in summary_rows:
            f.write(f"{k:<{key_w}}  {v}\n")
        f.write("\nNotes:\n")
        for n in notes:
            f.write(f"- {n}\n")
    return out_path


def _beam_size_m_from_df(df_all) -> Optional[float]:
    """
    Mirror app.py's template beam sizing logic.
    """
    try:
        row = df_all.iloc[0].copy()
    except Exception:
        return None

    beam_diameter_stats_m = None
    if "Beam Size (um)" in row.index:
        beam_um_val = row["Beam Size (um)"]
        try:
            if beam_um_val is not None and beam_um_val != "Beam size not calibrated":
                beam_diameter_stats_m = float(beam_um_val) * 1e-6
        except (TypeError, ValueError):
            beam_diameter_stats_m = None
    return beam_diameter_stats_m


# ----------------------------
# App-matching GS cutoff logic (for GS image rendering)
# ----------------------------

MICROGRAPH_JPG_RE = re.compile(
    r"^FoilHole_([A-Za-z0-9]+)_Data_[^_]+_[^_]+_(\d{8})_(\d{6})\.jpg$",
    re.IGNORECASE,
)

def first_micrograph_dt_in_gridsquare_data(gs_dir: str) -> Optional[datetime]:
    data_dir = os.path.join(gs_dir, "Data")
    if not os.path.isdir(data_dir):
        return None
    earliest = None
    try:
        for name in os.listdir(data_dir):
            m = MICROGRAPH_JPG_RE.match(name)
            if not m:
                continue
            dt = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
            if earliest is None or dt < earliest:
                earliest = dt
    except Exception:
        return None
    return earliest


def compute_cutoff_dt_for_session(nodes: List[dict]) -> Optional[datetime]:
    """
    Same rule as app.py:
      - If only 1 GS: None
      - Else: cutoff is first micrograph dt in GS with index==1 (if present and has micrographs)
    """
    if not nodes or len(nodes) <= 1:
        return None
    gs1 = next((n for n in nodes if n.get("index") == 1), None)
    if not gs1:
        return None
    return first_micrograph_dt_in_gridsquare_data(gs1["gs_dir"])


# ----------------------------
# Child box rendering (multi-micrograph)
# ----------------------------

def measure_child_box_height(
    c,
    foilhole_img: Optional[Image.Image],
    micro_imgs: List[Image.Image],
    w: float,
    title_font_size: int,
    pad: float = 6.0,
) -> float:
    title_h = title_font_size + 4
    gap_t = 3
    h = pad + title_h + gap_t

    if foilhole_img is not None:
        fw, fh = foilhole_img.size
        if fw > 0 and fh > 0:
            scale = (w - 2 * pad) / fw
            h += fh * scale + 3

    for mi in micro_imgs or []:
        if mi is None:
            continue
        mw, mh = mi.size
        if mw > 0 and mh > 0:
            scale = (w - 2 * pad) / mw
            h += mh * scale + 3

    h += pad
    return h


def draw_child_box(
    c,
    x: float,
    y_top: float,
    w: float,
    title: str,
    foilhole_img: Optional[Image.Image],
    micro_imgs: List[Image.Image],
    title_font_size: int,
    pad: float = 6.0,
) -> float:
    title_h = title_font_size + 4
    gap_t = 3

    total_h = measure_child_box_height(c, foilhole_img, micro_imgs, w, title_font_size, pad)
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

    for mi in micro_imgs or []:
        if mi is None:
            continue
        dh = draw_image_fill_width_top_center(c, mi, x, content_top, w, pad=pad)
        content_top -= (dh + 3)

    return total_h


def render_fallback_atlas_images(
    c,
    fallback_imgs: List[str],
    x_left: float,
    y: float,
    width: float,
    height: float,
    margin: float,
) -> float:
    cfg = IMAGE_LAYOUT["atlas_fallback"]
    pad = cfg["frame_padding"]
    max_w = width - 2 * margin
    max_h = cfg["max_height"]
    caption_font = RL_FONT_FAMILY
    caption_size = PDF_FONT_SIZES["caption"]
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
        c.drawImage(img_reader, x_img, y_img, width=dw, height=dh, preserveAspectRatio=True, mask="auto")

        c.setFont(caption_font, caption_size)
        caption_y = y - total_h - caption_gap - caption_size
        c.drawCentredString(x_left + max_w / 2.0, caption_y, os.path.basename(img_path))

        y -= (needed_h + extra_gap)

    return y


# ----------------------------
# Main report generation
# ----------------------------

def build_report(session_dir: str, atlas_arg: Optional[str], pixel_table_path: str) -> int:
    session_dir = os.path.abspath(session_dir)
    if not os.path.isdir(session_dir):
        print(f"Error: session directory not found: {session_dir}")
        return 2

    folder_name = os.path.basename(session_dir)
    if folder_name == "EPU_Out":
        folder_name = os.path.basename(os.path.dirname(session_dir))

    if not os.path.isfile(pixel_table_path):
        print(f"Error: pixel table not found: {pixel_table_path}")
        return 2

    # --- Mode + stats (same pipeline as app) ---
    pix_dict, beamsize_dict, caldate_dict = load_calibration_table(pixel_table_path)
    mode = "collection" if is_collection_session(session_dir) else "screening"

    if mode == "screening":
        df_all, _atlas_path, instrument_model = process_directory_screening(
            session_dir, pix_dict, beamsize_dict, caldate_dict
        )
    else:
        df_all, _atlas_path, instrument_model, _cam_name = process_directory_collection(
            session_dir, pix_dict, beamsize_dict, caldate_dict
        )

    summary_rows = build_summary_rows(df_all, instrument_model, mode)
    notes = build_notes(mode)
    stats_txt_path = write_stats_txt(session_dir, folder_name, mode, summary_rows, notes)

    beam_size_m = _beam_size_m_from_df(df_all)

    # --- Atlas root detection (same as app) ---
    atlas_root, atlas_source = detect_atlas_root(session_dir, atlas_arg, summary_text="")

    # --- Annotated atlas (same preference order as app) ---
    atlas_annotated_path = None
    if atlas_root and atlas_source in ("dm_atlasid", "dm_hint", "cli"):
        try:
            atlas_img = annotate_atlas_pair(session_dir, atlas_root)
            atlas_annotated_path = os.path.join(session_dir, f"atlas_annotated_{folder_name}.jpg")
            atlas_img.save(atlas_annotated_path, format="JPEG", quality=90)
        except Exception as e:
            print(f"Warning: failed to generate annotated atlas: {e}")
            atlas_annotated_path = None

    # --- Nodes (same as app) ---
    nodes = build_session_nodes(session_dir, atlas_root)

    # --- GS cutoff dt (for GS image rendering) ---
    cutoff_dt = compute_cutoff_dt_for_session(nodes)

    # --- PDF output path ---
    pdf_name = f"Imaging_Summary_{folder_name}.pdf"
    pdf_path = os.path.join(session_dir, pdf_name)

    c = rl_canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    margin = 0.5 * inch
    x_left = margin
    y = height - margin
    page_num = 1

    # ---------------- Summary pages (paginated) ----------------
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=RL_FONT_FAMILY,
        fontSize=PDF_FONT_SIZES["body"],
        leading=PDF_FONT_SIZES["body"] * 1.2,
    )
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontName=RL_FONT_FAMILY_BOLD,
        fontSize=PDF_FONT_SIZES["title"],
        leading=PDF_FONT_SIZES["title"] * 1.2,
        spaceAfter=12,
    )
    key_style = ParagraphStyle("Key", parent=body_style, fontName=RL_FONT_FAMILY_BOLD)
    val_style = body_style

    notes_title_style = ParagraphStyle(
        "NotesTitle",
        parent=body_style,
        fontName=RL_FONT_FAMILY_BOLD,
        fontSize=PDF_FONT_SIZES["body"],
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        parent=body_style,
        leftIndent=12,
        bulletIndent=0,
        bulletFontName=RL_FONT_FAMILY,
        bulletFontSize=PDF_FONT_SIZES["body"],
    )

    table_data = [
        [Paragraph(str(key), key_style), Paragraph(str(value), val_style)]
        for key, value in summary_rows
    ]

    max_rows_first_page = 50
    max_rows_other_pages = 55

    chunks = []
    remaining = table_data
    if remaining:
        chunks.append(remaining[:max_rows_first_page])
        remaining = remaining[max_rows_first_page:]
    while remaining:
        chunks.append(remaining[:max_rows_other_pages])
        remaining = remaining[max_rows_other_pages:]

    for i, chunk in enumerate(chunks or [[]]):
        story = []
        if i == 0:
            story.append(Paragraph(f"Imaging Summary: {folder_name}", title_style))
            story.append(Spacer(1, 0.2 * inch))

        if chunk:
            table = Table(chunk, colWidths=[3.25 * inch, 4.25 * inch], hAlign="LEFT")
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

        if i == len(chunks) - 1 and notes:
            story_with_notes = list(story)
            story_with_notes.append(Spacer(1, 0.3 * inch))
            story_with_notes.append(Paragraph("Notes", notes_title_style))
            for note in notes:
                story_with_notes.append(Paragraph(note, bullet_style, bulletText="•"))

            frame = Frame(margin, margin, width - 2 * margin, height - 2 * margin, showBoundary=0)
            remaining_story = list(story_with_notes)
            frame.addFromList(remaining_story, c)
            draw_page_number(c, page_num, width, margin)
            c.showPage()
            page_num += 1

            if remaining_story:
                frame = Frame(margin, margin, width - 2 * margin, height - 2 * margin, showBoundary=0)
                frame.addFromList(remaining_story, c)
                draw_page_number(c, page_num, width, margin)
                c.showPage()
                page_num += 1
            break

        frame = Frame(margin, margin, width - 2 * margin, height - 2 * margin, showBoundary=0)
        frame.addFromList(story, c)
        draw_page_number(c, page_num, width, margin)
        c.showPage()
        page_num += 1

    # reset manual y
    y = height - margin

    # ---------------- Atlas page(s) ----------------
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
                c.drawImage(img_reader, x_img, y_img, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
                y -= (total_h + atlas_cfg["after_box_gap"])
            else:
                fallback_imgs = find_fallback_atlas_jpgs(session_dir)
                if fallback_imgs:
                    c.setFont(RL_FONT_FAMILY, PDF_FONT_SIZES["note"])
                    c.drawString(x_left, y, "Atlas not found in expected directory structure; showing detected atlas JPG(s).")
                    y -= 0.5 * inch
                    y = render_fallback_atlas_images(c, fallback_imgs, x_left, y, width, height, margin)
                else:
                    c.setFont(RL_FONT_FAMILY, PDF_FONT_SIZES["note"])
                    c.drawString(x_left, y, "No matching atlas found in session or parent folder")
                    y -= 0.5 * inch
        else:
            fallback_imgs = find_fallback_atlas_jpgs(session_dir)
            if fallback_imgs:
                c.setFont(RL_FONT_FAMILY, PDF_FONT_SIZES["note"])
                c.drawString(x_left, y, "Atlas not found in expected directory structure; showing detected atlas JPG(s).")
                y -= 0.5 * inch
                y = render_fallback_atlas_images(c, fallback_imgs, x_left, y, width, height, margin)
            else:
                c.setFont(RL_FONT_FAMILY, PDF_FONT_SIZES["note"])
                c.drawString(x_left, y, "No matching atlas found in session or parent folder")
                y -= 0.5 * inch
    else:
        fallback_imgs = find_fallback_atlas_jpgs(session_dir)
        if fallback_imgs:
            c.setFont(RL_FONT_FAMILY, PDF_FONT_SIZES["note"])
            c.drawString(x_left, y, "Atlas not found; showing detected atlas JPG(s).")
            y -= 0.3 * inch
            y = render_fallback_atlas_images(c, fallback_imgs, x_left, y, width, height, margin)
        else:
            c.setFont(RL_FONT_FAMILY, PDF_FONT_SIZES["note"])
            c.drawString(x_left, y, "No matching atlas folder or atlas images found in session folder")
            y -= 0.5 * inch

    # ---------------- Template Definition (same as app) ----------------
    template_img = None
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
        scale = min((max_w - 2 * pad) / max(iw, 1), tpl_cfg["max_image_height"] / max(ih, 1))
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
        c.drawImage(img_reader, x_img, y_img, width=dw, height=dh, preserveAspectRatio=True, mask="auto")

        y = box_top_y - total_h - tpl_cfg["after_box_gap"]

    # ---------------- GridSquares ----------------
    gs_cfg = IMAGE_LAYOUT["gridsquare"]
    child_cfg = IMAGE_LAYOUT["child"]

    parent_max_h = gs_cfg["max_image_height"]
    col_gap = child_cfg["col_gap"]
    row_gap = child_cfg["row_gap"]
    columns = child_cfg["columns"]
    avail_w = width - 2 * margin
    child_w = (avail_w - (columns - 1) * col_gap) / columns
    child_title_font_size = PDF_FONT_SIZES["hole_title"]
    pad = gs_cfg["frame_padding"]

    gs_title_font_name = RL_FONT_FAMILY_BOLD
    gs_title_font_size = PDF_FONT_SIZES["gs_title"]

    def new_page():
        nonlocal page_num, y
        draw_page_number(c, page_num, width, margin)
        c.showPage()
        page_num += 1
        y = height - margin

    for gs in nodes:
        new_page()

        if gs.get("index") is not None and gs.get("epu"):
            label = f"Grid Square {gs['index']} (EPU {gs['epu']})"
        elif gs.get("index") is not None:
            label = f"Grid Square {gs['index']}"
        else:
            label = gs.get("name", "GridSquare")

        # --- Main GS image (prefer annotated), with app-like cutoff filtering ---
        gs_img_pil = None
        main_base_path = None

        min_ts_for_this_gs = None
        if cutoff_dt is not None and gs.get("index") != 1:
            min_ts_for_this_gs = cutoff_dt

        if annotate_gridsquare_image_or_pair is not None:
            try:
                gs_img_pil = annotate_gridsquare_image_or_pair(gs["gs_dir"], min_ts=min_ts_for_this_gs)
                # base image path: use support if present else latest
                main_base_path = gs.get("support_img_path") or gs.get("latest_img_path")
            except TypeError:
                # backwards compatibility
                gs_img_pil = annotate_gridsquare_image_or_pair(gs["gs_dir"])
                main_base_path = gs.get("support_img_path") or gs.get("latest_img_path")
            except Exception:
                gs_img_pil = None

        if gs_img_pil is None and annotate_single_gridsquare_image is not None:
            try:
                try:
                    gs_img_pil = annotate_single_gridsquare_image(gs["gs_dir"], min_ts=min_ts_for_this_gs)
                except TypeError:
                    gs_img_pil = annotate_single_gridsquare_image(gs["gs_dir"])
                main_base_path = gs.get("latest_img_path")
            except Exception:
                gs_img_pil = None

        if gs_img_pil is None and gs.get("latest_img_path"):
            main_base_path = gs.get("latest_img_path")
            gs_img_pil = open_image_or_none(main_base_path)

        # --- Plasmon (same rule as app: show only if it differs from main base) ---
        plasmon_img = None
        if find_latest_gridsquare_support_and_nonsupport is not None:
            try:
                support_path, nonsupport_path = find_latest_gridsquare_support_and_nonsupport(gs["gs_dir"])
                main_base = support_path or nonsupport_path
                if nonsupport_path and os.path.isfile(nonsupport_path):
                    same_file = False
                    if main_base and os.path.isfile(main_base):
                        try:
                            same_file = (os.path.realpath(nonsupport_path) == os.path.realpath(main_base))
                        except Exception:
                            same_file = False
                    if not same_file:
                        raw_plasmon = open_image_or_none(nonsupport_path)
                        if raw_plasmon is not None and add_plasmon_caption is not None:
                            plasmon_img = add_plasmon_caption(raw_plasmon, "Energy filter plasmon image: black = empty hole")
                        else:
                            plasmon_img = raw_plasmon
            except Exception:
                plasmon_img = None

        # --- Shared scale factor for GS image + plasmon image ---
        scale = 1.0
        ref_img = gs_img_pil or plasmon_img
        if ref_img is not None:
            rw, rh = ref_img.size
            scale = min((avail_w - 2 * pad) / max(rw, 1), parent_max_h / max(rh, 1))

        main_img_h_est = 0.0
        plasmon_img_h_est = 0.0
        if gs_img_pil is not None:
            _w, _h = gs_img_pil.size
            main_img_h_est = _h * scale
        if plasmon_img is not None:
            _w, _h = plasmon_img.size
            plasmon_img_h_est = _h * scale

        title_h = gs_title_font_size
        parent_h = pad + title_h + 8 + main_img_h_est
        if plasmon_img is not None:
            parent_h += 6 + plasmon_img_h_est
        parent_h += pad

        if y - parent_h < margin:
            new_page()

        draw_node_box(
            c, x_left, y, avail_w, parent_h, label,
            font_name=gs_title_font_name, font_size=gs_title_font_size,
            pad=pad, title_align="center"
        )

        parent_top = y
        content_top = parent_top - (pad + title_h + gs_cfg["title_gap"])

        if gs_img_pil is not None:
            iw, ih = gs_img_pil.size
            dw, dh = iw * scale, ih * scale
            x_img = x_left + pad + (avail_w - 2 * pad - dw) / 2.0
            y_img = content_top - dh
            c.drawImage(ImageReader(gs_img_pil), x_img, y_img, width=dw, height=dh, preserveAspectRatio=False, mask="auto")
            content_top -= dh

        if plasmon_img is not None:
            content_top -= 6
            iw, ih = plasmon_img.size
            dw, dh = iw * scale, ih * scale
            x_img = x_left + pad + (avail_w - 2 * pad - dw) / 2.0
            y_img = content_top - dh
            c.drawImage(ImageReader(plasmon_img), x_img, y_img, width=dw, height=dh, preserveAspectRatio=False, mask="auto")
            content_top -= dh + 4

        y = (parent_top - parent_h) - gs_cfg["after_box_gap"]

        # --- Children: choose micrographs for display (max total 12, app-like) ---
        children = gs.get("children") or []
        if not children:
            continue

        keys_order = [ch["key"] for ch in children if ch.get("key")]
        micro_map = {ch["key"]: (ch.get("micrograph_img_paths") or []) for ch in children if ch.get("key")}

        chosen_micro = choose_micrographs_for_display(
            keys_order,
            micro_map,
            max_total=12,
            seed_str=f"{session_dir}:{gs.get('name','')}",
        )

        # Pre-open / annotate images
        for ch in children:
            # FoilHole image
            ch["foilhole_img"] = None
            fh_path = ch.get("foilhole_img_path")
            if fh_path and os.path.isfile(fh_path):
                fh_img = open_image_or_none(fh_path)
                if fh_img is not None:
                    fh_img = add_scale_bar_by_xml(
                        fh_img,
                        fh_path,
                        bar_um=1.0,
                        align="left",
                        font_size=EPU_FONT_SIZES["defocus"],
                    )
                ch["foilhole_img"] = fh_img

            # Chosen micrographs (stacked)
            micro_imgs: List[Image.Image] = []
            for mp in (chosen_micro.get(ch.get("key"), []) or []):
                if not mp or not os.path.isfile(mp):
                    continue
                mi = open_image_or_none(mp)
                if mi is None:
                    continue
                mi = add_scale_bar_by_xml(
                    mi,
                    mp,
                    bar_nm=50.0,
                    align="left",
                    add_defocus=True,
                    font_size=EPU_FONT_SIZES["defocus"],
                )
                micro_imgs.append(mi)
            ch["micro_imgs"] = micro_imgs

        # draw children grid
        rows = [children[i : i + columns] for i in range(0, len(children), columns)]
        child_pad = child_cfg["frame_padding"]

        for row in rows:
            row_heights = []
            for ch in row:
                row_heights.append(
                    measure_child_box_height(
                        c,
                        ch.get("foilhole_img"),
                        ch.get("micro_imgs") or [],
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
                    child_title = f"FoilHole_{ch.get('key','')}"
                draw_child_box(
                    c,
                    cx,
                    y,
                    child_w,
                    child_title,
                    ch.get("foilhole_img"),
                    ch.get("micro_imgs") or [],
                    title_font_size=child_title_font_size,
                    pad=child_pad,
                )

            y -= (row_h + row_gap)

        y -= 0.08 * inch

    draw_page_number(c, page_num, width, margin)
    c.save()

    # --- Console summary ---
    print(f"Wrote: {pdf_name}")
    print(f"Wrote: {os.path.basename(stats_txt_path)}")
    if atlas_annotated_path and os.path.isfile(atlas_annotated_path):
        print(f"Wrote: {os.path.basename(atlas_annotated_path)}")
    else:
        fallbacks = find_fallback_atlas_jpgs(session_dir)
        if fallbacks:
            print("Atlas annotation not written; used fallback atlas JPG(s) (unannotated) in PDF.")
        else:
            print("Atlas not found; atlas section may be empty or show note.")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Generate Imaging Summary PDF (screening or collection).")
    parser.add_argument("session_dir", help="Path to EPU session directory")
    parser.add_argument("atlas_arg", nargs="?", default=None, help="Optional atlas root path or name/path hint")
    parser.add_argument(
        "--pixel-table",
        default=os.path.join(os.path.dirname(__file__), "pixelsizes.txt"),
        help="Path to pixelsizes.txt (default: ./pixelsizes.txt next to generate_report.py)",
    )
    args = parser.parse_args()

    rc = build_report(args.session_dir, args.atlas_arg, args.pixel_table)
    sys.exit(rc)


if __name__ == "__main__":
    main()
