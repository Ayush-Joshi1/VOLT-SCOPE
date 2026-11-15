# VOLT SCOPE

VOLT SCOPE is a reliability-to-profitability intelligence project for the Gradient Learnings Data Analytics Hackathon. Stage 1 establishes the project structure and audits the eight supplied raw datasets without changing them.

## Structure

- `data/raw/` — original source CSV files (kept out of Git)
- `data/interim/`, `data/processed/` — reserved for later stages
- `notebooks/` — later analysis notebook
- `src/data/` — reusable data audit
- `src/analysis/`, `src/utils/` — reserved for later stages
- `reports/` — audit and analysis reports
- `outputs/tables/`, `outputs/figures/` — generated outputs

## Setup and run

Use Python 3.10+ and install the small set of Stage 1 dependencies:

```powershell
python -m pip install -r requirements.txt
python src\data\audit_data.py
```

To adjust chunk size:

```powershell
python src\data\audit_data.py --chunk-size 50000
```

The audit streams the large CSV files with Pandas, writes CSV summaries to `outputs/tables/`, and writes the full findings to `reports/stage_01_data_audit.md`. It does not modify files in `data/raw/`.
