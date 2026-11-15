"""Fast Phase 1 validation using DuckDB (columnar pushdown, no full pandas load).

This script reads large Parquet files via DuckDB for fast aggregation,
only loading small reference tables into pandas.
Run from project root: python src/data/_validate_phase1_fast.py
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq
import duckdb

ROOT    = Path(r"c:\Users\ADMIN\OneDrive\Desktop\VOLT-SCOPE")
INTERIM = ROOT / "data" / "interim"
PROC    = ROOT / "data" / "processed"
TABLES  = ROOT / "outputs" / "tables"

SWAPS     = str(PROC    / "swap_events_clean.parquet")
TELE      = str(PROC    / "station_hourly_status_clean.parquet")
EXC       = str(INTERIM / "chronology_exceptions_24_events.parquet")
FLEET     = str(INTERIM / "fleet_partners_clean.parquet")
STATIONS  = str(INTERIM / "stations_clean.parquet")
CDC       = str(INTERIM / "city_daily_context_clean.parquet")
BATT      = str(INTERIM / "batteries_clean.parquet")
RIDERS    = str(INTERIM / "riders_clean.parquet")
TICKETS   = str(INTERIM / "support_tickets_clean.parquet")

val_rows = []

def chk(name, passed, detail=""):
    st = "PASS" if passed else "FAIL"
    val_rows.append({"check": name, "status": st, "detail": detail})
    icon = "+" if passed else "x"
    print(f"  [{icon}] [{st}] {name}" + (f" -- {detail}" if detail else ""))

con = duckdb.connect()

print("=" * 70)
print("PHASE 1 FAST VALIDATION (DuckDB)")
print("=" * 70)

# ---- 1. File existence ----
print("\n[ FILE EXISTENCE ]")
for label, path in [
    ("swap_events_clean.parquet",           SWAPS),
    ("station_hourly_status_clean.parquet", TELE),
    ("chronology_exceptions_24.parquet",    EXC),
    ("fleet_partners_clean.parquet",        FLEET),
    ("stations_clean.parquet",              STATIONS),
    ("city_daily_context_clean.parquet",    CDC),
    ("batteries_clean.parquet",             BATT),
    ("riders_clean.parquet",                RIDERS),
    ("support_tickets_clean.parquet",       TICKETS),
]:
    exists = Path(path).is_file()
    size   = f"{Path(path).stat().st_size/1024/1024:.2f} MiB" if exists else "N/A"
    chk(f"File exists: {label}", exists, size)

# ---- 2. Row counts via pyarrow metadata (instant) ----
print("\n[ ROW COUNTS (pyarrow metadata, no data load) ]")
meta_counts = {}
for label, path in [
    ("swap_events",           SWAPS),
    ("station_hourly_status", TELE),
    ("chronology_exceptions", EXC),
    ("fleet_partners",        FLEET),
    ("stations",              STATIONS),
    ("city_daily_context",    CDC),
    ("batteries",             BATT),
    ("riders",                RIDERS),
    ("support_tickets",       TICKETS),
]:
    if Path(path).is_file():
        n = pq.read_metadata(path).num_rows
        meta_counts[label] = n
        print(f"  {label:<35} {n:>12,} rows")
    else:
        meta_counts[label] = 0
        print(f"  {label:<35}      MISSING")

chk("swap_events: row count preserved (3,877,013)",
    meta_counts.get("swap_events", 0) == 3_877_013,
    f"actual={meta_counts.get('swap_events',0):,}")

chk("station_hourly_status: non-empty",
    meta_counts.get("station_hourly_status", 0) > 0,
    f"rows={meta_counts.get('station_hourly_status',0):,}")

chk("chronology_exceptions: 24 rows",
    meta_counts.get("chronology_exceptions", 0) == 24,
    f"actual={meta_counts.get('chronology_exceptions',0)}")

# ---- 3. DuckDB stats on swap_events ----
print("\n[ SWAP EVENTS STATS (DuckDB) ]")

event_types = con.execute(f"""
    SELECT event_type, COUNT(*) as cnt
    FROM read_parquet('{SWAPS}')
    GROUP BY event_type ORDER BY cnt DESC
