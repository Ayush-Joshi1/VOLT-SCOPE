import sys, json
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

ROOT = Path(r"c:\Users\ADMIN\OneDrive\Desktop\VOLT-SCOPE")

files = {
    'Phase 2 Notebook':    ROOT / 'notebooks' / '02_business_analytics.ipynb',
    'Business Report':     ROOT / 'reports' / 'VoltRelay_Final_Analytics_Report.docx',
    'Presentation Script': ROOT / 'reports' / 'presentation_script.md',
    'Phase 1 Notebook':    ROOT / 'notebooks' / '01_data_audit_and_preparation.ipynb',
    'Data Dictionary':     ROOT / 'reports' / 'data_dictionary.md',
    'Phase 1 Report':      ROOT / 'reports' / 'phase1_data_quality_and_cleaning_report.md',
}

print('FINAL DELIVERABLE VALIDATION')
print('=' * 60)
all_pass = True
for label, path in files.items():
    exists = path.exists()
    size = path.stat().st_size / 1024 if exists else 0
    status = 'PASS' if exists else 'FAIL'
    if not exists:
        all_pass = False
    print(f'  [{status}] {label:30s} {size:7.1f} KB   {path.name}')

print()
an_dir = ROOT / 'outputs' / 'analytics'
csvs = list(an_dir.glob('*.csv'))
status = 'PASS' if len(csvs) >= 36 else 'FAIL'
if len(csvs) < 36:
    all_pass = False
print(f'  [{status}] Analytics CSVs (outputs/analytics/)  {len(csvs)} files')

proc_dir = ROOT / 'data' / 'processed'
parqs = list(proc_dir.glob('*.parquet'))
status = 'PASS' if len(parqs) >= 2 else 'FAIL'
if len(parqs) < 2:
    all_pass = False
print(f'  [{status}] Processed Parquets (data/processed/) {len(parqs)} files')

nb_path = ROOT / 'notebooks' / '02_business_analytics.ipynb'
if nb_path.exists():
    try:
        nb = json.loads(nb_path.read_text(encoding='utf-8'))
        n_cells = len(nb['cells'])
        md = sum(1 for c in nb['cells'] if c['cell_type'] == 'markdown')
        code = sum(1 for c in nb['cells'] if c['cell_type'] == 'code')
        print(f'  [PASS] Notebook valid JSON: {n_cells} cells ({md} markdown, {code} code)')
    except Exception as e:
        print(f'  [FAIL] Notebook JSON invalid: {e}')
        all_pass = False

print()
print('=' * 60)
result = 'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'
print(f'OVERALL: {result}')
