#!/usr/bin/env python
# coding: utf-8

import os
import argparse
import fnmatch
from datetime import datetime
from pathlib import Path

import pandas as pd
import xml.etree.ElementTree as ET

# ----

## FILL IN THIS INFORMATION WITH YOUR SCOPES AND WINDOWS ROOT ##

MICROSCOPE_INFO = {
    "TUNDRA-XXX": ("DFCI Tundra", 1.6),
    "TITANXXX": ("HMS Krios2", 2.7),
    "TITANXXX": ("HMS Krios1", 2.7),
}

windows_root = "Z:\\"

# ----------------------------
# Filesystem helpers
# ----------------------------

def get_modification_time(path):
    p = Path(path)
    return datetime.fromtimestamp(p.stat().st_mtime)

def get_earliest_file_modification_time(base_dir, exclude_dirs):
    earliest_time = None
    for entry in os.scandir(base_dir):
        if entry.is_dir() and entry.name in exclude_dirs:
            continue
        if entry.is_file():
            t = datetime.fromtimestamp(entry.stat().st_mtime)
            if earliest_time is None or t < earliest_time:
                earliest_time = t
        elif entry.is_dir():
            sub = get_earliest_file_modification_time(entry.path, exclude_dirs)
            if sub and (earliest_time is None or sub < earliest_time):
                earliest_time = sub
    return earliest_time

def get_latest_folder_modification_time(base_dir):
    latest_time = None
    for entry in os.scandir(base_dir):
        if entry.is_dir():
            t = datetime.fromtimestamp(entry.stat().st_mtime)
            if latest_time is None or t > latest_time:
                latest_time = t
            sub = get_latest_folder_modification_time(entry.path)
            if sub and (latest_time is None or sub > latest_time):
                latest_time = sub
    return latest_time

def count_folders_with_data_and_pattern(parent_folder, pattern):
    """
    Count grid squares (subfolders of Images-Disc1) that have a Data subfolder
    containing at least one file matching pattern (e.g. 'FoilHole*.mrc', '*Fractions.mrc').
    """
    images_disc1_path = os.path.join(parent_folder, 'Images-Disc1')
    count = 0

    if not os.path.exists(images_disc1_path):
        return 0

    for folder_name in os.listdir(images_disc1_path):
        folder_path = os.path.join(images_disc1_path, folder_name)
        if not os.path.isdir(folder_path):
            continue
        data_folder_path = os.path.join(folder_path, 'Data')
        if not (os.path.exists(data_folder_path) and os.path.isdir(data_folder_path)):
            continue
        for file_name in os.listdir(data_folder_path):
            if fnmatch.fnmatch(file_name, pattern):
                count += 1
                break

    return count

def count_files_with_pattern(base_dir, pattern):
    total = 0
    for root, dirs, files in os.walk(base_dir):
        for f in files:
            if fnmatch.fnmatch(f, pattern):
                total += 1
    return total

# ----------------------------
# EpuSession namespaces
# ----------------------------

def find_first_by_localname(root, localname):
    """
    Search the tree for the first element whose tag's local name matches `localname`,
    ignoring namespaces.
    """
    for elem in root.iter():
        # elem.tag can be '{uri}name' or 'name'
        if elem.tag.endswith('}' + localname) or elem.tag == localname:
            return elem
    return None

def find_first_child_by_localname(elem, localname):
    """
    Return the first direct child of `elem` whose tag's local name matches `localname`,
    ignoring namespaces.
    """
    if elem is None:
        return None
    for child in list(elem):
        tag = child.tag
        if tag.endswith('}' + localname) or tag == localname:
            return child
    return None

NS = {
    "d": "http://schemas.datacontract.org/2004/07/Applications.Epu.Persistence",
    "fei": "http://schemas.datacontract.org/2004/07/Fei.Applications.Common.Types",
    "a": "http://schemas.microsoft.com/2003/10/Serialization/Arrays",
    "b": "http://schemas.datacontract.org/2004/07/System.Collections.Generic",
    "fs": "http://schemas.datacontract.org/2004/07/Fei.SharedObjects",
    "ft": "http://schemas.datacontract.org/2004/07/Fei.Types",
}

def find_text(root, path, default=None, ns=None):
    if ns is None:
        ns = NS
    elem = root.find(path, ns)
    return elem.text if elem is not None and elem.text is not None else default

def find_all(root, path, ns=None):
    if ns is None:
        ns = NS
    return root.findall(path, ns)