""").fetchdf()
print("  event_type distribution:")
print(event_types.to_string(index=False))

sync_modes = con.execute(f"""
    SELECT sync_mode, COUNT(*) as cnt
    FROM read_parquet('{SWAPS}')
    GROUP BY sync_mode ORDER BY cnt DESC
""").fetchdf()
print("\n  sync_mode distribution:")
print(sync_modes.to_string(index=False))

firmware_top = con.execute(f"""
    SELECT station_firmware, COUNT(*) as cnt
    FROM read_parquet('{SWAPS}')
    GROUP BY station_firmware ORDER BY cnt DESC
    LIMIT 8
""").fetchdf()
print("\n  station_firmware (top 8):")
print(firmware_top.to_string(index=False))

soh_check = con.execute(f"""
    SELECT
        SUM(CASE WHEN soh_in_pct  > 100 THEN 1 ELSE 0 END) as soh_in_over100,
        SUM(CASE WHEN soh_out_pct > 100 THEN 1 ELSE 0 END) as soh_out_over100,
        SUM(CASE WHEN soc_in_pct  > 100 THEN 1 ELSE 0 END) as soc_in_over100,
        SUM(CASE WHEN soc_out_pct > 100 THEN 1 ELSE 0 END) as soc_out_over100,
        SUM(CASE WHEN km_since_last_swap < 0 THEN 1 ELSE 0 END) as km_negative,
        SUM(CASE WHEN station_id LIKE 'STN-TST%' THEN 1 ELSE 0 END) as test_station_events
    FROM read_parquet('{SWAPS}')
""").fetchdf()
print("\n  Anomaly counts:")
print(soh_check.T.to_string())

chk("swap_events: no invalid timestamps",
    con.execute(f"SELECT COUNT(*) FROM read_parquet('{SWAPS}') WHERE event_ts IS NULL").fetchone()[0] == 0,
    "0 NaT")
chk("swap_events: event_date column present",
    con.execute(f"SELECT COUNT(*) FROM read_parquet('{SWAPS}') WHERE event_date IS NOT NULL LIMIT 1").fetchone()[0] > 0)

# ---- 4. DuckDB stats on station_hourly_status ----
print("\n[ STATION HOURLY STATUS STATS (DuckDB) ]")

tele_status = con.execute(f"""
    SELECT telemetry_status, COUNT(*) as cnt
    FROM read_parquet('{TELE}')
    GROUP BY telemetry_status ORDER BY cnt DESC
""").fetchdf()
print("  telemetry_status distribution:")
print(tele_status.to_string(index=False))

tele_range = con.execute(f"""
    SELECT
        MIN(hour_start) as min_ts,
        MAX(hour_start) as max_ts,
        COUNT(DISTINCT station_id) as n_stations
    FROM read_parquet('{TELE}')
""").fetchdf()
print("\n  Time range and station count:")
print(tele_range.to_string(index=False))

chk("station_hourly_status: telemetry_date column present",
    con.execute(f"SELECT COUNT(*) FROM read_parquet('{TELE}') WHERE telemetry_date IS NOT NULL LIMIT 1").fetchone()[0] > 0)

# ---- 5. DuckDB FK check: swap_events -> stations ----
print("\n[ REFERENTIAL INTEGRITY (DuckDB) ]")

unmatched_stations = con.execute(f"""
    SELECT COUNT(*) as unmatched
    FROM read_parquet('{SWAPS}') se
    WHERE se.station_id NOT IN (SELECT station_id FROM read_parquet('{STATIONS}'))
""").fetchone()[0]
chk("swap_events -> stations: no unmatched station_ids",
    unmatched_stations == 0, f"unmatched={unmatched_stations:,}")

unmatched_riders = con.execute(f"""
    SELECT COUNT(*) as unmatched
    FROM read_parquet('{SWAPS}') se
    WHERE se.rider_id NOT IN (SELECT rider_id FROM read_parquet('{RIDERS}'))
