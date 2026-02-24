# EPU_Generate_Report
A set of scripts that generates a statistics txt file and a pdf of a single particle EPU screening or collection session. 

# Getting Started
1. Create a new python environment
   ```bash
   conda create --name epu_report python=3.9 numpy pandas pillow reportlab font-ttf-dejavu-sans-mono, fonts-conda-ecosystem
   ```
   _Note the fonts are recommended but not required_
2. Update the appropriate pixelsizes.txt file with your pixel size information.
3. Update EPU_stats to include your microscope information. First, change
   ```python
   MICROSCOPE_INFO = {
      "TUNDRA-XXX": ("DFCI Tundra", 1.6),
      "TITANXXX": ("HMS Krios2", 2.7),
      "TITANXXX": ("HMS Krios1", 2.7),
   }
   ```
   to contain your serial number in the XXX spot and the correct spherical aberration in mm for your microscope. Then, if you will be using this script with a Tundra, modify the instances of
   ```python
   if instrument_model == "TUNDRA-XXX":
   ```
   to contain your serial number in the XXX spot. Note that the 

# Running the Script

   ```bash
  conda activate transferenv
  python epustats.py {/path/to/folder/to/query} {/path/to/pixelsizes.txt}
  python generate_report.py {path/to/folder} [optional: path/to/atlas]
   ```

Note 1: You could definitely combine integrate epustats.py into generate_report.py, it is kept separate only because of our transfer workflow

Note 2: generate_report.py should be able to automatically find the atlas images if the directory is located within the screening/collection directory or the directory containing it. However you can provide the path to the directory containing the atlas images if it is located elsewhere. If there are no atlas images, the report will still generate but will skip showing atlas images. This version of the script does not find atlas images for non-Tundra data sets but could be easily modified to do so. 

_These scripts were generated with the assistance of GPT4DFCI, a private, HIPAA-secure endpoint to GPT-4o provided by DFCI_
