# report_scale_bars.py (only font-related parts changed)
import os
import math
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw

from epu.report_style import FONT_SIZES, pil_font

NS = {
    "fei": "http://schemas.datacontract.org/2004/07/Fei.SharedObjects",
    "arr": "http://schemas.microsoft.com/2003/10/Serialization/Arrays",
    "types": "http://schemas.datacontract.org/2004/07/Fei.Types",
    "common": "http://schemas.datacontract.org/2004/07/Fei.Common.Types",
    "draw": "http://schemas.datacontract.org/2004/07/System.Drawing",
    "media": "http://schemas.datacontract.org/2004/07/System.Windows.Media",
    "epu": "http://schemas.datacontract.org/2004/07/Applications.Epu.Persistence",
}

def _to_float(x):
    try:
        return float(x)
    except Exception:
        return None

def _ln(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else tag

def _default_font(size):
    return pil_font(size, bold=False, italic=False)

def find_xml_for_image(img_path):
    if not img_path:
        return None
    base, _ = os.path.splitext(img_path)
    xml_path = base + ".xml"
    return xml_path if os.path.isfile(xml_path) else None

def parse_px_and_readout(xml_path):
    """
    Return (px_x_m_per_px, width_raw, height_raw) from the EPU XML, or (None, None, None).
    px_x is meters-per-pixel for the raw MRC/camera image.
    width_raw/height_raw are the ReadoutArea dimensions of that raw image (raw pixels).
    """
    if not xml_path or not os.path.isfile(xml_path):
        return (None, None, None)
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return (None, None, None)

    # 1) Pixel size (namespaced path)
    px_x = None
    px_y = None
    try:
        node_x = root.find(".//fei:SpatialScale/fei:pixelSize/fei:x/fei:numericValue", NS)
        node_y = root.find(".//fei:SpatialScale/fei:pixelSize/fei:y/fei:numericValue", NS)
        px_x = _to_float(node_x.text) if node_x is not None else None
        px_y = _to_float(node_y.text) if node_y is not None else None
    except Exception:
        px_x = None
        px_y = None

    # 1b) Fallback: namespace-agnostic search for pixelSize -> x/y -> numericValue
    if px_x is None or px_y is None:
        # Find any 'pixelSize' element, then look for 'x' and 'y' children with 'numericValue'
        for elem in root.iter():
            if _ln(elem.tag).lower() == "pixelsize":
                x_val = y_val = None
                for ch in elem.iter():
                    lname = _ln(ch.tag).lower()
                    if lname == "x":
                        # find numericValue under this x
                        for gg in ch.iter():
                            if _ln(gg.tag).lower() == "numericvalue":
                                v = _to_float(gg.text)
                                if v is not None:
                                    x_val = v
                                    break
                    elif lname == "y":
                        for gg in ch.iter():
                            if _ln(gg.tag).lower() == "numericvalue":
                                v = _to_float(gg.text)
                                if v is not None:
                                    y_val = v
                                    break
                px_x = px_x if px_x is not None else x_val
                px_y = px_y if px_y is not None else y_val
                if px_x is not None and px_y is not None:
                    break

    # 2) ReadoutArea width/height (raw camera pixels)
    width_raw = None
    height_raw = None
    for elem in root.iter():
        if _ln(elem.tag).lower() == "readoutarea":
            w = h = None
            for ch in list(elem):
                lname = _ln(ch.tag).lower()
                if lname == "width":
                    try:
                        w = int(ch.text)
                    except Exception:
                        pass
                elif lname == "height":
                    try:
                        h = int(ch.text)
                    except Exception:
                        pass
            if w is not None and h is not None:
                width_raw, height_raw = w, h
                break

    return (px_x, width_raw, height_raw)

def extract_defocus_um_from_xml(xml_path):
   """
   Extract AppliedDefocus from an EPU XML file and return it in micrometers.
   AppliedDefocus is stored in meters under CustomData/KeyValueOfstringanyType.
   """
   if not xml_path or not os.path.isfile(xml_path):
       return None
   try:
       root = ET.parse(xml_path).getroot()
   except Exception:
       return None

   # Namespace-agnostic search for KeyValueOfstringanyType with Key == 'AppliedDefocus'
   for kv in root.iter():
       if _ln(kv.tag).lower() == "keyvalueofstringanytype":
           key_elem = None
           val_elem = None
           for ch in kv:
               lname = _ln(ch.tag).lower()
               if lname == "key":
                   key_elem = ch
               elif lname == "value":
                   val_elem = ch
           if key_elem is not None and val_elem is not None:
               key_text = (key_elem.text or "").strip()
               if key_text == "AppliedDefocus":
                   try:
                       val_m = float((val_elem.text or "").strip())
                       return val_m * 1e6  # meters -> micrometers
                   except Exception:
                       return None
   return None

def add_scale_bar_to_image_bottom(
    img,
    bar_px_jpg,
    label_text,
    align="left",
    side_margin_px=10,
    top_margin_px=6,
    bottom_margin_px=6,
    gap_px=6,
    thickness_px=None,
    bar_color=(0, 0, 0),
    text_color=(0, 0, 0),
    font_size=None,
    extra_text=None,
    extra_gap_px=6,
):
    if img is None:
        return img

    W, H = img.size
    if bar_px_jpg is None or bar_px_jpg <= 0 or bar_px_jpg > (W - 2 * side_margin_px):
        return img

    if thickness_px is None:
        thickness_px = max(4, int(round(H * 0.004)))
    if font_size is None:
        font_size = FONT_SIZES["scale_bar"]
    font = _default_font(font_size)

    tmp = Image.new("RGB", (W, H))
    td = ImageDraw.Draw(tmp)
    # Label size
    try:
        bbox = td.textbbox((0, 0), label_text, font=font)
        label_w = bbox[2] - bbox[0]
        label_h = bbox[3] - bbox[1]
    except Exception:
        label_w, label_h = td.textsize(label_text, font=font)

    # Extra text size (if any)
    extra_h = 0
    if extra_text:
        try:
            bbox2 = td.textbbox((0, 0), extra_text, font=font)
            extra_w = bbox2[2] - bbox2[0]
            extra_h = bbox2[3] - bbox2[1]
        except Exception:
            extra_w, extra_h = td.textsize(extra_text, font=font)
    else:
        extra_w = 0

    # Height of bottom block
    block_h = top_margin_px + thickness_px + gap_px + label_h
    if extra_text:
        block_h += extra_gap_px + extra_h
    block_h += bottom_margin_px

    out = Image.new("RGB", (W, H + block_h), color=(255, 255, 255))
    out.paste(img, (0, 0))
    draw = ImageDraw.Draw(out)

    y_bar_top = H + top_margin_px
    y_bar_bottom = y_bar_top + thickness_px
    y_label = y_bar_bottom + gap_px

    x_left = W - side_margin_px - bar_px_jpg if align == "right" else side_margin_px + 3
    x_right = x_left + bar_px_jpg

    # Bar
    draw.rectangle([x_left, y_bar_top, x_right, y_bar_bottom], fill=bar_color, outline=None)

    # Label centered over bar
    x_label = x_left + (bar_px_jpg - label_w) // 2
    draw.text((x_label, y_label), label_text, fill=text_color, font=font)

    # Extra text right-aligned, next to label
    if extra_text:
    #       y_extra = y_label + label_h + extra_gap_px
    #       x_extra = x_left
        y_extra = y_bar_top
        x_extra = W - side_margin_px - 180
        draw.text((x_extra, y_extra), extra_text, fill=text_color, font=font)

    return out

def add_scale_bar_by_xml(img, jpg_path, bar_um=None, bar_nm=None, align="left", add_defocus=False, font_size=None):
    """
    Read px_x (m/px) and ReadoutArea width (raw px) from the paired XML,
    convert the requested bar length to JPG pixels, and append a bottom scale bar.
    If add_defocus is True, also add a defocus text line (in µm) below the label.
    Returns original image when the scale cannot be determined or the bar won't fit.
    """
    if img is None or not jpg_path:
        return img

    xml_path = find_xml_for_image(jpg_path)
    if xml_path is None:
        return img

    px_x_m_per_px, width_raw, _ = parse_px_and_readout(xml_path)
    if (px_x_m_per_px is None or not math.isfinite(px_x_m_per_px) or px_x_m_per_px <= 0 or
        width_raw is None or width_raw <= 0):
        # No guessing
        return img

    # Bar length in meters
    if bar_um is not None:
        bar_length_m = float(bar_um) * 1e-6
        label_text = f"{int(bar_um) if float(bar_um).is_integer() else bar_um} µm"
    elif bar_nm is not None:
        bar_length_m = float(bar_nm) * 1e-9
        label_text = f"{int(bar_nm) if float(bar_nm).is_integer() else bar_nm} nm"
    else:
        return img

    # Convert to raw pixels, then to JPG pixels
    bar_px_raw = bar_length_m / px_x_m_per_px
    W_jpg = img.size[0]
    scale_x = W_jpg / float(width_raw)  # JPG pixels per raw pixel (horizontal)
    bar_px_jpg = int(round(bar_px_raw * scale_x))

    extra_text = None
    if add_defocus:
        defocus_um = extract_defocus_um_from_xml(xml_path)
        if defocus_um is not None:
            extra_text = f"Defocus: {defocus_um:.2f} µm"

    return add_scale_bar_to_image_bottom(
        img=img,
        bar_px_jpg=bar_px_jpg,
        label_text=label_text,
        align=align,
        extra_text=extra_text,
        font_size=font_size
    )