# EPU_Generate_Report
A set of scripts that generates a statistics txt file and a pdf of a single particle EPU screening or collection session. 

Note: the epu folder is identical to the epu folder in the EPU_Screening_Visualization scripts. If you are already using that and are implementing this on the same computer, you can download only generate_report.py and make a pixelsizes.txt copy and put them both in the same folder as app.py. You will need to add the rest of the dependencies into your screening_vis python environment. 

# Getting Started
_These scripts are intended for use in a Linux (or WSL) environment._

1. Create a new python environment
   ```bash
   conda create --name epu_report python=3.9 numpy pandas pillow reportlab font-ttf-dejavu-sans-mono, fonts-conda-ecosystem
   ```
   _Note the fonts are recommended but not required_

2. Modify this section of epu/epustats.py with your microscope information

   ```python
   MICROSCOPE_INFO = {
       "TUNDRA-XXX": ("DFCI Tundra", 1.6),
       "TITANXXX": ("HMS Krios2", 2.7),
       "TITANXXX": ("HMS Krios1", 2.7),
   }

   windows_root = "Z:\\"
   ```

   MICROSCOPE_INFO should contain your InstrumentModel (replace XXX with the serial number) followed by the
   spherical aberration in mm. If you do not know what to use for InstrumentModel, you can run 
   ```bash
   grep InstrumentModel FoilHole*.xml
   ```
   in a bash terminal within Images-Disc1/GridSquare*/Data/ (repalace the * after FoilHole with a single file
   name)
   
   The windows_root is the root of where the atlas is stored on the original drive. If you are not sure what
   your root is, you can run
   ```bash
   grep -oP 'AtlasId .{0,50}' EpuSession.dm
   ```
   in the terminal while standing in the base directory of an imaging session. This is only used to "clean up"
   the atlas path displayed in the summary table.
   
3. The code assumes that you have a pixel size table named pixelsizes.txt located in the same directory as generate_report.py). There are two example files provided here that you can modify. You can omit the beam size column for 3-condenser systems. If you want the table to live elsewhere, or if you need multiple tables for different microscopes, you can specify the pixel-table path (see Running the Script, below).

4. If your microscope writes out .tiff files, or if you have a Ceta-F that writes out .mrc files, you do
   not need to change anything. Otherwise, you will have to modify this segment of epu/epustats.py to include
   your file extension(s). 
   ```python
    if cam_name == "Ceta-F":
        fractions_ext = "mrc"
        pattern = "*Fractions.mrc"
    else:
        fractions_ext = "tiff"
        pattern = "*Fractions.tiff"
   ```

# Running the Script

   ```bash
  conda activate transferenv
  python generate_report.py {path/to/session}
   ```

Alternative commands if you need to specify nonstandard paths:
```bash
  python3 generate_report.py /path/to/session
  python3 generate_report.py /path/to/session /optional/path/to/atlas_root_or_atlas
  python3 generate_report.py /path/to/session --pixel-table /path/to/pixelsizes.txt
  python3 generate_report.py /path/to/session /optional/atlas --pixel-table /path/to/pixelsizes.txt
```
_The path/to/session must be the full EPU-generated directory containing all metadata. The atlas path must be specified if the atlas directory is not located within the EPU directory or its parent directory_

_These scripts were generated with the assistance of GPT4DFCI, a private, HIPAA-secure endpoint to GPT-4o provided by DFCI_

# Example Outputs

## PDF report screenshots:
<img width="627" height="791" alt="p1" src="https://github.com/user-attachments/assets/c7d5c29d-e338-40a7-b6f2-40019cb3e369" />

<img width="651" height="802" alt="p2" src="https://github.com/user-attachments/assets/e6d61524-7dfd-46d2-a984-de1aa51f2708" />

<img width="637" height="733" alt="p3" src="https://github.com/user-attachments/assets/5cb14a6f-9698-46a2-8bf0-906ae4ed94ea" />

## txt file with screening/collection statistics:
```txt
Date                                              20260220                                              
Folder                                            TL_20260220_110k                                      
Start Time                                        20260220 12:20:51                                     
End Time                                          20260220 14:04:33                                     
Total Time (hrs)                                  1.73                                                  
Grid Squares Collected                            2                                                     
Total Movies                                      332                                                   
Average Movies per Grid Square                    166.0                                                 
Movies per Hour                                   191.91                                                
Microscope                                        DFCI Tundra                                           
Acceleration Voltage (kV)                         100                                                   
Extractor Voltage (V)                             3850                                                  
Spherical Aberration (mm)                         1.6                                                   
Gun Lens                                          4                                                     
Spot Size                                         4                                                     
Intensity                                         0.448                                                 
EPU Version                                       3.15.0.11609                                          
C2 Aperture (um)                                  50                                                    
Objective Aperture (um)                           None                                                  
Camera                                            Ceta-F                                                
Image Dimensions (pixels)                         4096 x 4096                                           
Nominal Magnification                             110000                                                
EPU Pixel Size (A/pix)                            1.209                                                 
Calibrated Pixel Size (A/pix)                     1.2                                                   
Beam Size (um)                                    1.3                                                   
Pixel and Beam Size Calibration Date              20260110                                              
Exposure Time (s)                                 2.864                                                 
Approx. Total Dose (e/pix)                        43.09                                                 
Approx. Total Dose (e/A2)                         29.46                                                 
Approx. Dose Rate (e/pix/s)                       15.05                                                 
Grid Type                                         HoleyCarbon                                           
Grid Geometry                                     Square                                                
EPU Measured Hole Size (um)                       1.32                                                  
EPU Measured Hole Center-to-Center Distance (um)  2.6                                                   
Best Guess Hole Size and Spacing (um)             1.2/1.3                                               
Number of Acquisition Areas (Shots Per Hole)      1                                                     
AFIS                                              Yes                                                   
AFIS Clustering Distance (um)                     6.0                                                   
Number of Fractions                               39                                                    
Defocus Values (um)                               [-2.1, -1.9, -1.7, -1.5, -1.3, -1.1, -0.9, -0.7, -0.5]

Notes:
    -Please contact Talya if any of these numbers appear to be incorrect! The script may need updating.
    -The dose is approximated from the first movie. The total dose on specimen is slightly higher; however, if you did not record the dose when setting up collection, this will be appropriate for most (if not all) processing.
    -The hole size and spacing is guessed based on the measure hole size function in EPU. If you are using an uncommon hole size/spacing, it may misidentify it.
    -Pixel size is listed both as the pixel size automatically coded in EPU as well as the experimentally-calibrated pixel size. I advise that you use the calibrated pixel size in processing
```
