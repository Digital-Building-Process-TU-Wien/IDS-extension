# IDS Extension

This repository contains proposed extensions to the Information Delivery Specification (IDS) schema that facilitate the more sophisticated handling of relations. These extensions are based on the official IDS 1.0 version.
The repository contains the new IDS schema definition [ids.xsd](ifctester/ids.xsd), as well as an enhanced, Python-based IDS checking prototype, which is based on the open-source IfcOpenShell package, IfcTester (v0.9.0).

The repository combines the results from two different extension projects:
1. **Advanced related element restriction by extending the PartOf facet** 
   This project involves nesting the 'Attribute' and 'Property' facets within the 'PartOf' facet in order to define the characteristics of related elements. The relationships considered by the PartOf facet are also extended to include IfcRelSpaceBoundary. Finally, functionality is introduced to enable the comparison of multiple related elements.
   The corresponding journal article can be found here: [https://doi.org/10.1016/j.dibe.2024.100560](https://doi.org/10.1016/j.dibe.2024.100560)
2. **Full IFC traversal by extending the Attribute and Material facet**
   This project applies the concept of facet nesting to the Attribute and Material facets. Nesting the Attribute facet within itself and the Material facet allows relation and inverse attributes that refer to other entities to be used for traversing the IFC schema. Nesting the other facets allows the related entities to be restricted. This therefore represents a general approach to aligning IDS functionality with the IFC's hierarchical structure.

None of the extensions to the schema conflict with the IDS 1.0 schema. The extensions represent optional functionality. Therefore, the extended IDS checking prototype can verify IFC models against standard or extended IDS files. All extensions in the IfcTester files are labelled 'Extension'.

### Project structure

1. **Folder [ids-files/](ids-files/)**: Contains IDS specification files organized by facet extension type:
   - [attribute-material-facet-extension/](ids-files/attribute-material-facet-extension/): IDS files for the Attribute and Material facet extensions
   - [partOf-facet-extension/](ids-files/partOf-facet-extension/): IDS files for the PartOf facet extensions
2. **Folder [ifc-files/](ifc-files/)**: Contains test IFC models for validation
3. **Folder [ifctester/](ifctester/)**: Core IDS validation engine with extended functionality:
   - [facet.py](ifctester/facet.py): Contains specific extended IDS facet functionality
   - [ids.py](ifctester/ids.py): Conatins extension for general IDS facet recursion
   - [reporter.py](ifctester/reporter.py): Contains extensions to align reporter functionality with the new recursion functionality.
   - [ids.xsd](ifctester/ids.xsd): Adapted XML Schema Definition for IDS file validation
4. **[IDS-validator-main.py](IDS-validator-main.py)**: Main script for validating IFC files against IDS specifications using the extended IDS functionality.
5. **[requirements.txt](requirements.txt)**: Python dependencies

All data files are licensed under CC BY 4.0, all software files are licensed under MIT License.

## Installation

1. Clone or download this repository
2. Install Python
3. Open Terminal in the project folder
4. Create a virtual environment:
   ```bash
   python -m venv venv
   ```
5. Activate the virtual environment:
   - **Windows:**
     ```bash
     venv\Scripts\activate
     ```
   - **Linux/macOS:**
     ```bash
     source venv/bin/activate
     ```
6. Install all dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Run the project

Try the project with the default test files:

```bash
python IDS-validator-main.py
```

The validator will check the IFC model against the IDS specification and output the results to the console.

### Override the input and output paths

The default input paths can be overwritten either directly in the script or via command line arguments.

#### Override the input paths in the script

To override the input paths, you can edit the default settings in lines 7-10 in [IDS-validator-main.py](IDS-validator-main.py):

```python
#Default settings
default_IDS_path = "./ids-files/"
default_input_IDS_name = "Geometric-Representation-Restriction.ids"
default_IFC_path = "./ifc-files/"
default_input_IFC_name = "ifc2023_de_D0077-enriched.ifc"
```

#### Override the input paths via command line arguments

```bash
python IDS-validator-main.py -h
```

```
usage: IDS-validator-main.py [-h] [--ids-path IDS_PATH] [--ids-name IDS_NAME] [--ifc-path IFC_PATH] [--ifc-name IFC_NAME]

Validates IFC files against IDS specifications

options:
  -h, --help            show this help message and exit
  --ids-path IDS_PATH   Path to IDS files directory (default: ./ids-files/)
  --ids-name IDS_NAME   IDS filename (default: Geometric-Representation-Restriction.ids)
  --ifc-path IFC_PATH   Path to IFC files directory (default: ./ifc-files/)
  --ifc-name IFC_NAME   IFC filename (default: ifc2023_de_D0077-enriched.ifc)
```

To run with custom paths, provide the arguments explicitly:

```bash
python IDS-validator-main.py --ids-path ./ids-files/partOf-facet-extension/ --ids-name Information-Requirements-EscapeRouteAnalysis.ids --ifc-path ./ifc-files/ --ifc-name CustomTestModel-EscapeRouteAnalysis-ZDB-v2.ifc 
```