# annotate_atlas.py
import os
import re
import glob
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from epu.report_style import (
    COLOR_COLLECTION,
    COLOR_SCREENING,
    COLOR_SELECTED,
    FONT_SIZES,
    pil_font,
)

from epu.report_utils import draw_bold_text_centered

# Consistent label style
TEXT_STROKE = 1

def localname(tag):
    return tag.split('}')[-1] if '}' in tag else tag

def direct_child_by_localname(elem, name):
    for ch in list(elem):
        if localname(ch.tag) == name:
            return ch
    return None

def direct_child_text(elem, name):
    ch = direct_child_by_localname(elem, name)
    return ch.text.strip() if (ch is not None and ch.text is not None) else None

def find_descendant_first(elem, name):
    for e in elem.iter():
        if localname(e.tag) == name:
            return e
    return None

def atlas_id_from_epu_dm(session_dir: str):
    dm_path = os.path.join(session_dir, "EpuSession.dm")
    if not os.path.isfile(dm_path):
        return None
    try:
        root = ET.parse(dm_path).getroot()
    except Exception:
        return None
    for elem in root.iter():
        if localname(elem.tag).lower() == "atlasid":
            txt = (elem.text or "").strip()
            return txt if txt else None
    return None

def sample_from_atlas_id(atlas_id_text: str):
    if not atlas_id_text:
        return None
    parts = [p for p in re.split(r"[\\/]+", atlas_id_text.strip().strip('"').strip("'")) if p]
    for p in parts:
        if re.match(r"(?i)^sample\d+$", p):
            return p
    return None

def parse_atlas_nodes_precise(atlas_dm_path):
    """
    Parse Atlas.dm and extract atlas node metadata.
    """
    tree = ET.parse(atlas_dm_path)
    root = tree.getroot()
    nodes, pairs = {}, []

    for container in root.iter():
        pos = find_descendant_first(container, 'PositionOnTheAtlas')
        if pos is None:
            continue

        center = direct_child_by_localname(pos, 'Center')
        if center is None:
            continue
        ax_text = direct_child_text(center, 'x')
        ay_text = direct_child_text(center, 'y')
        rot_text = direct_child_text(pos, 'Rotation')
        shear_text = direct_child_text(pos, 'Shear')
        if ax_text is None or ay_text is None:
            continue

        # Stage position (aligned or raw)
        stage = find_descendant_first(container, 'StagePosition')
        if stage is None:
            stage = find_descendant_first(container, 'AlignedStagePosition')
        if stage is None:
            continue
        sx_text = direct_child_text(stage, 'X')
        sy_text = direct_child_text(stage, 'Y')
        if sx_text is None or sy_text is None:
            continue

        # Node ID
        node_id = None
        key_el = find_descendant_first(container, 'key')
        if key_el is not None and key_el.text:
            try:
                node_id = int(key_el.text.strip())
            except ValueError:
                node_id = None
        if node_id is None:
            id_el = find_descendant_first(container, 'Id')
            if id_el is not None and id_el.text:
                try:
                    node_id = int(id_el.text.strip())
                except ValueError:
                    node_id = None

        # Convert basics
        try:
            sx = float(sx_text)
            sy = float(sy_text)
            ax = float(ax_text)
            ay = float(ay_text)
        except ValueError:
            continue

        rot = None
        if rot_text is not None:
            try:
                rot = float(rot_text)
            except ValueError:
                rot = None

        shear = None
        if shear_text is not None:
            try:
                shear = float(shear_text)
            except ValueError:
                shear = None

        # Read atlas-pixel Size
        size_el = find_descendant_first(pos, 'Size')
        sw = sh = None
        if size_el is not None:
            sw_text = direct_child_text(size_el, 'width')
            sh_text = direct_child_text(size_el, 'height')
            try:
                sw = float(sw_text) if sw_text is not None else None
                sh = float(sh_text) if sh_text is not None else None
            except ValueError:
                sw = sh = None

        # Physical size (meters)
        phys_el = find_descendant_first(pos, 'Physical')
        pw = ph = None
        if phys_el is not None:
            pw_text = direct_child_text(phys_el, 'width')
            ph_text = direct_child_text(phys_el, 'height')
            try:
                pw = float(pw_text) if pw_text is not None else None
                ph = float(ph_text) if ph_text is not None else None
            except ValueError:
                pw = ph = None

        pairs.append((sx, sy, ax, ay))
        if node_id is not None:
            nodes[node_id] = {
                'stage_x': sx, 'stage_y': sy,
                'atlas_x': ax, 'atlas_y': ay,
                'rotation': rot, 'shear': shear,
                'size_w_px': sw, 'size_h_px': sh,
                'phys_w_m': pw, 'phys_h_m': ph,
            }

    if not pairs:
        raise RuntimeError('No pairs parsed in Atlas.dm')
    return nodes, pairs

