import pathlib

path = pathlib.Path(r"c:\Users\ADMIN\OneDrive\Desktop\VOLT-SCOPE\src\data\prepare_phase1.py")
content = path.read_text(encoding="utf-8")

# Fix unicode arrows
content = content.replace("\u2192", "->")

# Fix pandas 3.0 select_dtypes deprecation - use 'str' instead of 'object'
# In pandas 3.0+, string columns have dtype 'str', not 'object'
content = content.replace(
    'df.select_dtypes(include=["object", "str"])',
    'df.select_dtypes(include=["object", "str"])'
)
# Just replace the original single-quoted version
old = "df.select_dtypes(include=\"object\")"
new = "df.select_dtypes(include=[\"object\", \"str\"])"
content = content.replace(old, new)

path.write_text(content, encoding="utf-8")
print(f"Fixed. Occurrences of old select_dtypes: {content.count('select_dtypes')}")
print("Done.")