""").fetchone()[0]
chk("swap_events -> riders: no unmatched rider_ids",
    unmatched_riders == 0, f"unmatched={unmatched_riders:,}")

# ---- 6. Key uniqueness on small reference tables ----
print("\n[ KEY UNIQUENESS ]")
key_checks = [
    ("fleet_partners",    FLEET,    "partner_id"),
    ("stations",          STATIONS, "station_id"),
    ("batteries",         BATT,     "battery_id"),
    ("riders",            RIDERS,   "rider_id"),
    ("support_tickets",   TICKETS,  "ticket_id"),
]
for label, path, key in key_checks:
    n_total = meta_counts.get(label, 0)
    n_distinct = con.execute(f"SELECT COUNT(DISTINCT {key}) FROM read_parquet('{path}')").fetchone()[0]
    unique = n_total == n_distinct
    chk(f"Key unique: {label}.{key}", unique, f"total={n_total:,} distinct={n_distinct:,}")

# city_daily_context composite key
ctx_total = meta_counts.get("city_daily_context", 0)
ctx_distinct = con.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT city, date FROM read_parquet('{CDC}'))").fetchone()[0]
chk("Key unique: city_daily_context.city+date", ctx_total == ctx_distinct,
    f"total={ctx_total:,} distinct={ctx_distinct:,}")

# ---- 7. Chronology exception detail ----
print("\n[ CHRONOLOGY EXCEPTION DETAIL ]")
exc_df = con.execute(f"""
    SELECT station_id, event_date::date as event_date,
           commissioned_date::date as comm_date,
           COUNT(*) as n_events
    FROM read_parquet('{EXC}')
    GROUP BY station_id, event_date::date, commissioned_date::date
    ORDER BY station_id, event_date::date
""").fetchdf()
print("  Station-day breakdown:")
print(exc_df.to_string(index=False))

fw_check = con.execute(f"""
    SELECT DISTINCT station_firmware
    FROM read_parquet('{EXC}')
""").fetchdf()
all_v320 = (fw_check["station_firmware"] == "v3.2.0").all()
chk("Chronology exceptions: all firmware=v3.2.0", bool(all_v320), str(fw_check["station_firmware"].tolist()))

exc_in_swaps = con.execute(f"""
    SELECT COUNT(*) FROM read_parquet('{EXC}') exc
    WHERE exc.event_id IN (SELECT event_id FROM read_parquet('{SWAPS}'))
""").fetchone()[0]
chk("Chronology exceptions: all present in swap_events_clean",
    exc_in_swaps == 24, f"{exc_in_swaps}/24 found")

# ---- 8. Support tickets ----
print("\n[ SUPPORT TICKETS STATS (DuckDB) ]")
csat = con.execute(f"""
    SELECT
        resolution_status,
        COUNT(*) as total,
        SUM(CASE WHEN csat_score IS NULL THEN 1 ELSE 0 END) as csat_missing
    FROM read_parquet('{TICKETS}')
    GROUP BY resolution_status
    ORDER BY total DESC
""").fetchdf()
csat["missing_pct"] = (csat["csat_missing"] / csat["total"] * 100).round(1)
print("  CSAT missing by resolution_status:")
print(csat.to_string(index=False))

total_missing_csat = int(csat["csat_missing"].sum())
total_tickets = int(csat["total"].sum())
chk("support_tickets: CSAT missing count matches prior run (28,871)",
    total_missing_csat == 28_871, f"actual={total_missing_csat:,} of {total_tickets:,}")

# ---- 9. Summary ----
print("\n" + "=" * 70)
n_pass = sum(1 for r in val_rows if r["status"] == "PASS")
n_fail = sum(1 for r in val_rows if r["status"] == "FAIL")
print(f"VALIDATION SUMMARY: {n_pass} PASS | {n_fail} FAIL")

val_df = pd.DataFrame(val_rows)
val_df.to_csv(str(TABLES / "phase1_validation_results.csv"), index=False, encoding="utf-8")
print(f"Saved -> outputs/tables/phase1_validation_results.csv")

con.close()

if n_fail > 0:
    print("\nFAILED checks:")
    for r in val_rows:
        if r["status"] == "FAIL":
            print(f"  x {r['check']}: {r['detail']}")
    raise SystemExit(1)