def fit_stage_to_atlas_affine(pairs):
    S = np.asarray([[sx, sy, 1.0] for sx, sy, _, _ in pairs], dtype=np.float64)
    A = np.asarray([[ax, ay] for _, _, ax, ay in pairs], dtype=np.float64)
    M, _, _, _ = np.linalg.lstsq(S, A, rcond=None)

    def mapper(sx, sy):
        return tuple((np.array([sx, sy, 1.0]) @ M).tolist())

    return mapper, M

def parse_gridsquare_xml(xml_path):
    """
    Namespace-agnostic GridSquare XML parsing for stage X,Y (meters).
    """
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return {'stage_x': None, 'stage_y': None}
    sx = sy = None
    stage_el = find_descendant_first(root, 'stage')
    if stage_el is not None:
        pos_el = find_descendant_first(stage_el, 'Position')
        if pos_el is not None:
            sx_txt = direct_child_text(pos_el, 'X') or direct_child_text(pos_el, '_x')
            sy_txt = direct_child_text(pos_el, 'Y') or direct_child_text(pos_el, '_y')
            if sx_txt and sy_txt:
                sx, sy = sx_txt, sy_txt
    if sx is None or sy is None:
        pos_any = find_descendant_first(root, 'Position')
        if pos_any is not None:
            sx_txt = direct_child_text(pos_any, 'X') or direct_child_text(pos_any, '_x')
            sy_txt = direct_child_text(pos_any, 'Y') or direct_child_text(pos_any, '_y')
            if sx_txt and sy_txt:
                sx, sy = sx_txt, sy_txt
    return {'stage_x': float(sx) if sx is not None else None,
            'stage_y': float(sy) if sy is not None else None}