# ----------------------------
# FoilHole XML parsing (no namespaces)
# ----------------------------

def find_first_foilhole_xml_in_data(base_folder):
    """
    Find the first FoilHole*.xml under Images-Disc1/GridSquare*/Data.
    This matches the original behavior and avoids FoilHoles/Metadata XMLs.
    """
    images_disc1 = os.path.join(base_folder, "Images-Disc1")
    if not os.path.isdir(images_disc1):
        return None

    for grid in sorted(os.listdir(images_disc1)):
        grid_path = os.path.join(images_disc1, grid)
        if not os.path.isdir(grid_path):
            continue
        data_path = os.path.join(grid_path, "Data")
        if not os.path.isdir(data_path):
            continue
        for f in sorted(os.listdir(data_path)):
            if f.endswith(".xml") and "FoilHole" in f:
                return os.path.join(data_path, f)
    return None

def find_matching_foilhole_and_fractions_in_data(base_folder, fractions_ext):
    """
    For collection mode: find a FoilHole XML in Images-Disc1/GridSquare*/Data
    that has a matching fractions file in the same Data folder.
    """
    images_disc1 = os.path.join(base_folder, "Images-Disc1")
    if not os.path.isdir(images_disc1):
        return None, None

    for grid in sorted(os.listdir(images_disc1)):
        grid_path = os.path.join(images_disc1, grid)
        if not os.path.isdir(grid_path):
            continue
        data_path = os.path.join(grid_path, "Data")
        if not os.path.isdir(data_path):
            continue
        for f in sorted(os.listdir(data_path)):
            if f.endswith(".xml") and "FoilHole" in f and "Fractions" not in f:
                xml_file = os.path.join(data_path, f)
                base_name = os.path.splitext(f)[0]
                fractions_file = os.path.join(data_path, f"{base_name}_Fractions.{fractions_ext}")
                if os.path.exists(fractions_file):
                    return xml_file, fractions_file
    return None, None

# For FoilHole CustomData
FH_CUSTOM_NS = {
    "a": "http://schemas.datacontract.org/2004/07/System.Collections.Generic",
    "b": "http://www.w3.org/2001/XMLSchema",
}


def get_foilhole_ns(root):
    """
    Detect default namespace for FoilHole XML.
    Returns a dict suitable for ElementTree's namespaces argument.
    """
    if root.tag.startswith("{"):
        uri = root.tag.split("}")[0][1:]
        return {"fh": uri}
    else:
        return {}  # no namespace

def _normalize_custom_value_to_string(v):
    """
    Normalize CustomData <Value> into a string.
    - If it looks boolean-like, return "true" / "false"
    - Otherwise return stripped string
    - Return None for empty/missing
    """
    if v is None:
        return None

    # If some parser ever gives an actual bool
    if isinstance(v, bool):
        return "true" if v else "false"

    s = str(v).strip()
    if s == "":
        return None

    sl = s.lower()
    if sl in ("true", "t", "1", "yes", "y", "on"):
        return "true"
    if sl in ("false", "f", "0", "no", "n", "off"):
        return "false"

    return s

def parse_custom_value(root, key):
    """
    For FoilHole XML: CustomData contains a list of KeyValueOfstringanyType.
    We search for the given key and return the value as a normalized string.
    Handles both namespaced and non-namespaced forms, ignoring prefixes.
    """
    custom = root.find(".//fh:CustomData", get_foilhole_ns(root))
    if custom is None:
        custom = root.find(".//CustomData")
    if custom is None:
        return None

    for kv in list(custom):
        k_elem = None
        v_elem = None
        for child in list(kv):
            tag = child.tag
            if tag.endswith("}Key") or tag == "Key":
                k_elem = child
            elif tag.endswith("}Value") or tag == "Value":
                v_elem = child

        if k_elem is None or v_elem is None:
            continue

        if (k_elem.text or "").strip() == key:
            return _normalize_custom_value_to_string(v_elem.text)

    return None

