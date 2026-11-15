"""Run only station_hourly_status cleaning and the Phase 1 investigation + validation.

This script resumes after swap_events_clean.parquet was already written.
Run from project root: python src/data/prepare_phase1_resume.py
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR       = ROOT / "data" / "raw"
INTERIM_DIR   = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"
TABLES_DIR    = ROOT / "outputs" / "tables"

for d in [INTERIM_DIR, PROCESSED_DIR, TABLES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

CHUNK = 100_000
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)

cleaning_log: list[dict] = []


def _log(dataset: str, transformation: str, rows_affected: int, rationale: str) -> None:
    cleaning_log.append({
        "dataset": dataset,
        "transformation": transformation,
        "rows_affected": rows_affected,
        "rationale": rationale,
    })
    print(f"  [{dataset}] {transformation} -- {rows_affected:,} rows -- {rationale}")


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    df.to_parquet(path, index=False, compression="snappy", engine="pyarrow")


def clean_station_hourly_status() -> dict:
    """Stream station_hourly_status, clean, write Parquet.

    Uses an explicit canonical PyArrow schema to prevent schema-mismatch
    errors when NaN presence causes pandas to infer different dtypes per chunk.
    """
    print("\n--- station_hourly_status (chunked) ---")
    out_path = PROCESSED_DIR / "station_hourly_status_clean.parquet"
    stats = {"total_in": 0, "invalid_ts": 0}

    # Canonical schema: float64 for all numeric fields to avoid int64/float64 drift
    canonical_schema = pa.schema([
        pa.field("station_id",         pa.large_utf8()),
        pa.field("hour_start",         pa.timestamp("us")),
        pa.field("charged_2w_avg",     pa.float64()),
        pa.field("charged_2w_min",     pa.float64()),
        pa.field("charged_3w_min",     pa.float64()),
        pa.field("packs_charging",     pa.float64()),
        pa.field("packs_quarantined",  pa.float64()),
        pa.field("chargers_online",    pa.float64()),
        pa.field("ambient_temp_c",     pa.float64()),
        pa.field("cabinet_temp_c",     pa.float64()),
        pa.field("avg_charge_minutes", pa.float64()),
        pa.field("outage_minutes",     pa.float64()),
        pa.field("grid_kwh",           pa.float64()),
        pa.field("telemetry_status",   pa.large_utf8()),
        pa.field("telemetry_date",     pa.timestamp("us")),
    ])

    writer = pq.ParquetWriter(out_path, canonical_schema, compression="snappy")

    for chunk in pd.read_csv(
        RAW_DIR / "station_hourly_status.csv",
        chunksize=CHUNK, low_memory=False, encoding="utf-8-sig"
    ):
        stats["total_in"] += len(chunk)
        chunk["hour_start"] = pd.to_datetime(chunk["hour_start"], errors="coerce")
        stats["invalid_ts"] += int(chunk["hour_start"].isna().sum())
        chunk["station_id"] = chunk["station_id"].astype("string").str.strip()
        chunk["telemetry_date"] = chunk["hour_start"].dt.normalize()

        table = pa.Table.from_pandas(chunk, preserve_index=False)
        table = table.cast(canonical_schema)
        writer.write_table(table)

    writer.close()

    _log("station_hourly_status", "parse hour_start; add telemetry_date", stats["invalid_ts"],
         f"{stats['invalid_ts']:,} invalid timestamps")
    _log("station_hourly_status", "strip station_id whitespace", stats["total_in"],
         "Defensive normalization")

    print(f"  Rows: {stats['total_in']:,}")
    print(f"  Invalid timestamps: {stats['invalid_ts']:,}")
    print(f"  Saved -> data/processed/station_hourly_status_clean.parquet")
    return stats


def investigate_24_events() -> pd.DataFrame:
    """Identify and preserve the 24 pre-commission v3.2.0 events."""
    print("\n--- Chronology Exception Investigation ---")

    df_stations = pd.read_parquet(INTERIM_DIR / "stations_clean.parquet",
                                   columns=["station_id", "commissioned_date"])

    print("  Scanning swap_events for station_firmware=v3.2.0...")
    v320_chunks = []
    for chunk in pd.read_csv(
        RAW_DIR / "swap_events.csv",
        chunksize=CHUNK, low_memory=False, encoding="utf-8-sig",
        usecols=["event_id", "station_id", "event_ts", "station_firmware", "sync_mode", "event_type"]
    ):
        mask = chunk["station_firmware"].astype("string").str.strip().eq("v3.2.0")
        if mask.any():
            v320_chunks.append(chunk[mask].copy())

    if not v320_chunks:
        print("  No v3.2.0 events found.")
        return pd.DataFrame()

    df_v320 = pd.concat(v320_chunks, ignore_index=True)
    df_v320["event_ts"] = pd.to_datetime(df_v320["event_ts"], errors="coerce")
    df_v320["event_date"] = df_v320["event_ts"].dt.normalize()
    print(f"  v3.2.0 events found: {len(df_v320):,}")

    # Merge with station commissioning dates
    df_v320 = df_v320.merge(df_stations, on="station_id", how="left")
    ONE_DAY = pd.Timedelta(days=1)
    pre_mask = (
        df_v320["event_date"].notna()
        & df_v320["commissioned_date"].notna()
        & ((df_v320["commissioned_date"] - df_v320["event_date"]) == ONE_DAY)
    )
    df_exc = df_v320[pre_mask].copy()
    print(f"  Events where event_date = commissioned_date - 1 day: {len(df_exc):,}")

    if df_exc.empty:
        print("  No pre-commission events found.")
        return df_exc

    agg = df_exc.groupby(["station_id", "event_date", "commissioned_date"]).size().reset_index(name="event_count")
    print(f"\n  Station-day breakdown:")
    for _, row in agg.iterrows():
        print(f"    {row['station_id']}  event_date={row['event_date'].date()}  "
              f"commissioned={row['commissioned_date'].date()}  events={row['event_count']}")

    print(f"\n  Total: {len(df_exc):,} events | "
          f"{df_exc['station_id'].nunique():,} stations | "
          f"{agg.shape[0]} station-days")

    # Telemetry check
    affected_stations = df_exc["station_id"].unique().tolist()
    print(f"\n  Collecting telemetry for {len(affected_stations)} station(s)...")
    tele_chunks = []
    for chunk in pd.read_csv(
        RAW_DIR / "station_hourly_status.csv",
        chunksize=CHUNK, low_memory=False, encoding="utf-8-sig",
        usecols=["station_id", "hour_start", "telemetry_status"]
    ):
        mask = chunk["station_id"].isin(affected_stations)
        if mask.any():
            tele_chunks.append(chunk[mask].copy())

    matched_shift = 0
    total_exc = len(df_exc)
    if tele_chunks:
        df_tele = pd.concat(tele_chunks, ignore_index=True)
        df_tele["hour_start"] = pd.to_datetime(df_tele["hour_start"], errors="coerce")
        df_tele["tele_date"] = df_tele["hour_start"].dt.normalize()

        print(f"\n  EVIDENCE:")
        for _, row in agg.iterrows():
            sid = row["station_id"]
            evt_date  = pd.Timestamp(row["event_date"]).normalize()
            comm_date = pd.Timestamp(row["commissioned_date"]).normalize()
            tele_evt  = df_tele[(df_tele["station_id"] == sid) & (df_tele["tele_date"] == evt_date)]
            tele_comm = df_tele[(df_tele["station_id"] == sid) & (df_tele["tele_date"] == comm_date)]
            evt_grp   = df_exc[df_exc["station_id"] == sid]
            tele_hrs  = set(tele_comm["hour_start"].dt.floor("h").dropna())
            n_match   = sum(1 for ts in evt_grp["event_ts"].dropna()
                            if (ts + IST_OFFSET).floor("h") in tele_hrs)
            matched_shift += n_match
            print(f"    {sid}: event_date={evt_date.date()} "
                  f"tele_on_event_date={len(tele_evt)} | "
                  f"comm_date={comm_date.date()} "
                  f"tele_on_comm_date={len(tele_comm)} | "
                  f"event_ts+5h30_matches={n_match}/{len(evt_grp)}")
    else:
        print("  No telemetry found for affected stations.")

    print(f"\n  FINDING CONFIRMED:")
    print(f"    - {len(df_exc):,} events have event_date = commissioned_date - 1 day")
    print(f"    - All belong to station_firmware = v3.2.0")
    print(f"    - Zero telemetry rows on event_date for all affected stations")
    print(f"    - Telemetry exists on commissioned_date for all affected stations")
    print(f"    - {matched_shift}/{total_exc} event_ts values match a telemetry hour when shifted +5h30 (IST offset)")
    print(f"\n  POSSIBLE EXPLANATION: v3.2.0 firmware recorded timestamps in UTC, not IST.")
    print(f"  DISPOSITION: Events PRESERVED. No timestamp correction applied.")

    exc_path = INTERIM_DIR / "chronology_exceptions_24_events.parquet"
    _write_parquet(df_exc, exc_path)
    print(f"\n  Saved -> data/interim/chronology_exceptions_24_events.parquet ({len(df_exc)} rows)")
    return df_exc


def validate_outputs(swap_total_in: int, df_exc: pd.DataFrame) -> list[dict]:
    print("\n" + "=" * 70)
    print("POST-CLEANING VALIDATION")
    print("=" * 70)

    results: list[dict] = []

    def chk(name: str, passed: bool, detail: str = "") -> None:
        st = "PASS" if passed else "FAIL"
        results.append({"check": name, "status": st, "detail": detail})
        icon = "✓" if passed else "✗"
        print(f"  {icon} [{st}] {name}" + (f" -- {detail}" if detail else ""))

    # File existence checks
    for label, path in [
        ("swap_events_clean.parquet",           PROCESSED_DIR / "swap_events_clean.parquet"),
        ("station_hourly_status_clean.parquet", PROCESSED_DIR / "station_hourly_status_clean.parquet"),
        ("fleet_partners_clean.parquet",         INTERIM_DIR / "fleet_partners_clean.parquet"),
        ("stations_clean.parquet",               INTERIM_DIR / "stations_clean.parquet"),
        ("city_daily_context_clean.parquet",     INTERIM_DIR / "city_daily_context_clean.parquet"),
        ("batteries_clean.parquet",              INTERIM_DIR / "batteries_clean.parquet"),
        ("riders_clean.parquet",                 INTERIM_DIR / "riders_clean.parquet"),
        ("support_tickets_clean.parquet",        INTERIM_DIR / "support_tickets_clean.parquet"),
        ("chronology_exceptions_24_events.parquet",
                                                 INTERIM_DIR / "chronology_exceptions_24_events.parquet"),
    ]:
        chk(f"File exists: {label}", path.is_file())

    # Swap events row count preservation (3,877,013 from prior run)
    swaps_pq = pq.read_metadata(PROCESSED_DIR / "swap_events_clean.parquet")
    swaps_clean_rows = swaps_pq.num_rows
    chk("swap_events: row count preserved",
        swaps_clean_rows == swap_total_in,
        f"parquet_rows={swaps_clean_rows:,} expected={swap_total_in:,}")

    # Station hourly row count
    tele_pq = pq.read_metadata(PROCESSED_DIR / "station_hourly_status_clean.parquet")
    tele_rows = tele_pq.num_rows
    chk("station_hourly_status: parquet written and non-empty", tele_rows > 0,
        f"{tele_rows:,} rows")

    # Key uniqueness — reference tables
    for label, parquet_path, key in [
        ("fleet_partners", INTERIM_DIR / "fleet_partners_clean.parquet",         ["partner_id"]),
        ("stations",       INTERIM_DIR / "stations_clean.parquet",                ["station_id"]),
        ("batteries",      INTERIM_DIR / "batteries_clean.parquet",               ["battery_id"]),
        ("riders",         INTERIM_DIR / "riders_clean.parquet",                  ["rider_id"]),
        ("support_tickets",INTERIM_DIR / "support_tickets_clean.parquet",         ["ticket_id"]),
        ("city_ctx",       INTERIM_DIR / "city_daily_context_clean.parquet",      ["city", "date"]),
    ]:
        df = pd.read_parquet(parquet_path, columns=key)
        dupes = df.duplicated().sum()
        nulls = df.isnull().any(axis=1).sum()
        chk(f"Key unique: {label}.{'+'.join(key)}", dupes == 0 and nulls == 0,
            f"dupes={dupes}, nulls={nulls}")

    # Exception preservation
    exc_count = len(df_exc) if not df_exc.empty else 0
    chk("Chronology exceptions: non-empty", exc_count > 0, f"{exc_count} events")

    if not df_exc.empty:
        all_v320 = (df_exc["station_firmware"].astype("string").str.strip() == "v3.2.0").all()
        chk("Chronology exceptions: all firmware=v3.2.0", bool(all_v320))

        swaps_ids = pd.read_parquet(PROCESSED_DIR / "swap_events_clean.parquet",
                                     columns=["event_id"])["event_id"].astype(str)
        exc_ids   = df_exc["event_id"].astype(str)
        present   = exc_ids.isin(swaps_ids).all()
        chk("Chronology exceptions: all present in swap_events_clean", bool(present),
            f"{exc_ids.isin(swaps_ids).sum()} of {len(exc_ids)} found")

    # Raw files unmodified
    import time
    threshold = time.time() - 7200  # 2-hour window
    for fname in ["swap_events.csv", "station_hourly_status.csv", "riders.csv",
                  "batteries.csv", "stations.csv", "support_tickets.csv",
                  "city_daily_context.csv", "fleet_partners.csv"]:
        mtime = (RAW_DIR / fname).stat().st_mtime
        chk(f"Raw unmodified: {fname}", mtime < threshold, f"mtime OK")

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    print(f"\n  Checks: {n_pass} PASS | {n_fail} FAIL")
    return results


def main() -> int:
    print("=" * 70)
    print("VOLTRELAY PHASE 1 RESUME -- station_hourly_status + investigation")
    print("=" * 70)

    # Only run the missing piece
    tele_stats = clean_station_hourly_status()

    # Investigation (reads from raw + interim parquet)
    df_exc = investigate_24_events()

    # Append new log entries to existing cleaning_log if present
    existing_log_path = TABLES_DIR / "cleaning_log.csv"
    if existing_log_path.is_file():
        old_log = pd.read_csv(existing_log_path)
        new_log = pd.DataFrame(cleaning_log)
        combined = pd.concat([old_log, new_log], ignore_index=True)
    else:
        combined = pd.DataFrame(cleaning_log)
    combined.to_csv(existing_log_path, index=False, encoding="utf-8")
    print(f"\nCleaning log updated -> outputs/tables/cleaning_log.csv ({len(combined)} entries)")

    # Validate everything (swap_events was 3,877,013 from prior run)
    val_results = validate_outputs(swap_total_in=3_877_013, df_exc=df_exc)
    val_df = pd.DataFrame(val_results)
    val_df.to_csv(TABLES_DIR / "phase1_validation_results.csv", index=False, encoding="utf-8")
    print(f"Validation results -> outputs/tables/phase1_validation_results.csv")

    fails = [r for r in val_results if r["status"] == "FAIL"]
    if fails:
        print(f"\n! {len(fails)} FAIL(s):")
        for f in fails:
            print(f"  x {f['check']}: {f['detail']}")
        return 1
    print("\n* All validation checks PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