def map_grids_to_atlas(atlas_root, screening_root, check_node_center=True, fill_rotation='median'):
    # Prefer the sample referenced by EpuSession.dm <AtlasId>
    preferred_sample = sample_from_atlas_id(atlas_id_from_epu_dm(screening_root))

    # Build candidate Atlas.dm paths in priority order
    candidates = []
    if preferred_sample:
        candidates.append((preferred_sample, os.path.join(atlas_root, preferred_sample, "Atlas", "Atlas.dm")))

    candidates.append(("Sample0", os.path.join(atlas_root, "Sample0", "Atlas", "Atlas.dm")))

    # try any other SampleN
    try:
        for s in sorted(os.listdir(atlas_root)):
            if not re.match(r"(?i)^sample\d+$", s):
                continue
            if s in (preferred_sample, "Sample0"):
                continue
            candidates.append((s, os.path.join(atlas_root, s, "Atlas", "Atlas.dm")))
    except Exception:
        pass

    # last resort: atlas_root/Atlas/Atlas.dm (some datasets have this)
    candidates.append(("Atlas", os.path.join(atlas_root, "Atlas", "Atlas.dm")))

    nodes = pairs = None
    used_sample = None
    used_dm_path = None
    last_err = None

    for sample_name, atlas_dm_path in candidates:
        if not os.path.isfile(atlas_dm_path):
            continue
        try:
            nodes, pairs = parse_atlas_nodes_precise(atlas_dm_path)
            used_sample = sample_name
            used_dm_path = atlas_dm_path
            break
        except Exception as e:
            last_err = e
            continue

    if nodes is None or pairs is None:
        raise RuntimeError(f"Could not parse any Atlas.dm under {atlas_root}. Last error: {last_err}")

    stage_to_atlas, M = fit_stage_to_atlas_affine(pairs)

    discs = sorted([d for d in glob.glob(os.path.join(screening_root, 'Images-Disc*')) if os.path.isdir(d)]) or [screening_root]
    rows = []
    for disc in discs:
        grid_dirs = sorted([d for d in glob.glob(os.path.join(disc, 'GridSquare*')) if os.path.isdir(d)])
        for gdir in grid_dirs:
            xmls = sorted(glob.glob(os.path.join(gdir, 'GridSquare*.xml')) or glob.glob(os.path.join(gdir, '*.xml')))
            if not xmls:
                continue
            xml_path = xmls[-1]
            info = parse_gridsquare_xml(xml_path)
            sx, sy = info.get('stage_x'), info.get('stage_y')
            if sx is None or sy is None:
                continue

            ax, ay = stage_to_atlas(sx, sy)
            m = re.search(r'GridSquare[_\s-]*([0-9]+)', os.path.basename(gdir), flags=re.IGNORECASE)
            grid_id = int(m.group(1)) if m else None

            rot = shear = atlas_w = atlas_h = phys_w = phys_h = atlas_cx = atlas_cy = None
            if (grid_id is not None) and (grid_id in nodes):
                node = nodes[grid_id]
                rot = node.get('rotation', None)
                shear = node.get('shear', None)
                atlas_w = node.get('size_w_px', None)
                atlas_h = node.get('size_h_px', None)
                phys_w = node.get('phys_w_m', None)
                phys_h = node.get('phys_h_m', None)
                atlas_cx = node.get('atlas_x', None)
                atlas_cy = node.get('atlas_y', None)

            row = {
                'grid_id': grid_id, 'folder': gdir, 'xml_path': xml_path,
                'stage_x_m': sx, 'stage_y_m': sy, 'atlas_x_px': ax, 'atlas_y_px': ay,
                'rotation_rad': rot, 'shear': shear,
                'atlas_box_w_px': atlas_w, 'atlas_box_h_px': atlas_h,
                'phys_box_w_m': phys_w, 'phys_box_h_m': phys_h,
            }
            if check_node_center and atlas_cx is not None and atlas_cy is not None:
                row['atlas_x_center_px'] = atlas_cx
                row['atlas_y_center_px'] = atlas_cy
                row['atlas_center_delta_px'] = float(np.hypot(ax - atlas_cx, ay - atlas_cy))
            rows.append(row)

    df = pd.DataFrame(rows)

    if not df.empty and 'rotation_rad' in df.columns:
        df['rotation_rad'] = pd.to_numeric(df['rotation_rad'], errors='coerce')
        if fill_rotation == 'median' and df['rotation_rad'].notna().any():
            try:
                fallback = float(df['rotation_rad'].dropna().median())
            except Exception:
                fallback = 0.0
            df['rotation_rad'] = df['rotation_rad'].fillna(fallback)
        else:
            df['rotation_rad'] = df['rotation_rad'].fillna(0.0)
        df['rotation_deg'] = np.degrees(df['rotation_rad'].values.astype(float))

    if 'shear' in df.columns:
        df['shear'] = pd.to_numeric(df['shear'], errors='coerce').fillna(0.0)

    return df, M, used_sample, used_dm_path

def square_type_and_mtime(folder):
    """
    Returns (color_rgb_tuple, square_type, most_recent_mtime or None)
    square_type: 'collection' | 'screening' | 'none'
    color: blue for collection, orange for screening, white otherwise
    """
    data_dir = os.path.join(folder, 'Data')
    if not os.path.isdir(data_dir):
        return COLOR_SELECTED, 'none', None

    frac_files = []
    frac_files += glob.glob(os.path.join(data_dir, 'FoilHole*Fractions.mrc'))
    frac_files += glob.glob(os.path.join(data_dir, 'FoilHole*Fractions.tif'))
    frac_files += glob.glob(os.path.join(data_dir, 'FoilHole*Fractions.tiff'))

    fh_files = []
    fh_files += glob.glob(os.path.join(data_dir, 'FoilHole*.mrc'))
    fh_files += glob.glob(os.path.join(data_dir, 'FoilHole*.tif'))
    fh_files += glob.glob(os.path.join(data_dir, 'FoilHole*.tiff'))

    fh_non_frac = [p for p in fh_files if 'Fractions' not in os.path.basename(p)]

    if len(frac_files) > 0:
        try:
            mtime = min(os.path.getmtime(p) for p in fh_non_frac) if fh_non_frac else None
        except Exception:
            mtime = None
        return COLOR_COLLECTION, 'collection', mtime

    if len(fh_non_frac) > 0:
        try:
            mtime = min(os.path.getmtime(p) for p in fh_non_frac)
        except Exception:
            mtime = None
        return COLOR_SCREENING, 'screening', mtime

    return COLOR_SELECTED, 'none', None

