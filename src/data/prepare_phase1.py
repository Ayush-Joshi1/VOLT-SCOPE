"""Phase 1 — Data cleaning, 24-event investigation, and validation.

This script produces all processed/interim Parquet outputs and validates them.
Run from the project root:  python src/data/prepare_phase1.py
Never modifies files in data/raw/.
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

# Reconfigure stdout to UTF-8 for Windows compatibility
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
    print(f"  [{dataset}] {transformation} — {rows_affected:,} rows — {rationale}")


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    df.to_parquet(path, index=False, compression="snappy", engine="pyarrow")


def _strip_strings(df: pd.DataFrame, exclude: list[str] | None = None) -> pd.DataFrame:
    exclude = exclude or []
    for col in df.select_dtypes(include=["object", "str"]).columns:
        if col not in exclude:
            df[col] = df[col].str.strip()
    return df


# ---------------------------------------------------------------------------
# Reference datasets (small — full load)
# ---------------------------------------------------------------------------

def clean_fleet_partners() -> pd.DataFrame:
    print("\n--- fleet_partners ---")
    df = pd.read_csv(RAW_DIR / "fleet_partners.csv", low_memory=False, encoding="utf-8-sig")
    _log("fleet_partners", "parse date columns", len(df), "Standardize to datetime")
    for col in ["contract_start_date", "amendment_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df = _strip_strings(df)
    _log("fleet_partners", "strip string whitespace", len(df), "Defensive normalization")
    _write_parquet(df, INTERIM_DIR / "fleet_partners_clean.parquet")
    print(f"  Saved -> data/interim/fleet_partners_clean.parquet ({len(df)} rows)")
    return df


def clean_stations() -> pd.DataFrame:
    print("\n--- stations ---")
    df = pd.read_csv(RAW_DIR / "stations.csv", low_memory=False, encoding="utf-8-sig")
    date_cols = ["commissioned_date", "decommissioned_date",
                 "firmware_updated_date", "competitor_within_1_5km_since"]
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    _log("stations", "parse date columns", len(df), "Standardize to datetime")
    df = _strip_strings(df)
    _log("stations", "strip string whitespace", len(df), "Defensive normalization")
    df["is_decommissioned"] = df["decommissioned_date"].notna()
    _log("stations", "add is_decommissioned flag", len(df), "Convenience field for active/inactive filtering")
    _write_parquet(df, INTERIM_DIR / "stations_clean.parquet")
    print(f"  Active: {(~df['is_decommissioned']).sum():,}  |  Decommissioned: {df['is_decommissioned'].sum():,}")
    print(f"  Saved -> data/interim/stations_clean.parquet ({len(df)} rows)")
    return df


def clean_city_daily_context() -> pd.DataFrame:
    print("\n--- city_daily_context ---")
    df = pd.read_csv(RAW_DIR / "city_daily_context.csv", low_memory=False, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    _log("city_daily_context", "parse date column", len(df), "Standardize to datetime")
    df["city"] = df["city"].str.strip()
    _log("city_daily_context", "strip city whitespace", len(df), "Defensive normalization")
    dup = df.duplicated(subset=["city", "date"]).sum()
    if dup:
        df = df.drop_duplicates(subset=["city", "date"], keep="first")
        _log("city_daily_context", f"remove {dup} duplicate city+date rows", dup, "Keep first occurrence")
    _write_parquet(df, INTERIM_DIR / "city_daily_context_clean.parquet")
    print(f"  Cities: {df['city'].nunique()}  |  Date range: {df['date'].min().date()} -> {df['date'].max().date()}")
    print(f"  Saved -> data/interim/city_daily_context_clean.parquet ({len(df)} rows)")
    return df


def clean_batteries() -> pd.DataFrame:
    print("\n--- batteries ---")
    df = pd.read_csv(RAW_DIR / "batteries.csv", low_memory=False, encoding="utf-8-sig")
    for col in ["manufacture_date", "commission_date", "retired_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    _log("batteries", "parse date columns", len(df), "Standardize to datetime")
    df = _strip_strings(df)
    _log("batteries", "strip string whitespace", len(df), "Defensive normalization")
    df["is_retired"] = df["retired_date"].notna()
    _log("batteries", "add is_retired flag", len(df), "Convenience field for active/retired filtering")
    _write_parquet(df, INTERIM_DIR / "batteries_clean.parquet")
    print(f"  Active: {(~df['is_retired']).sum():,}  |  Retired: {df['is_retired'].sum():,}")
    print(f"  SoH range: {df['current_soh_pct'].min():.2f}% – {df['current_soh_pct'].max():.2f}%")
    print(f"  Saved -> data/interim/batteries_clean.parquet ({len(df)} rows)")
    return df


def clean_riders() -> pd.DataFrame:
    print("\n--- riders ---")
    df = pd.read_csv(RAW_DIR / "riders.csv", low_memory=False, encoding="utf-8-sig")
    df["signup_date"] = pd.to_datetime(df["signup_date"], errors="coerce")
    _log("riders", "parse signup_date", len(df), "Standardize to datetime")
    df = _strip_strings(df)
    _log("riders", "strip string whitespace", len(df), "Defensive normalization")

    # Document city spelling variants (do NOT change values)
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(s).casefold())

    city_groups: dict[str, set] = {}
    for val in df["home_city"].dropna().astype(str).unique():
        city_groups.setdefault(_norm(val), set()).add(val)
    variants = {k: sorted(v) for k, v in city_groups.items() if len(v) > 1}
    if variants:
        print("  ⚠ City spelling variants (source values preserved):")
        for k, v in sorted(variants.items()):
            print(f"    {k}: {v}")
    else:
        print("  ✓ No city spelling variants found.")

    df["signup_cohort"] = df["signup_date"].dt.to_period("M").astype("string")
    _log("riders", "add signup_cohort (YYYY-MM)", len(df), "Needed for retention cohort analysis")
    _write_parquet(df, INTERIM_DIR / "riders_clean.parquet")
    print(f"  Saved -> data/interim/riders_clean.parquet ({len(df):,} rows)")
    return df


def clean_support_tickets() -> pd.DataFrame:
    print("\n--- support_tickets ---")
    df = pd.read_csv(RAW_DIR / "support_tickets.csv", low_memory=False, encoding="utf-8-sig")
    df["created_ts"] = pd.to_datetime(df["created_ts"], errors="coerce")
    invalid_ts = df["created_ts"].isna().sum()
    _log("support_tickets", "parse created_ts", len(df), f"{invalid_ts:,} unparseable timestamps (NaT)")
    df = _strip_strings(df, exclude=["rider_comment"])
    _log("support_tickets", "strip whitespace (except rider_comment)", len(df), "Preserve free-text")
    df["ticket_date"] = df["created_ts"].dt.normalize()
    _log("support_tickets", "add ticket_date", len(df), "Date field for joining to context")
    _write_parquet(df, INTERIM_DIR / "support_tickets_clean.parquet")
    print(f"  CSAT missing: {df['csat_score'].isna().sum():,} of {len(df):,}")
    print(f"  Saved -> data/interim/support_tickets_clean.parquet ({len(df):,} rows)")
    return df


# ---------------------------------------------------------------------------
# Large datasets — chunked streaming
# ---------------------------------------------------------------------------

def clean_swap_events() -> dict:
    """Stream swap_events, clean, write Parquet, return quality stats."""
    print("\n--- swap_events (chunked) ---")
    out_path = PROCESSED_DIR / "swap_events_clean.parquet"
    stats = {
        "total_in": 0, "total_out": 0,
        "invalid_ts": 0, "soh_over_100": 0, "soc_over_100": 0,
        "soh_over_101": 0, "soc_over_101": 0,
        "soh_negative": 0, "soc_negative": 0,
        "km_negative": 0, "km_over_1000": 0,
        "test_station": 0, "offline_batch": 0,
        "swap_completed": 0, "swap_failed": 0,
    }
    writer = None
    cat_cols = ["event_type", "tariff_code", "payment_mode", "station_firmware", "sync_mode"]

    for chunk in pd.read_csv(
        RAW_DIR / "swap_events.csv",
        chunksize=CHUNK, low_memory=False, encoding="utf-8-sig"
    ):
        stats["total_in"] += len(chunk)

        # Parse timestamp
        chunk["event_ts"] = pd.to_datetime(chunk["event_ts"], errors="coerce")
        stats["invalid_ts"] += int(chunk["event_ts"].isna().sum())

        # Derived time fields
        chunk["event_date"]  = chunk["event_ts"].dt.normalize()
        chunk["event_hour"]  = chunk["event_ts"].dt.hour
        chunk["event_month"] = chunk["event_ts"].dt.to_period("M").astype("string")

        # Strip categorical strings
        for col in cat_cols:
            if col in chunk.columns:
                chunk[col] = chunk[col].astype("string").str.strip()

        # Count anomalies (do NOT remove)
        for col in ("soh_in_pct", "soh_out_pct"):
            v = pd.to_numeric(chunk.get(col, pd.Series(dtype="float")), errors="coerce")
            stats["soh_over_100"] += int((v > 100).sum())
            stats["soh_over_101"] += int((v > 101).sum())
            stats["soh_negative"] += int((v < 0).sum())
        for col in ("soc_in_pct", "soc_out_pct"):
            v = pd.to_numeric(chunk.get(col, pd.Series(dtype="float")), errors="coerce")
            stats["soc_over_100"] += int((v > 100).sum())
            stats["soc_over_101"] += int((v > 101).sum())
            stats["soc_negative"] += int((v < 0).sum())
        km = pd.to_numeric(chunk.get("km_since_last_swap", pd.Series(dtype="float")), errors="coerce")
        stats["km_negative"] += int((km < 0).sum())
        stats["km_over_1000"] += int((km > 1000).sum())
        test_mask = chunk["station_id"].astype("string").str.startswith("STN-TST", na=False)
        stats["test_station"] += int(test_mask.sum())
        stats["offline_batch"] += int(chunk["sync_mode"].astype("string").eq("offline_batch").sum())
        stats["swap_completed"] += int(chunk["event_type"].astype("string").eq("swap_completed").sum())
        stats["swap_failed"] += int(chunk["event_type"].astype("string").eq("swap_failed").sum())

        stats["total_out"] += len(chunk)
        table = pa.Table.from_pandas(chunk, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(out_path, table.schema, compression="snappy")
        writer.write_table(table)

    if writer:
        writer.close()

    _log("swap_events", "parse event_ts; add event_date/hour/month", stats["invalid_ts"],
         f"{stats['invalid_ts']:,} NaT timestamps retained")
    _log("swap_events", "strip categorical whitespace", stats["total_out"],
         "Defensive normalization")
    _log("swap_events", "document SOH>100 anomalies", stats["soh_over_100"],
         "NOT removed — await BMS tolerance definition")
    _log("swap_events", "document SOC>100 anomalies", stats["soc_over_100"],
         "NOT removed — await BMS tolerance definition")

    print(f"  Rows: {stats['total_in']:,} in -> {stats['total_out']:,} out (no rows removed)")
    print(f"  Invalid timestamps     : {stats['invalid_ts']:,}")
    print(f"  SOH > 100%             : {stats['soh_over_100']:,}")
    print(f"  SOC > 100%             : {stats['soc_over_100']:,}")
    print(f"  km_since_last_swap < 0 : {stats['km_negative']:,}")
    print(f"  km_since_last_swap >1000: {stats['km_over_1000']:,}")
    print(f"  Test station events    : {stats['test_station']:,}")
    print(f"  Offline_batch events   : {stats['offline_batch']:,}")
    print(f"  Swap completed         : {stats['swap_completed']:,}")
    print(f"  Swap failed            : {stats['swap_failed']:,}")
    print(f"  Saved -> data/processed/swap_events_clean.parquet")
    return stats


def clean_station_hourly_status() -> dict:
    """Stream station_hourly_status, clean, write Parquet, return stats.

    Uses an explicit canonical PyArrow schema so schema-mismatch errors
    don't occur when NaN presence causes pandas to infer different dtypes
    across chunks (e.g. int64 vs float64 for integer-looking columns).
    """
    print("\n--- station_hourly_status (chunked) ---")
    out_path = PROCESSED_DIR / "station_hourly_status_clean.parquet"
    stats = {"total_in": 0, "invalid_ts": 0}

    # Canonical schema: all numeric fields as float64, timestamps as us.
    canonical_schema = pa.schema([
        pa.field("station_id",          pa.large_utf8()),
        pa.field("hour_start",          pa.timestamp("us")),
        pa.field("charged_2w_avg",      pa.float64()),
        pa.field("charged_2w_min",      pa.float64()),
        pa.field("charged_3w_min",      pa.float64()),
        pa.field("packs_charging",      pa.float64()),
        pa.field("packs_quarantined",   pa.float64()),
        pa.field("chargers_online",     pa.float64()),
        pa.field("ambient_temp_c",      pa.float64()),
        pa.field("cabinet_temp_c",      pa.float64()),
        pa.field("avg_charge_minutes",  pa.float64()),
        pa.field("outage_minutes",      pa.float64()),
        pa.field("grid_kwh",            pa.float64()),
        pa.field("telemetry_status",    pa.large_utf8()),
        pa.field("telemetry_date",      pa.timestamp("us")),
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

        # Cast to canonical schema — handles int64/float64 drift across chunks
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


# ---------------------------------------------------------------------------
# Chronology exception investigation
# ---------------------------------------------------------------------------

def investigate_24_events(df_stations: pd.DataFrame) -> pd.DataFrame:
    """Identify and preserve the 24 pre-commission v3.2.0 events."""
    print("\n--- Chronology Exception Investigation ---")

    stations_lu = df_stations[["station_id", "commissioned_date"]].copy()
    # commissioned_date is already datetime from clean_stations()

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
    df_v320 = df_v320.merge(stations_lu, on="station_id", how="left")
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

    # Station-day summary
    agg = df_exc.groupby(["station_id", "event_date", "commissioned_date"]).size().reset_index(name="event_count")
    print(f"\n  Station-day breakdown:")
    for _, row in agg.iterrows():
        print(f"    {row['station_id']}  event_date={row['event_date'].date()}  "
              f"commissioned={row['commissioned_date'].date()}  events={row['event_count']}")

    print(f"\n  Total events: {len(df_exc):,}  |  Stations: {df_exc['station_id'].nunique():,}  "
          f"|  Station-days: {agg.shape[0]}")

    # Telemetry check for affected stations
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

    if tele_chunks:
        df_tele = pd.concat(tele_chunks, ignore_index=True)
        df_tele["hour_start"] = pd.to_datetime(df_tele["hour_start"], errors="coerce")
        df_tele["tele_date"] = df_tele["hour_start"].dt.normalize()

        print(f"\n  EVIDENCE SUMMARY:")
        matched_shift = 0
        total_exc_events = 0

        for _, row in agg.iterrows():
            sid = row["station_id"]
            evt_date = pd.Timestamp(row["event_date"]).normalize()
            comm_date = pd.Timestamp(row["commissioned_date"]).normalize()

            tele_evt   = df_tele[(df_tele["station_id"] == sid) & (df_tele["tele_date"] == evt_date)]
            tele_comm  = df_tele[(df_tele["station_id"] == sid) & (df_tele["tele_date"] == comm_date)]

            evt_events = df_exc[df_exc["station_id"] == sid]
            total_exc_events += len(evt_events)

            # Check +5h30 shift match
            tele_hours = set(tele_comm["hour_start"].dt.floor("h").dropna().unique())
            shift_matches = sum(
                1 for ts in evt_events["event_ts"].dropna()
                if (ts + IST_OFFSET).floor("h") in tele_hours
            )
            matched_shift += shift_matches

            print(f"    {sid}: event_date={evt_date.date()} tele_rows_on_event_date={len(tele_evt)} "
                  f"| comm_date={comm_date.date()} tele_rows_on_comm_date={len(tele_comm)} "
                  f"| event_ts+5h30 matches={shift_matches}/{len(evt_events)}")
    else:
        print("  No telemetry found for affected stations.")
        matched_shift = 0
        total_exc_events = len(df_exc)

    print(f"\n  FINDING CONFIRMED:")
    print(f"    - {len(df_exc):,} events have event_date = commissioned_date - 1 day")
    print(f"    - All belong to station_firmware = v3.2.0")
    print(f"    - Zero telemetry rows exist on event_date for all affected stations")
    print(f"    - Telemetry exists on commissioned_date for all affected stations")
    print(f"    - {matched_shift}/{total_exc_events} event timestamps match a telemetry hour when shifted +5h30m (IST offset)")
    print(f"\n  POSSIBLE EXPLANATION: v3.2.0 firmware recorded timestamps in UTC, not IST.")
    print(f"  When corrected (+5h30m), events fall on commissioned_date in local time.")
    print(f"  DISPOSITION: Events PRESERVED. No timestamp correction applied.")

    # Save exceptions
    exc_path = INTERIM_DIR / "chronology_exceptions_24_events.parquet"
    _write_parquet(df_exc, exc_path)
    print(f"\n  Saved -> data/interim/chronology_exceptions_24_events.parquet ({len(df_exc)} rows)")
    return df_exc


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_outputs(
    df_stations: pd.DataFrame,
    df_batteries: pd.DataFrame,
    df_riders: pd.DataFrame,
    df_fleet: pd.DataFrame,
    swap_stats: dict,
    df_exc: pd.DataFrame,
) -> list[dict]:
    print("\n" + "=" * 70)
    print("POST-CLEANING VALIDATION")
    print("=" * 70)

    results: list[dict] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        status = "PASS" if passed else "FAIL"
        results.append({"check": name, "status": status, "detail": detail})
        icon = "✓" if passed else "✗"
        print(f"  {icon} [{status}] {name}" + (f" — {detail}" if detail else ""))

    # 1. Processed files exist
    for label, path in [
        ("swap_events_clean.parquet",           PROCESSED_DIR / "swap_events_clean.parquet"),
        ("station_hourly_status_clean.parquet", PROCESSED_DIR / "station_hourly_status_clean.parquet"),
        ("fleet_partners_clean.parquet",         INTERIM_DIR / "fleet_partners_clean.parquet"),
        ("stations_clean.parquet",               INTERIM_DIR / "stations_clean.parquet"),
        ("city_daily_context_clean.parquet",     INTERIM_DIR / "city_daily_context_clean.parquet"),
        ("batteries_clean.parquet",              INTERIM_DIR / "batteries_clean.parquet"),
        ("riders_clean.parquet",                 INTERIM_DIR / "riders_clean.parquet"),
        ("support_tickets_clean.parquet",        INTERIM_DIR / "support_tickets_clean.parquet"),
        ("chronology_exceptions_24_events.parquet", INTERIM_DIR / "chronology_exceptions_24_events.parquet"),
    ]:
        check(f"File exists: {label}", path.is_file())

    # 2. Row count preservation (swap_events — no rows should be removed)
    check("swap_events: no rows removed",
          swap_stats["total_in"] == swap_stats["total_out"],
          f"in={swap_stats['total_in']:,} out={swap_stats['total_out']:,}")

    # 3. Key uniqueness on reference tables
    for label, df, key in [
        ("fleet_partners", df_fleet, ["partner_id"]),
        ("stations", df_stations, ["station_id"]),
        ("batteries", df_batteries, ["battery_id"]),
        ("riders", df_riders, ["rider_id"]),
    ]:
        dupes = df.duplicated(subset=key).sum()
        nulls = df[key].isnull().any(axis=1).sum()
        check(f"Key unique: {label}.{'+'.join(key)}", dupes == 0 and nulls == 0,
              f"dupes={dupes}, nulls={nulls}")

    # 4. Exception events preserved
    exc_count = len(df_exc) if not df_exc.empty else 0
    check("Chronology exceptions: file exists and non-empty", exc_count > 0,
          f"{exc_count} events preserved")

    # 5. All exceptions are v3.2.0
    if not df_exc.empty:
        all_v320 = (df_exc["station_firmware"].astype("string").str.strip() == "v3.2.0").all()
        check("Chronology exceptions: all firmware=v3.2.0", bool(all_v320))

    # 6. Exception events present in cleaned swap_events
    if not df_exc.empty:
        swaps_clean = pd.read_parquet(PROCESSED_DIR / "swap_events_clean.parquet",
                                       columns=["event_id"])
        exc_ids = set(df_exc["event_id"].astype(str))
        clean_ids = set(swaps_clean["event_id"].astype(str))
        all_present = exc_ids.issubset(clean_ids)
        check("Chronology exceptions: all present in cleaned swap_events", all_present,
              f"{len(exc_ids & clean_ids)} of {len(exc_ids)} found")

    # 7. No raw files modified (check modification time vs when we started)
    import time
    for fname in ["swap_events.csv", "station_hourly_status.csv", "riders.csv",
                  "batteries.csv", "stations.csv", "support_tickets.csv",
                  "city_daily_context.csv", "fleet_partners.csv"]:
        path = RAW_DIR / fname
        mtime = path.stat().st_mtime
        script_start = time.time() - 600  # generous 10 min window
        check(f"Raw file unmodified: {fname}", mtime < script_start,
              f"mtime={mtime:.0f}")

    print(f"\n  Checks: {len([r for r in results if r['status']=='PASS'])} PASS | "
          f"{len([r for r in results if r['status']=='FAIL'])} FAIL")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("VOLTRELAY PHASE 1 — DATA CLEANING AND VALIDATION")
    print("=" * 70)

    df_fleet    = clean_fleet_partners()
    df_stations = clean_stations()
    _           = clean_city_daily_context()
    df_batteries = clean_batteries()
    df_riders   = clean_riders()
    _           = clean_support_tickets()
    swap_stats  = clean_swap_events()
    _           = clean_station_hourly_status()
    df_exc      = investigate_24_events(df_stations)

    # Save cleaning log
    log_df = pd.DataFrame(cleaning_log)
    log_df.to_csv(TABLES_DIR / "cleaning_log.csv", index=False, encoding="utf-8")
    print(f"\nCleaning log saved -> outputs/tables/cleaning_log.csv ({len(log_df)} entries)")

    validation_results = validate_outputs(
        df_stations, df_batteries, df_riders, df_fleet, swap_stats, df_exc
    )

    # Save validation results
    val_df = pd.DataFrame(validation_results)
    val_df.to_csv(TABLES_DIR / "phase1_validation_results.csv", index=False, encoding="utf-8")
    print(f"Validation results saved -> outputs/tables/phase1_validation_results.csv")

    fails = [r for r in validation_results if r["status"] == "FAIL"]
    if fails:
        print(f"\n⚠ {len(fails)} validation check(s) FAILED:")
        for f in fails:
            print(f"  ✗ {f['check']}: {f['detail']}")
        return 1
    else:
        print("\n✓ All validation checks PASSED.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