def parse_micrograph_xml(file_path, pix_dict, beamsize_dict, caldate_dict):
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = get_foilhole_ns(root)  # {'fh': uri} or {}

    values = {}

    # Microscope ID and mapping (namespace-agnostic)
    inst_elem = find_first_by_localname(root, "InstrumentModel")
    instrument_model = inst_elem.text if inst_elem is not None else None
    if instrument_model is None:
        print(f"Warning: InstrumentModel not found in {file_path}")
    microscope_name, cs = MICROSCOPE_INFO.get(instrument_model, ("unknown", "unknown"))
    if microscope_name != "unknown":
        values["Microscope"] = microscope_name
        values["Spherical Aberration (mm)"] = cs
    else:
        values["Microscope"] = instrument_model
        if instrument_model and "TITAN" in instrument_model.upper():
            values["Spherical Aberration (mm)"] = 2.7
        elif instrument_model and "TALOS" in instrument_model.upper():
            values["Spherical Aberration (mm)"] = 2.7
        elif instrument_model and "ARCTICA" in instrument_model.upper():
            values["Spherical Aberration (mm)"] = 2.7
        elif instrument_model and "GLACIOS" in instrument_model.upper():
            values["Spherical Aberration (mm)"] = 2.7
        elif instrument_model and "TUNDRA" in instrument_model.upper():
            values["Spherical Aberration (mm)"] = 1.6

    # EPU version
    epu_ver = root.findtext(".//fh:microscopeData/fh:core/fh:ApplicationSoftwareVersion", namespaces=ns) \
        if ns else root.findtext("./microscopeData/core/ApplicationSoftwareVersion")
    values["EPU Version"] = epu_ver

    # Gun
    accel_v = root.findtext(".//fh:microscopeData/fh:gun/fh:AccelerationVoltage", namespaces=ns) \
        if ns else root.findtext("./microscopeData/gun/AccelerationVoltage")
    values["Acceleration Voltage (kV)"] = round(float(accel_v) / 1000) if accel_v else None

    extractor = root.findtext(".//fh:microscopeData/fh:gun/fh:ExtractorVoltage", namespaces=ns) \
        if ns else root.findtext("./microscopeData/gun/ExtractorVoltage")
    values["Extractor Voltage (V)"] = extractor

    gun_lens = root.findtext(".//fh:microscopeData/fh:gun/fh:GunLens", namespaces=ns) \
        if ns else root.findtext("./microscopeData/gun/GunLens")
    values["Gun Lens"] = gun_lens

    # Optics
    spot = root.findtext(".//fh:microscopeData/fh:optics/fh:SpotIndex", namespaces=ns) \
        if ns else root.findtext("./microscopeData/optics/SpotIndex")
    values["Spot Size"] = spot

    intensity = root.findtext(".//fh:microscopeData/fh:optics/fh:Intensity", namespaces=ns) \
        if ns else root.findtext("./microscopeData/optics/Intensity")
    values["Intensity"] = round(float(intensity), 3) if intensity else None

    # Nominal magnification
    mag = root.findtext(".//fh:microscopeData/fh:optics/fh:TemMagnification/fh:NominalMagnification", namespaces=ns) \
        if ns else root.findtext("./microscopeData/optics/TemMagnification/NominalMagnification")
    mag_val = round(float(mag)) if mag else None
    values["Nominal Magnification"] = mag_val

    # EPU Pixel size (A/pix)
    pix_m = root.findtext(".//fh:SpatialScale/fh:pixelSize/fh:x/fh:numericValue", namespaces=ns) \
        if ns else root.findtext("./SpatialScale/pixelSize/x/numericValue")
    pix_A = float(pix_m) * 1e10 if pix_m else None
    values["EPU Pixel Size (A/pix)"] = round(pix_A, 3) if pix_A is not None else None

    # Exposure time
    exp = root.findtext(".//fh:microscopeData/fh:acquisition/fh:camera/fh:ExposureTime", namespaces=ns) \
        if ns else root.findtext("./microscopeData/acquisition/camera/ExposureTime")
    exp_s = float(exp) if exp else None
    values["Exposure Time (s)"] = round(exp_s, 3) if exp_s is not None else None

    # Stage tilt
    tilt_rad = root.findtext(".//fh:microscopeData/fh:stage/fh:Position/fh:A", namespaces=ns) \
        if ns else root.findtext("./microscopeData/stage/Position/A")
    if tilt_rad:
        try:
            tilt_rad_val = float(tilt_rad)
            tilt_deg = tilt_rad_val * 180.0 / 3.141592653589793
            # Force to 0 if magnitude is < 0.1 degrees
            if abs(tilt_deg) < 0.1:
                values["Stage Tilt (Degrees)"] = 0
            else:
                values["Stage Tilt (Degrees)"] = round(tilt_deg, 2)
        except ValueError:
            # If parsing fails, fall back to 0
            values["Stage Tilt (Degrees)"] = 0
    else:
        values["Stage Tilt (Degrees)"] = 0

    # Energy filter
    eftem_on = root.findtext(".//fh:microscopeData/fh:optics/fh:EFTEMOn", namespaces=ns) \
        if ns else root.findtext("./microscopeData/optics/EFTEMOn")
    if eftem_on is not None:
        if eftem_on.lower() == "true":
            values["Energy Filter"] = "Yes"
            slit = root.findtext(".//fh:microscopeData/fh:optics/fh:EnergyFilter/fh:EnergySelectionSlitWidth", namespaces=ns) \
                if ns else root.findtext("./microscopeData/optics/EnergyFilter/EnergySelectionSlitWidth")
            values["Energy Filter Slit Width (eV)"] = slit if slit is not None else "N/A"
        else:
            values["Energy Filter"] = "No"
            values["Energy Filter Slit Width (eV)"] = "N/A"

    # Beam diameter (m -> um)
    beam_d = root.findtext(".//fh:microscopeData/fh:optics/fh:BeamDiameter", namespaces=ns) \
        if ns else root.findtext("./microscopeData/optics/BeamDiameter")
    if beam_d:
        values["Beam Diameter (um)"] = round(float(beam_d) * 1e6, 2)
    else:
        values["Beam Diameter (um)"] = None

    # Illumination mode
    illum_mode = root.findtext(".//fh:microscopeData/fh:optics/fh:IlluminationMode", namespaces=ns) \
        if ns else root.findtext("./microscopeData/optics/IlluminationMode")
    values["Illumination Mode"] = illum_mode if illum_mode is not None else "Unknown"

    # Camera name
    
    cam_TF_name = root.findtext(".//fh:microscopeData/fh:acquisition/fh:camera/fh:Name", namespaces=ns) \
        if ns else root.findtext("./microscopeData/acquisition/camera/Name")
    cam_name_string = f"Detectors[{cam_TF_name}].CommercialName"
    cam_name = parse_custom_value(root, f"{cam_name_string}")
    values["Camera"] = cam_name

    # Image dimensions (pixels) from FoilHole XML
    readout = root.find(".//microscopeData/acquisition/camera/ReadoutArea")
    if readout is None:
        # try namespaced microscopeData if needed
        readout = root.find(".//fh:microscopeData/fh:acquisition/fh:camera/fh:ReadoutArea", namespaces=ns) if ns else None

    width_elem = find_first_child_by_localname(readout, "width")
    height_elem = find_first_child_by_localname(readout, "height")

    width = int(width_elem.text) if width_elem is not None and width_elem.text else None
    height = int(height_elem.text) if height_elem is not None and height_elem.text else None

    if width is not None and height is not None:
        values["Image Dimensions (pixels)"] = f"{width} x {height}"
    else:
        values["Image Dimensions (pixels)"] = None

    # Camera mode (Counting/Linear) from camera info
    counted_key = f"Detectors[{cam_TF_name}].ElectronCounted"
    electron_counted = parse_custom_value(root, counted_key)

    if electron_counted is not None:
        if electron_counted.lower() == "true":
            values["Camera Mode"] = "Counting"
        elif electron_counted.lower() == "false":
            values["Camera Mode"] = "Linear"
        else:
            values["Camera Mode"] = "Unknown"

    # Gain reference file (Krios)
    gain_key = f"Detectors[{cam_TF_name}].GainReference"
    gain_ref = parse_custom_value(root, gain_key)
    if gain_ref is not None:
        values["Gain Reference File"] = gain_ref.strip()

    # Apertures from CustomData
    obj_ap = parse_custom_value(root, "Aperture[OBJ].Name")
    c2_ap = parse_custom_value(root, "Aperture[C2].Name")
    c3_ap = parse_custom_value(root, "Aperture[C3].Name")
    values["Objective Aperture (um)"] = obj_ap
    values["C2 Aperture (um)"] = c2_ap
    if c3_ap is not None:
        values["C3 Aperture (um)"] = c3_ap

    # DoseOnCamera (e/pix)
    dose_on_cam = parse_custom_value(root, "DoseOnCamera")
    dose_e_per_pix = float(dose_on_cam) if dose_on_cam is not None else None
    values["Approx. Total Dose (e/pix)"] = round(dose_e_per_pix, 2) if dose_e_per_pix is not None else None

    # Calibrated pixel size / beam size / cal date
    if mag_val is not None:
        cal_pix, cal_beam, cal_date = get_calibrated_values(mag_val, pix_dict, beamsize_dict, caldate_dict)
        values["Calibrated Pixel Size (A/pix)"] = cal_pix
        if beamsize_dict is not None:
            values["Beam Size (um)"] = cal_beam
            values["Pixel and Beam Size Calibration Date"] = cal_date
        else:
            values["Pixel Size Calibration Date"] = cal_date

    # Derived dose quantities
    if dose_e_per_pix is not None and pix_A is not None and exp_s is not None:
        dose_per_A2 = dose_e_per_pix / (pix_A ** 2)
        values["Approx. Total Dose (e/A2)"] = round(dose_per_A2, 2)
        values["Approx. Dose Rate (e/pix/s)"] = round(dose_e_per_pix / exp_s, 2)

    return values, instrument_model, cam_name

