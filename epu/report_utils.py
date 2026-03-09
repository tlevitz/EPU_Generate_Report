# report_utils.py
"""
Shared drawing and layout utilities for the report and annotation scripts.
"""

import os
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from epu.report_style import (
    RL_FONT_FAMILY,
    RL_FONT_FAMILY_BOLD,
    RL_FONT_FAMILY_ITALIC,
    FONT_SIZES,
    TEXT_STROKE,
)

# ---------- Generic image helpers ----------

def open_image_or_none(path: Optional[str]) -> Optional[Image.Image]:
    if not path:
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None

def measure_text(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    """
    Measure text width/height with Pillow, using textbbox when available.
    """
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        return draw.textsize(text, font=font)

def draw_bold_text(draw: ImageDraw.ImageDraw, xy, text: str, fill, font, stroke: int = TEXT_STROKE):
    """
    Bold/outlined text for readability. Uses stroke_width when available.
    Fallback draws a black outline behind the colored glyphs.
    """
    try:
        draw.text(xy, text, fill=fill, font=font,
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
        return
    except Exception:
        pass

    x, y = xy
    offsets = [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1),
               (1, 1), (-1, 1), (1, -1), (-1, -1)]
    for dx, dy in offsets:
        draw.text((x + dx, y + dy), text, fill=(0, 0, 0), font=font)
    draw.text(xy, text, fill=fill, font=font)

def draw_bold_text_centered(draw: ImageDraw.ImageDraw, center_xy, text: str, fill, font, stroke: int = TEXT_STROKE):
    """
    Draw bold/outlined text centered at (cx, cy).
    Prefers Pillow anchor='mm' with stroke_width; falls back to bbox centering.
    """
    cx, cy = center_xy
    try:
        draw.text((cx, cy), text, fill=fill, font=font,
                  stroke_width=stroke, stroke_fill=(0, 0, 0), anchor="mm")
        return
    except Exception:
        pass

    # Fallback: center using bbox
    try:
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = cx - w / 2.0 - bbox[0]
        y = cy - h / 2.0 - bbox[1]
    except Exception:
        w, h = draw.textsize(text, font=font)
        x = cx - w / 2.0
        y = cy - h / 2.0

    try:
        draw.text((x, y), text, fill=fill, font=font,
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
    except Exception:
        offsets = [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1),
                   (1, 1), (-1, 1), (1, -1), (-1, -1)]
        for dx, dy in offsets:
            draw.text((x + dx, y + dy), text, fill=(0, 0, 0), font=font)
        draw.text((x, y), text, fill=fill, font=font)

# ---------- ReportLab helpers ----------

def draw_heading(c: canvas.Canvas, text: str, x: float, y: float,
                 level: str = "section",
                 page_height: Optional[float] = None,
                 margin: float = 0.5 * inch) -> float:
    """
    Draw a heading at (x, y) with appropriate font size.
    level: 'title' or 'section'.
    Returns new y.
    """
    size = FONT_SIZES["title"] if level == "title" else FONT_SIZES["section"]
    c.setFont(RL_FONT_FAMILY_BOLD, size)
    if page_height is not None and y < 0.9 * inch:
        c.showPage()
        y = page_height - margin
        c.setFont(RL_FONT_FAMILY_BOLD, size)
    c.drawString(x, y, text)
    return y - 0.25 * inch

def draw_page_number(c: canvas.Canvas, page_num: int, width: float, margin: float):
    """
    Draw page number at bottom-right.
    """
    size = FONT_SIZES["body"]
    c.setFont(RL_FONT_FAMILY, size)
    text = f"{page_num}"
    text_width = c.stringWidth(text, RL_FONT_FAMILY, size)
    x = width - text_width - margin
    y = margin / 2.0
    c.setFillColor(colors.gray)
    c.drawString(x, y, text)
    c.setFillColor(colors.black)

def draw_frame_box(c: canvas.Canvas, x: float, y_top: float, w: float, h: float):
    c.setLineWidth(1)
    c.rect(x, y_top - h, w, h)

def draw_node_box(c: canvas.Canvas, x: float, y_top: float, w: float, h: float,
                  title: str,
                  pad: float = 6,
                  title_align: str = "left",
                  font_name: str = RL_FONT_FAMILY_BOLD,
                  font_size: Optional[int] = None):
    """
    Draw a rectangular box with an optional title at the top.
    """
    if font_size is None:
        font_size = FONT_SIZES["gs_title"]
    c.setLineWidth(1)
    c.rect(x, y_top - h, w, h)
    if title:
        c.setFont(font_name, font_size)
        title_y = y_top - pad - font_size
        max_w = w - 2 * pad
        text = title
        ellipsis = "..."
        while c.stringWidth(text, font_name, font_size) > max_w and len(text) > 4:
            text = text[:-4] + ellipsis
        if title_align == "center":
            c.drawCentredString(x + w / 2.0, title_y, text)
        else:
            c.drawString(x + pad, title_y, text)

def draw_image_top_center(c: canvas.Canvas, pil_img: Optional[Image.Image],
                          box_left_x: float, content_top_y: float,
                          box_w: float, max_img_h: float, pad: float = 6.0) -> float:
    """
    Draw an image centered horizontally and top-aligned within the content area.
    Returns drawn height (0 if no image).
    """
    from reportlab.lib.utils import ImageReader

    if pil_img is None:
        return 0.0
    iw, ih = pil_img.size
    if iw <= 0 or ih <= 0:
        return 0.0
    avail_w = box_w - 2 * pad
    scale = min(avail_w / iw, max_img_h / ih)
    dw, dh = iw * scale, ih * scale
    x_img = box_left_x + pad + (avail_w - dw) / 2.0
    y_img = content_top_y - dh
    img_reader = ImageReader(pil_img)
    c.drawImage(img_reader, x_img, y_img, width=dw, height=dh,
                preserveAspectRatio=True, mask='auto')
    return dh

def draw_image_fill_width_top_center(c: canvas.Canvas, pil_img: Optional[Image.Image],
                                     box_left_x: float, content_top_y: float,
                                     box_w: float, pad: float = 6.0) -> float:
    """
    Draw an image at full available width, centered horizontally and top-aligned.
    Returns drawn height (0 if no image).
    """
    from reportlab.lib.utils import ImageReader

    if pil_img is None:
        return 0.0
    iw, ih = pil_img.size
    if iw <= 0 or ih <= 0:
        return 0.0
    avail_w = box_w - 2 * pad
    scale = avail_w / iw
    dw, dh = avail_w, ih * scale
    x_img = box_left_x + pad
    y_img = content_top_y - dh
    img_reader = ImageReader(pil_img)
    c.drawImage(img_reader, x_img, y_img, width=dw, height=dh,
                preserveAspectRatio=True, mask='auto')
    return dh