def annotate_atlas_pair(screening_root, atlas_root):
    """
    Render two atlases side-by-side with grid-square overlays and labels.
    Includes a 200 µm scale bar in the legend area.
    Returns a PIL.Image (RGB).
    """
    SHEAR_THRESHOLD = 0.02
    ASPECT_TOL = 0.15
    SS = 3
    SCALE_BAR_METERS = 200e-6  # 200 µm

    df, M, used_sample, used_dm_path = map_grids_to_atlas(atlas_root, screening_root, check_node_center=True, fill_rotation='median')

    # Classify and number
    colors, types, mtimes = [], [], []
    for _, row in df.iterrows():
        color, t, mt = square_type_and_mtime(row['folder'])
        types.append(t)
        mtimes.append(mt)
        colors.append(color)
    df['square_type'] = types
    df['color'] = colors
    df['square_first_mtime'] = mtimes
    df = df.sort_values(by='square_first_mtime', ascending=True, na_position='last')
    df['grid_square_index'] = range(1, len(df) + 1)

    # Load base atlas
    sample_for_jpg = used_sample if used_sample and re.match(r"(?i)^sample\d+$", used_sample) else "Sample0"
    atlas_jpgs = glob.glob(os.path.join(atlas_root, sample_for_jpg, 'Atlas', 'Atlas_*.jpg'))

    if not atlas_jpgs:
        raise FileNotFoundError(
            f'No atlas JPEGs found at {os.path.join(atlas_root, "Sample0", "Atlas", "Atlas_*.jpg")}'
        )
    atlas_jpgs.sort()
    atlas_img_path = atlas_jpgs[-1]
    base = Image.open(atlas_img_path).convert('RGB')
    w_img, h_img = base.size

    # High-res overlays
    hi_size = (w_img * SS, h_img * SS)
    left_overlay_hi = Image.new('RGBA', hi_size, (0, 0, 0, 0))
    right_overlay_hi = Image.new('RGBA', hi_size, (0, 0, 0, 0))
    draw_left_hi = ImageDraw.Draw(left_overlay_hi)
    draw_right_hi = ImageDraw.Draw(right_overlay_hi)

    stroke = 2
    hi_stroke = max(1, stroke * SS)

    ATLAS_FULL_RES_PX = 4005.0
    scale_x = w_img / ATLAS_FULL_RES_PX
    scale_y = h_img / ATLAS_FULL_RES_PX
    hi_scale_x = scale_x * SS
    hi_scale_y = scale_y * SS

    labels = []

    for _, row in df.iterrows():
        ax = row.get('atlas_x_center_px', None)
        ay = row.get('atlas_y_center_px', None)
        if ax is None or ay is None:
            ax = row.get('atlas_x_px', None)
            ay = row.get('atlas_y_px', None)

        theta = row.get('rotation_rad', None)
        raw_shear = row.get('shear', None)
        color = row.get('color', (255, 255, 255))
        w_px = row.get('atlas_box_w_px', None)
        h_px = row.get('atlas_box_h_px', None)
        grid_index = row.get('grid_square_index', None)

        if (ax is None or ay is None or theta is None or w_px is None or h_px is None):
            continue
        try:
            ax = float(ax)
            ay = float(ay)
            theta = float(theta)
            w_px = float(w_px)
            h_px = float(h_px)
            raw_shear = float(raw_shear) if raw_shear is not None and np.isfinite(raw_shear) else 0.0
        except Exception:
            continue
        if not (np.isfinite(ax) and np.isfinite(ay) and np.isfinite(theta) and np.isfinite(w_px) and np.isfinite(h_px)):
            continue

        aspect_out = False
        if w_px > 0 and h_px > 0:
            ratio = w_px / h_px
            aspect_out = (abs(ratio - 1.0) > ASPECT_TOL)

        out_of_tol = (abs(raw_shear) > SHEAR_THRESHOLD) or aspect_out

        if out_of_tol:
            shear_val = 0.0
            side = (w_px + h_px) / 2.0
            w_px = side
            h_px = side
        else:
            shear_val = float(np.clip(raw_shear, -SHEAR_THRESHOLD, SHEAR_THRESHOLD))

        half_w = w_px / 2.0 + 20
        half_h = h_px / 2.0 + 20
        rect_atlas = np.array(
            [
                [-half_w, -half_h],
                [half_w, -half_h],
                [half_w, half_h],
                [-half_w, half_h],
            ],
            dtype=float,
        )

        ct = np.cos(theta)
        st = np.sin(theta)
        R = np.array([[ct, -st], [st, ct]], dtype=float)
        if shear_val != 0.0:
            S = np.array([[1.0, shear_val], [0.0, 1.0]], dtype=float)
            M_local = S.T @ R.T
        else:
            M_local = R.T

        corners_atlas = (rect_atlas @ M_local) + np.array([ax, ay], dtype=float)

        corners_hi = np.column_stack(
            [
                corners_atlas[:, 0] * hi_scale_x,
                corners_atlas[:, 1] * hi_scale_y,
            ]
        )

        pts_hi = [(int(round(x)), int(round(y))) for x, y in corners_hi]

        drew_polygon = False
        try:
            draw_left_hi.polygon(pts_hi, outline=tuple(color) + (255,), width=hi_stroke)
            draw_right_hi.polygon(pts_hi, outline=tuple(color) + (255,), width=hi_stroke)
            drew_polygon = True
        except TypeError:
            drew_polygon = False

        if not drew_polygon:
            for i in range(len(pts_hi)):
                p1 = pts_hi[i]
                p2 = pts_hi[(i + 1) % len(pts_hi)]
                draw_left_hi.line([p1, p2], fill=tuple(color) + (255,), width=hi_stroke)
                draw_right_hi.line([p1, p2], fill=tuple(color) + (255,), width=hi_stroke)

        if grid_index is not None:
            cx = ax * scale_x
            cy = ay * scale_y
            labels.append((cx, cy, str(int(grid_index)), color))

    left_overlay = left_overlay_hi.resize((w_img, h_img), resample=Image.LANCZOS)
    right_overlay = right_overlay_hi.resize((w_img, h_img), resample=Image.LANCZOS)

    left_rgba = base.convert('RGBA')
    right_rgba = base.convert('RGBA')
    left_rgba.alpha_composite(left_overlay)
    right_rgba.alpha_composite(right_overlay)

    left = left_rgba.convert('RGB')
    right = right_rgba.convert('RGB')

    # Fonts
    font_labels = pil_font(FONT_SIZES["caption"]-0.5, bold=True)
    legend_font = pil_font(FONT_SIZES["caption"], bold=False)

    draw_right = ImageDraw.Draw(right)
    for cx, cy, text, color in labels:
        draw_bold_text_centered(draw_right, (cx, cy), text, fill=tuple(color), font=font_labels, stroke=2)

    legend_items = [
        (COLOR_COLLECTION, "Collection Squares"),
        (COLOR_SCREENING, "Screening Squares"),
        (COLOR_SELECTED, "Selected, not Imaged"),
    ]
    swatch_size = 14
    swatch_gap = 8
    item_gap = 16
    side_margin = 10
    top_margin_legend = 6
    bottom_margin_legend = 10

    GAP_PX = 40

    final_w_preview = w_img * 2 + GAP_PX
    tmp_img = Image.new('RGB', (final_w_preview, h_img))
    tmp_draw = ImageDraw.Draw(tmp_img)
    legend_item_widths = []
    legend_item_height = 0
    for color, label in legend_items:
        try:
            lb = tmp_draw.textbbox((0, 0), label, font=legend_font)
            tw = lb[2] - lb[0]
            th = lb[3] - lb[1]
        except Exception:
            tw, th = tmp_draw.textsize(label, font=legend_font)
        legend_item_widths.append(swatch_size + swatch_gap + tw)
        legend_item_height = max(legend_item_height, max(th, swatch_size))
    legend_total_width = sum(legend_item_widths) + item_gap * (len(legend_items) - 1)
    legend_height = top_margin_legend + legend_item_height + bottom_margin_legend

    # Compute scale bar length from stage→image distances
    img_pts = []
    stage_pts = []
    for _, row in df.iterrows():
        ax = row.get('atlas_x_center_px', None)
        ay = row.get('atlas_y_center_px', None)
        if ax is None or ay is None:
            ax = row.get('atlas_x_px', None)
            ay = row.get('atlas_y_px', None)
        sx = row.get('stage_x_m', None)
        sy = row.get('stage_y_m', None)
        if ax is None or ay is None or sx is None or sy is None:
            continue
        try:
            ax = float(ax)
            ay = float(ay)
            sx = float(sx)
            sy = float(sy)
        except Exception:
            continue
        if not (np.isfinite(ax) and np.isfinite(ay) and np.isfinite(sx) and np.isfinite(sy)):
            continue
        img_pts.append((ax * scale_x, ay * scale_y))
        stage_pts.append((sx, sy))

    scale_bar_px = None
    scale_bar_label = "200 μm"
    scale_bar_height_px = 6
    reserved_left = side_margin

    px_per_m_ratios = []
    n_pts = len(img_pts)
    if n_pts >= 2:
        pairs = [(i, j) for i in range(n_pts) for j in range(i + 1, n_pts)]
        for i, j in pairs:
            sx1, sy1 = stage_pts[i]
            sx2, sy2 = stage_pts[j]
            ax1, ay1 = img_pts[i]
            ax2, ay2 = img_pts[j]
            d_m = np.hypot(sx2 - sx1, sy2 - sy1)
            d_px = np.hypot(ax2 - ax1, ay2 - ay1)
            if d_m > 1e-9 and np.isfinite(d_px):
                px_per_m_ratios.append(d_px / d_m)

        if px_per_m_ratios:
            px_per_m = float(np.median(px_per_m_ratios))
            scale_bar_px = int(round(SCALE_BAR_METERS * px_per_m))

    if scale_bar_px is None:
        try:
            J = np.array(
                [
                    [M[0, 0] * scale_x, M[1, 0] * scale_x],
                    [M[0, 1] * scale_y, M[1, 1] * scale_y],
                ],
                dtype=float,
            )
            px_per_m_iso = float(np.sqrt((np.sum(J[:, 0] ** 2) + np.sum(J[:, 1] ** 2)) / 2.0))
            scale_bar_px = int(round(SCALE_BAR_METERS * px_per_m_iso))
        except Exception:
            scale_bar_px = None

    if scale_bar_px is not None:
        try:
            lb = tmp_draw.textbbox((0, 0), scale_bar_label, font=legend_font)
            label_w = lb[2] - lb[0]
        except Exception:
            label_w, _ = tmp_draw.textsize(scale_bar_label, font=legend_font)
        reserved_left = side_margin + max(scale_bar_px, label_w) + 20

    final_w = w_img * 2 + GAP_PX
    final_h = h_img + legend_height
    final_img = Image.new('RGB', (final_w, final_h), color=(255, 255, 255))
    final_draw = ImageDraw.Draw(final_img)

    final_img.paste(left, (0, 0))
    final_img.paste(right, (w_img + GAP_PX, 0))

    legend_x_start = max(reserved_left, (final_w - legend_total_width) // 2)
    y_leg_top = h_img + top_margin_legend
    x = legend_x_start
    for color, label in legend_items:
        final_draw.rectangle(
            [x, y_leg_top, x + swatch_size, y_leg_top + swatch_size],
            fill=color,
            outline=(0, 0, 0),
        )
        x += swatch_size + swatch_gap
        try:
            lb = final_draw.textbbox((0, 0), label, font=legend_font)
            tw = lb[2] - lb[0]
            th = lb[3] - lb[1]
            ty = y_leg_top + (swatch_size // 2) - (th // 2)
        except Exception:
            tw, th = final_draw.textsize(label, font=legend_font)
            ty = y_leg_top + (swatch_size // 2) - 6
        final_draw.text((x, ty), label, fill=(0, 0, 0), font=legend_font)
        x += tw + item_gap

    if scale_bar_px is not None:
        x_bar_left = side_margin
        y_bar_bottom = y_leg_top + 5
        final_draw.rectangle(
            [x_bar_left, y_bar_bottom - scale_bar_height_px, x_bar_left + scale_bar_px, y_bar_bottom],
            fill=(0, 0, 0),
            outline=None,
        )
        cx = x_bar_left + scale_bar_px / 2.0
        label_y = y_bar_bottom + 12
        draw_bold_text_centered(final_draw, (cx, label_y), scale_bar_label, fill=(0, 0, 0), font=legend_font, stroke=0)

    return final_img