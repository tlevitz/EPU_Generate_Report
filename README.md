# EPU_Generate_Report
A set of scripts that generates a statistics txt file and a pdf of a single particle EPU screening or collection session. 

# Getting Started
1. Create a new python environment
   ```bash
   conda create --name epu_report python=3.9 numpy pandas pillow reportlab font-ttf-dejavu-sans-mono, fonts-conda-ecosystem
   ```
   _Note the fonts are recommended but not required_
2. Update the appropriate pixelsizes.txt file with your pixel size information.
3. Update EPU_stats to include your microscope information.

      - First, change
      ```python
      MICROSCOPE_INFO = {
         "TUNDRA-XXX": ("DFCI Tundra", 1.6),
         "TITANXXX": ("HMS Krios2", 2.7),
         "TITANXXX": ("HMS Krios1", 2.7),
      }
      ```
      to contain your serial number in the XXX spot and the correct spherical aberration in mm for your microscope. Note that the Tundra has a hyphen after it whereas the Titan does not. 

      - Next, if you will be using this script with a Tundra, modify the instances of
      ```python
      if instrument_model == "TUNDRA-XXX":
      ```
      to contain your serial number in the XXX spot. 

      - Lastly, depending on your image format, you may have to modify this snippet to change the extension:
      ```python
      if instrument_model == "TUNDRA-XXX":
         fractions_ext = "mrc"
         pattern = "*Fractions.mrc"
      else:
         fractions_ext = "tiff"
         pattern = "*Fractions.tiff"
      ```        

# Running the Script

   ```bash
  conda activate transferenv
  python epustats.py {/path/to/folder/to/query} {/path/to/pixelsizes.txt}
  python generate_report.py {path/to/folder} [optional: path/to/atlas]
   ```

_The folder/to/query must be the full EPU-generated folder containing all metadata_

Note 1: You could definitely combine integrate epustats.py into generate_report.py, it is kept separate only because of our transfer workflow

Note 2: generate_report.py should be able to automatically find the atlas images if the directory is located within the screening/collection directory or the directory containing it. However you can provide the path to the directory containing the atlas images if it is located elsewhere. If there are no atlas images, the report will still generate but will skip showing atlas images. This version of the script does not find atlas images for non-Tundra data sets but could be easily modified to do so. 

_These scripts were generated with the assistance of GPT4DFCI, a private, HIPAA-secure endpoint to GPT-4o provided by DFCI_

# Example Outputs

## PDF report screenshots:
<img width="627" height="791" alt="p1" src="https://github.com/user-attachments/assets/c7d5c29d-e338-40a7-b6f2-40019cb3e369" />

<img width="651" height="802" alt="p2" src="https://github.com/user-attachments/assets/e6d61524-7dfd-46d2-a984-de1aa51f2708" />

<img width="637" height="733" alt="p3" src="https://github.com/user-attachments/assets/5cb14a6f-9698-46a2-8bf0-906ae4ed94ea" />

## txt file with screening/collection statistics:

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