def load_calibration_table(path):
    df = pd.read_csv(path, sep=None, engine="python")
    df["Mag"] = df["Mag"].astype(float)
    pix_dict = pd.Series(df["PixelSize"].values, index=df["Mag"]).to_dict()
    caldate_dict = pd.Series(df["CalDate"].values, index=df["Mag"]).to_dict()
    beamsize_dict = None
    if "BeamSize" in df.columns:
        beamsize_dict = pd.Series(df["BeamSize"].values, index=df["Mag"]).to_dict()
    return pix_dict, beamsize_dict, caldate_dict

def get_calibrated_values(mag, pix_dict, beamsize_dict, caldate_dict):
    try:
        pix = pix_dict[mag]
        caldate = caldate_dict[mag]
        if beamsize_dict is not None:
            beamsize = beamsize_dict[mag]
        else:
            beamsize = "N/A"
        return pix, beamsize, caldate
    except KeyError:
        return "Pixel size not calibrated", (
            "Beam size not calibrated" if beamsize_dict is not None else "N/A"
        ), "Calibration date not found"

# ----------------------------
# EpuSession.dm parsing (namespaced)
# ----------------------------

def parse_sample_info(root, windows_root):
    sample = root.find(".//d:Samples/d:_items/d:SampleXml", NS)
    if sample is None:
        return None, None, None, None, None

    atlas_path = find_text(sample, "./d:AtlasId")
    atlas_path = atlas_path.replace(windows_root, '')
    atlas_path = atlas_path.replace('\\Atlas.dm', '')

    grid_geometry = find_text(sample, "./d:GridGeometry")
    grid_type = find_text(sample, "./d:GridType")

    hole_size_m = find_text(sample, "./d:FilterHolesSettings/d:HoleSize")
    hole_spacing_m = find_text(sample, "./d:FilterHolesSettings/d:HoleSpacing")

    hole_size_um = round(float(hole_size_m) * 1e6, 2) if hole_size_m else None
    hole_spacing_um = round(float(hole_spacing_m) * 1e6, 2) if hole_spacing_m else None

    return atlas_path, grid_type, grid_geometry, hole_size_um, hole_spacing_um

