from csv import reader
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


def import_rows(path: Path) -> list[list[str]]:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as source:
            return list(reader(source))
    with ZipFile(path) as workbook:
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    return [[cell.findtext("{*}is/{*}t", default="") for cell in row] for row in sheet.findall(".//{*}row")]
