"""Fix notebook: replace Unicode arrows and pandas 3.0 select_dtypes deprecation."""
import pathlib

nb_path = pathlib.Path(
    r"c:\Users\ADMIN\OneDrive\Desktop\VOLT-SCOPE\notebooks\01_data_audit_and_preparation.ipynb"
)
content = nb_path.read_text(encoding="utf-8")

# Fix unicode arrows (Windows CP1252 safety)
content = content.replace("\u2192", "->")

# Fix pandas 3.0 select_dtypes deprecation
content = content.replace(
    'include="object"',
    'include=["object", "str"]'
)

nb_path.write_text(content, encoding="utf-8")
print(f"Notebook fixed. Size: {len(content):,} chars")
