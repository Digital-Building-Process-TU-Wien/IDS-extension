import ifcopenshell
from ifctester import reporter
from ifctester import ids
import argparse

#Default settings
default_IDS_path = "./ids-files/attribute-material-facet-extension/"
default_input_IDS_name = "Geometric-Representation-Restriction.ids"
default_IFC_path = "./ifc-files/"
default_input_IFC_name = "ifc2023_de_D0077-enriched.ifc"


def parse_arguments():
    parser = argparse.ArgumentParser(description="Validates IFC files against IDS specifications")
    parser.add_argument("--ids-path", type=str, default=default_IDS_path, 
                        help=f"Path to IDS files directory (default: {default_IDS_path})")
    parser.add_argument("--ids-name", type=str, default=default_input_IDS_name,
                        help=f"IDS filename (default: {default_input_IDS_name})")
    parser.add_argument("--ifc-path", type=str, default=default_IFC_path,
                        help=f"Path to IFC files directory (default: {default_IFC_path})")
    parser.add_argument("--ifc-name", type=str, default=default_input_IFC_name,
                        help=f"IFC filename (default: {default_input_IFC_name})")
    return parser.parse_args()

def main():

    args = parse_arguments()

    ids_path = args.ids_path
    ids_name = args.ids_name
    ifc_path = args.ifc_path
    ifc_name = args.ifc_name

    ids_file = ids.open(ids_path+ids_name,True)
    ifc_file = ifcopenshell.open(ifc_path+ifc_name,True)

    print(f"IDS file: {ids_path+ids_name}")
    print(f"IFC file: {ifc_path+ifc_name}")
    print()

    ids_file.validate(ifc_file)

    print("Checking complete!")
    print()

    reporter.Console(ids_file).report()

    print()
    print("Report complete!")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)