def parse_afis(root):
    mode = find_text(root, "./d:ClusteringMode")
    radius_m = find_text(root, "./d:ClusteringRadius")
    if mode == "NoClustering":
        return "No", "N/A"
    elif mode == "ClusteringWithImageBeamShift":
        radius_um = round(float(radius_m) * 1e6, 2) if radius_m else None
        return "Yes", radius_um
    else:
        return "Error", "Error"

def parse_defocus_and_acq_areas(root):
    areas = find_all(root, ".//d:TargetAreaTemplate//d:DataAcquisitionAreas//b:value", ns={
        "d": NS["d"],
        "b": NS["b"],
    })
    acq_areas = len(areas)

    # List of defocus lists, one per acquisition area
    defocus_per_area = []

    for area in areas:
        doubles = area.findall(".//d:ImageAcquisitionSettingXml/d:Defocus/a:_items/a:double", {
            "d": NS["d"],
            "a": NS["a"],
        })
        if not doubles:
            doubles = area.findall(".//d:ImageAcquisitionSettingXml/d:Defocus/a:double", {
                "d": NS["d"],
                "a": NS["a"],
            })

        this_area = []
        for d_elem in doubles:
            try:
                val_m = float(d_elem.text)
                val_um = round(val_m * 1e6, 2)
                if val_um < 0:
                    this_area.append(val_um)
            except (TypeError, ValueError):
                continue

        defocus_per_area.append(this_area)

    return defocus_per_area, acq_areas

