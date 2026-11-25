import pathlib
import runpy

runpy.run_path(str(pathlib.Path(__file__).resolve().parents[2] / "scope_excel_first" / "private" / "grade.py"))
