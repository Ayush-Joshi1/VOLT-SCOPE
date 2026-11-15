"""Validate all Phase 1 parquet outputs and report key findings."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path

ROOT    = Path(r"c:\Users\ADMIN\OneDrive\Desktop\VOLT-SCOPE")
INTERIM = ROOT / "data" / "interim"
PROC    = ROOT / "data" / "processed"
TABLES  = ROOT / "outputs" / "tables"

files = {
    "swap_events_clean":           PROC    / "swap_events_clean.parquet",
    "station_hourly_status_clean": PROC    / "station_hourly_status_clean.parquet",
    "chronology_exceptions_24":    INTERIM / "chronology_exceptions_24_events.parquet",
    "fleet_partners_clean":        INTERIM / "fleet_partners_clean.parquet",
    "stations_clean":              INTERIM / "stations_clean.parquet",
    "city_daily_context_clean":    INTERIM / "city_daily_context_clean.parquet",
    "batteries_clean":             INTERIM / "batteries_clean.parquet",
    "riders_clean":                INTERIM / "riders_clean.parquet",
    "support_tickets_clean":       INTERIM / "support_tickets_clean.parquet",
}

print("FILE VALIDATION RESULTS")
print("=" * 70)
all_ok = True
for label, path in files.items():
    if path.is_file():
        meta = pq.read_metadata(str(path))
        mib  = path.stat().st_size / 1024 / 1024
        print(f"  OK  {label:<40} {meta.num_rows:>12,} rows  {mib:.2f} MiB")
    else:
        print(f"  MISSING  {label}")
        all_ok = False

print()

# Key uniqueness on reference tables
print("KEY UNIQUENESS CHECKS")
print("=" * 70)
key_checks = [
    ("fleet_partners_clean",     INTERIM / "fleet_partners_clean.parquet",         ["partner_id"]),
    ("stations_clean",           INTERIM / "stations_clean.parquet",                ["station_id"]),
    ("batteries_clean",          INTERIM / "batteries_clean.parquet",               ["battery_id"]),
    ("riders_clean",             INTERIM / "riders_clean.parquet",                  ["rider_id"]),
    ("support_tickets_clean",    INTERIM / "support_tickets_clean.parquet",         ["ticket_id"]),
    ("city_daily_context_clean", INTERIM / "city_daily_context_clean.parquet",      ["city", "date"]),
]
for label, path, keys in key_checks:
    df = pd.read_parquet(str(path), columns=keys)
    dupes = df.duplicated().sum()
    nulls = df.isnull().any(axis=1).sum()
    status = "PASS" if dupes == 0 and nulls == 0 else "FAIL"
    print(f"  [{status}] {label:<35} key={'+'.join(keys)} dupes={dupes} nulls={nulls}")

print()

# Chronology exceptions detail
print("CHRONOLOGY EXCEPTION DETAIL")
print("=" * 70)
exc = pd.read_parquet(str(INTERIM / "chronology_exceptions_24_events.parquet"))
print(f"  Total events: {len(exc)}")
fw_vals = exc["station_firmware"].astype(str).unique().tolist()
print(f"  Firmware versions: {fw_vals}")
sids = exc["station_id"].unique().tolist()
print(f"  Distinct stations ({len(sids)}): {sids}")
agg = exc.groupby(["station_id", "event_date", "commissioned_date"]).size().reset_index(name="n")
print("  Station-day breakdown:")
for _, row in agg.iterrows():
    print(f"    {row['station_id']}: event_date={row['event_date'].date()}  "
          f"commissioned={row['commissioned_date'].date()}  events={row['n']}")

print()

# Swap events smoke test
print("SWAP EVENTS QUICK STATS")
print("=" * 70)
swaps_meta = pq.read_metadata(str(PROC / "swap_events_clean.parquet"))
print(f"  Total rows: {swaps_meta.num_rows:,}")
# Read a small sample for event type distribution
swaps_sample = pd.read_parquet(str(PROC / "swap_events_clean.parquet"),
                                columns=["event_type", "sync_mode", "station_firmware"])
print("  event_type distribution:")
print(swaps_sample["event_type"].value_counts().to_string())
print("  sync_mode distribution:")
print(swaps_sample["sync_mode"].value_counts().to_string())
print("  station_firmware top versions:")
print(swaps_sample["station_firmware"].value_counts().head(10).to_string())

print()

# Station hourly smoke test
print("STATION HOURLY STATUS QUICK STATS")
print("=" * 70)
tele_meta = pq.read_metadata(str(PROC / "station_hourly_status_clean.parquet"))
print(f"  Total rows: {tele_meta.num_rows:,}")
tele_sample = pd.read_parquet(str(PROC / "station_hourly_status_clean.parquet"),
                               columns=["telemetry_status"])
print("  telemetry_status distribution:")
print(tele_sample["telemetry_status"].value_counts().to_string())

print()

# Save validation results
import pandas as pd
val_rows = []
for label, path in files.items():
    exists = path.is_file()
    rows = pq.read_metadata(str(path)).num_rows if exists else 0
    val_rows.append({"check": f"File exists: {label}", "status": "PASS" if exists else "FAIL",
                     "detail": f"{rows:,} rows"})

# Key uniqueness
for label, path, keys in key_checks:
    df = pd.read_parquet(str(path), columns=keys)
    dupes = df.duplicated().sum()
    nulls = df.isnull().any(axis=1).sum()
    passed = dupes == 0 and nulls == 0
    val_rows.append({"check": f"Key unique: {label}", "status": "PASS" if passed else "FAIL",
                     "detail": f"dupes={dupes} nulls={nulls}"})

# Exception check
val_rows.append({"check": "Chronology exceptions: count=24", "status": "PASS" if len(exc) == 24 else "FAIL",
                 "detail": f"actual count={len(exc)}"})
val_rows.append({"check": "Chronology exceptions: all v3.2.0",
                 "status": "PASS" if (exc["station_firmware"].astype(str) == "v3.2.0").all() else "FAIL",
                 "detail": f"firmware_versions={fw_vals}"})

# Exception events present in swap_events
swaps_ids = pd.read_parquet(str(PROC / "swap_events_clean.parquet"), columns=["event_id"])["event_id"].astype(str)
exc_ids   = exc["event_id"].astype(str)
present   = exc_ids.isin(swaps_ids).all()
val_rows.append({"check": "Exceptions present in swap_events_clean", "status": "PASS" if present else "FAIL",
                 "detail": f"{exc_ids.isin(swaps_ids).sum()}/{len(exc_ids)} found"})

val_df = pd.DataFrame(val_rows)
val_df.to_csv(str(TABLES / "phase1_validation_results.csv"), index=False, encoding="utf-8")
print(f"Validation saved -> outputs/tables/phase1_validation_results.csv")
n_pass = (val_df["status"] == "PASS").sum()
n_fail = (val_df["status"] == "FAIL").sum()
print(f"TOTAL: {n_pass} PASS | {n_fail} FAIL")