def get_microscope_settings_block(root, key_name="Acquisition"):
    """
    Return the <value> element for the MicroscopeSettings entry whose <key> == key_name.
    Works with EPU 3.11 session files (namespace quirks).
    """
    # Find Samples (namespaced)
    samples = root.find(".//d:Samples", NS)
    if samples is None:
        return None

    # Find MicroscopeSettings under Samples
    ms = samples.find(".//d:MicroscopeSettings", NS)
    if ms is None:
        return None

    # Find the KeyValuePairs element by local name (namespace-agnostic)
    kvpairs = None
    for elem in ms.iter():
        tag = elem.tag
        if tag.endswith("KeyValuePairs") or tag == "KeyValuePairs":
            kvpairs = elem
            break

    if kvpairs is None:
        return None

    # Iterate over KeyValuePair elements (whatever their exact type name is)
    for kv in list(kvpairs):
        k_elem = None
        v_elem = None
        for child in list(kv):
            t = child.tag
            # match local names 'key'/'Key' and 'value'/'Value'
            if t.endswith("key") or t.endswith("Key"):
                k_elem = child
            elif t.endswith("value") or t.endswith("Value"):
                v_elem = child

        if k_elem is None or v_elem is None:
            continue

        if k_elem.text and k_elem.text.strip() == key_name:
            return v_elem

    return None

def parse_data_acquisition_block(root):
    """
    Parse EpuSession.dm for data acquisition settings:
    - Image dimensions (pixels)
    - Number of fractions
    - C2 aperture diameter
    - Beam diameter (um)
    """
    block = get_microscope_settings_block(root, "Acquisition")
    if block is None:
        return None, None, None, None

    # Helper to get local name from a tag like '{uri}Name' or 'Name'
    def lname(tag):
        return tag.split('}', 1)[-1] if '}' in tag else tag

    # ---------------- Image dimensions (pixels) ----------------
    acq_elem = None
    for elem in block.iter():
        if lname(elem.tag) == "Acquisition":
            acq_elem = elem
            break

    image_dims = None
    camera = None

    if acq_elem is not None:
        # Find <camera> under Acquisition
        for child in acq_elem:
            if lname(child.tag) == "camera":
                camera = child
                break

        if camera is not None:
            # Find <ReadoutArea> under camera
            readout = None
            for child in camera.iter():
                if lname(child.tag) == "ReadoutArea":
                    readout = child
                    break

            if readout is not None:
                width = height = None
                for child in readout:
                    lt = lname(child.tag)
                    if lt == "width" and child.text:
                        try:
                            width = int(child.text)
                        except ValueError:
                            pass
                    elif lt == "height" and child.text:
                        try:
                            height = int(child.text)
                        except ValueError:
                            pass
                if width is not None and height is not None:
                    image_dims = f"{width} x {height}"

    # ---------------- Number of fractions ----------------
    num_fractions = None
    if acq_elem is not None and camera is not None:
        # Find CameraSpecificInput under camera
        cam_spec = None
        for child in camera:
            if lname(child.tag) == "CameraSpecificInput":
                cam_spec = child
                break

        if cam_spec is not None:
            # Find KeyValuePairs under CameraSpecificInput
            kvpairs = None
            for elem in cam_spec.iter():
                if lname(elem.tag) == "KeyValuePairs":
                    kvpairs = elem
                    break

            if kvpairs is not None:
                for kv in kvpairs:
                    k_elem = None
                    v_elem = None
                    for child in kv:
                        lt = lname(child.tag)
                        if lt in ("key", "Key"):
                            k_elem = child
                        elif lt in ("value", "Value"):
                            v_elem = child
                    if k_elem is None or v_elem is None:
                        continue
                    if k_elem.text and k_elem.text.strip() == "FractionationSettings":
                        # Look for NumberOffractions under v_elem
                        for sub in v_elem.iter():
                            if lname(sub.tag) == "NumberOffractions" and sub.text:
                                try:
                                    num_fractions = int(sub.text)
                                except ValueError:
                                    num_fractions = None
                                break
                        break

    # ---------------- C2 aperture (um) ----------------
    c2_ap = None
    if acq_elem is not None:
        optics = None
        for elem in block.iter():
            if lname(elem.tag) == "Optics":
                optics = elem
                break
        if optics is not None:
            apertures = None
            for child in optics:
                if lname(child.tag) == "Apertures":
                    apertures = child
                    break
            if apertures is not None:
                c2_elem = None
                for child in apertures:
                    if lname(child.tag) == "C2Aperture":
                        c2_elem = child
                        break
                if c2_elem is not None:
                    for child in c2_elem:
                        if lname(child.tag) == "Diameter" and child.text:
                            c2_ap = child.text
                            break

    # ---------------- Beam diameter (um) ----------------
    beam_um = None
    if acq_elem is not None:
        optics = None
        for elem in block.iter():
            if lname(elem.tag) == "Optics":
                optics = elem
                break
        if optics is not None:
            beam_d_text = None
            for child in optics:
                if lname(child.tag) == "BeamDiameter" and child.text:
                    beam_d_text = child.text
                    break
            if beam_d_text:
                try:
                    beam_um = round(float(beam_d_text) * 1e6, 2)
                except ValueError:
                    beam_um = None

    return image_dims, num_fractions, c2_ap, beam_um

