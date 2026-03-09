# report_style.py
"""
Shared style configuration for reports and annotations.

- Centralizes font families and sizes.
- Centralizes shared colors.
- Provides Pillow font helpers that approximate Helvetica.
"""

import os
from PIL import ImageFont
from reportlab.lib.units import inch

# ReportLab font names (built-in)
RL_FONT_FAMILY = "Helvetica"
RL_FONT_FAMILY_BOLD = "Helvetica-Bold"
RL_FONT_FAMILY_ITALIC = "Helvetica-Oblique"

# Semantic font sizes (points)
# Note these are PIL sizes for many things and so do not show up as that size on the actual page
FONT_SIZES = {
    "title": 16,       # main report title
    "section": 14,     # "Atlas", "Template Definition", etc.
    "gs_title": 12,    # GridSquare titles
    "body": 9,         # table text
    "note": 16,         # notes
    "caption": 16,      # legends, small labels
    "scale_bar": 14,    # scale bar labels
    "defocus": 20,      # scale bar label and defocus in paired foilhole / micrograph images
    "hole_title": 8.5,   # FoilHole titles in PDF
}

# ---------- Image layout configuration ----------
# All values are in points unless otherwise noted (1 inch = 72 points).

IMAGE_LAYOUT = {
    # Atlas page(s)
    "atlas": {
        # Maximum height available for the atlas image (inside the atlas section),
        # not counting the section heading and margins.
        "max_height": 6.5 * inch,
        "frame_padding": 8,
        "after_box_gap": 0.5 * inch,
    },

    # Fallback atlas images
    "atlas_fallback": {
        "max_height": 6.5 * inch,
        "frame_padding": 8,
        "caption_gap": 4,
        "extra_gap": 0.12 * inch,
    },

    # Template Definition (FoilHole template)
    "template": {
        # Maximum height for the template image itself
        "max_image_height": 4.0 * inch,
        "frame_padding": 8,
        "after_box_gap": 0.2 * inch,
        "heading_height": 0.25 * inch,
    },

    # Main GridSquare image
    "gridsquare": {
        # Maximum height for the main GS image inside its box
        "max_image_height": 5.0 * inch,  # currently parent_max_h
        "frame_padding": 8,
        "title_gap": 8,
        "after_box_gap": 0.12 * inch,
    },

    # FoilHole / Micrograph thumbnails in child boxes
    "child": {
        "frame_padding": 6.0,
        "title_gap": 3.0,
        "row_gap": 0.20 * inch,
        "col_gap": 0.15 * inch,
        "columns": 4,
    },
    
}

# Colors reused across scripts (RGB)
COLOR_COLLECTION = (86, 180, 233)   # blue
COLOR_SCREENING  = (230, 159, 0)    # orange
COLOR_SELECTED   = (255, 255, 255)  # white (no micrograph)

# Text stroke for outlined labels on images
TEXT_STROKE = 1
LABEL_PAD_PX = 2.0

def _ttf_candidates(bold: bool = False, italic: bool = False):
    """
    Candidate TTF paths approximating Helvetica/Arial/DejaVu.
    """
    if bold and italic:
        names = [
            "/System/Library/Fonts/Supplemental/Helvetica Bold Oblique.ttf",
            "/Library/Fonts/Arial Bold Italic.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        ]
    elif bold:
        names = [
            "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    elif italic:
        names = [
            "/System/Library/Fonts/Supplemental/Helvetica Oblique.ttf",
            "/Library/Fonts/Arial Italic.ttf",
            "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ]
    else:
        names = [
            "/System/Library/Fonts/Supplemental/Helvetica.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    return names

def pil_font(size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    """
    Return a Pillow font approximating Helvetica at the given size.
    Falls back gracefully to DejaVuSans or the default bitmap font.
    """
    try:
        for p in _ttf_candidates(bold=bold, italic=italic):
            if os.path.isfile(p):
                return ImageFont.truetype(p, size)
        # Fallback by name
        if bold and italic:
            name = "DejaVuSans-BoldOblique.ttf"
        elif bold:
            name = "DejaVuSans-Bold.ttf"
        elif italic:
            name = "DejaVuSans-Oblique.ttf"
        else:
            name = "DejaVuSans.ttf"
        return ImageFont.truetype(name, size)
    except Exception:
        return ImageFont.load_default()