def parse_epu_session_dm(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()

    atlas_path, grid_type, grid_geometry, hole_size_um, hole_spacing_um = parse_sample_info(root, windows_root)
    afis, afis_dist = parse_afis(root)
    defocus_values, acq_areas = parse_defocus_and_acq_areas(root)
    image_dims, num_fractions, c2_ap, beam_um = parse_data_acquisition_block(root)

    distance_values = {
        4: '2/1',
        6: '2/4',
	2: '1/1',
        2.5: '1.2/1.3',
        4.5: '3.5/1',
        5: '1/4',
        25: '5/20',
        1.6: '0.6/1',
        0.6: '0.29/0.31 (hexaufoil)',
        1.0: '0.29/0.31 (hexaufoil every other)',
        1.3: '0.5/0.8 (hexaufoil testers)'
    }

    def best_guess_spacing(val):
        if val is None:
            return None
        closest = min(distance_values.keys(), key=lambda x: abs(x - val))
        return distance_values[closest]

    best_guess = best_guess_spacing(hole_spacing_um)

    dm_values = {
        "Atlas Path": atlas_path,
        "Grid Type": grid_type,
        "Grid Geometry": grid_geometry,
        "EPU Measured Hole Size (um)": hole_size_um,
        "EPU Measured Hole Center-to-Center Distance (um)": hole_spacing_um,
        "Best Guess Hole Size and Spacing (um)": best_guess,
        "AFIS": afis,
        "AFIS Clustering Distance (um)": afis_dist,
        "Number of Fractions": num_fractions,
    }

    return dm_values, defocus_values, acq_areas, atlas_path

# ----------------------------
# High-level workflow
# ----------------------------

def process_directory_screening(directory, pix_dict, beamsize_dict, caldate_dict):
    date = get_modification_time(directory).strftime('%Y%m%d')
    base = os.path.basename(os.path.normpath(directory))
    if base == "EPU_Out":
        folder_name = os.path.basename(os.path.dirname(directory))
    else:
        folder_name = base

    total_micrographs = count_files_with_pattern(
        os.path.join(directory, "Images-Disc1"),
        "FoilHole*Data*.mrc"
    )
    grid_squares = count_folders_with_data_and_pattern(directory, "FoilHole*Data*.mrc")

    df = pd.DataFrame(
        [[date, folder_name, grid_squares, total_micrographs]],
        columns=["Date", "Folder", "Grid Squares Screened", "Total Micrographs"]
    )
    df["Average Micrographs per Grid Square"] = (
        df["Total Micrographs"] / df["Grid Squares Screened"]
    ).round(2)

    foil_xml = find_first_foilhole_xml_in_data(directory)
    if foil_xml is None:
        print(f"No FoilHole XML found in Images-Disc1/*/Data for {directory}")
        return df, None, None

    xml_values, instrument_model, cam_name = parse_micrograph_xml(foil_xml, pix_dict, beamsize_dict, caldate_dict)

    epu_session_file = os.path.join(directory, "EpuSession.dm")
    dm_values = {}
    defocus_values = []
    acq_areas = None
    atlas_path = None
    if os.path.isfile(epu_session_file):
        dm_values, defocus_values, acq_areas, atlas_path = parse_epu_session_dm(epu_session_file)
        xml_values["Defocus Values (um)"] = defocus_values
        xml_values["Number of Acquisition Areas (Shots Per Hole)"] = acq_areas
    else:
        print(f"EpuSession.dm not found in {directory}")

    df_xml = pd.DataFrame([xml_values])
    df_dm = pd.DataFrame([dm_values])

    df_all = pd.concat([df.reset_index(drop=True), df_xml, df_dm], axis=1)

    if instrument_model and "TUNDRA" in instrument_model.upper():
        drop_cols = [c for c in df_all.columns if c.startswith("Energy Filter")]
        df_all = df_all.drop(columns=drop_cols, errors="ignore")

    return df_all, atlas_path, instrument_model

def process_directory_collection(directory, pix_dict, beamsize_dict, caldate_dict):
    exclude_dirs = {"Data", "FoilHoles"}
    earliest = get_earliest_file_modification_time(directory, exclude_dirs)
    latest = get_latest_folder_modification_time(directory)

    if earliest is None or latest is None:
        raise RuntimeError("Could not determine start/end times")

    foil_xml = find_first_foilhole_xml_in_data(directory)
    if foil_xml is None:
        raise RuntimeError(f"No FoilHole XML found in Images-Disc1/*/Data for {directory}")
    _, instrument_model, cam_name = parse_micrograph_xml(foil_xml, pix_dict, beamsize_dict, caldate_dict)

    if cam_name == "Ceta-F":
        fractions_ext = "mrc"
        pattern = "*Fractions.mrc"
    else:
        fractions_ext = "tiff"
        pattern = "*Fractions.tiff"

    images_disc1 = os.path.join(directory, "Images-Disc1")
    total_movies = count_files_with_pattern(images_disc1, pattern)
    grid_squares = count_folders_with_data_and_pattern(directory, pattern)

    date = earliest.strftime('%Y%m%d')

    # Folder naming: if the directory itself is EPU_Out, use its parent folder name
    base = os.path.basename(os.path.normpath(directory))
    if base == "EPU_Out":
        folder_name = os.path.basename(os.path.dirname(os.path.normpath(directory)))
    else:
        folder_name = base

    total_hours = round((latest - earliest).total_seconds() / 3600.0, 2)

    df = pd.DataFrame(
        [[date, folder_name, grid_squares, total_movies,
          earliest.strftime('%Y%m%d %H:%M:%S'),
          latest.strftime('%Y%m%d %H:%M:%S'),
          total_hours]],
        columns=["Date", "Folder", "Grid Squares Collected", "Total Movies",
                 "Start Time", "End Time", "Total Time (hrs)"]
    )

    df["Average Movies per Grid Square"] = (df["Total Movies"] / df["Grid Squares Collected"]).round(2)
    df["Movies per Hour"] = (df["Total Movies"] / df["Total Time (hrs)"]).round(2)

    foil_xml2, _ = find_matching_foilhole_and_fractions_in_data(directory, fractions_ext)
    if foil_xml2 is None:
        foil_xml2 = foil_xml

    xml_values, instrument_model, cam_name = parse_micrograph_xml(foil_xml2, pix_dict, beamsize_dict, caldate_dict)

    epu_session_file = os.path.join(directory, "EpuSession.dm")
    dm_values = {}
    defocus_values = []
    acq_areas = None
    atlas_path = None
    if os.path.isfile(epu_session_file):
        dm_values, defocus_values, acq_areas, atlas_path = parse_epu_session_dm(epu_session_file)
        xml_values["Defocus Values (um)"] = defocus_values
        xml_values["Number of Acquisition Areas (Shots Per Hole)"] = acq_areas
    else:
        print(f"EpuSession.dm not found in {directory}")

    df_xml = pd.DataFrame([xml_values])
    df_dm = pd.DataFrame([dm_values])

    df_all = pd.concat([df.reset_index(drop=True), df_xml, df_dm], axis=1)

    if instrument_model and "TUNDRA" in instrument_model.upper():
        drop_cols = [c for c in df_all.columns if c.startswith("Energy Filter")]
        df_all = df_all.drop(columns=drop_cols, errors="ignore")

    return df_all, atlas_path, instrument_model, cam_name

# ----------------------------
# Main
# ----------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Process EPU session directory and export collection/screening stats to a table"
    )
    parser.add_argument("directory", type=str, help="EPU session directory path")
    parser.add_argument("pixel_size_table", type=str, help="path to pixel size table (.txt)")
    parser.add_argument("mode", choices=["screening", "collection"], help="screening or collection")

    args = parser.parse_args()
    directory = args.directory
    pixel_size_table = args.pixel_size_table
    mode = args.mode

    pix_dict, beamsize_dict, caldate_dict = load_calibration_table(pixel_size_table)

    if mode == "screening":
        df_all, atlas_path, instrument_model = process_directory_screening(
            directory, pix_dict, beamsize_dict, caldate_dict
        )
    else:
        df_all, atlas_path, instrument_model, cam_name = process_directory_collection(
            directory, pix_dict, beamsize_dict, caldate_dict
        )

if __name__ == "__main__":
    main()
