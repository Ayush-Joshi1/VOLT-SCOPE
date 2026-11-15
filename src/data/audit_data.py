"""Chunked, read-only audit for the VOLT SCOPE raw CSV datasets."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
TABLE_DIR = ROOT / "outputs" / "tables"
REPORT_PATH = ROOT / "reports" / "stage_01_data_audit.md"
CHUNK_SIZE = 100_000
SAMPLE_LIMIT = 20_000

SPECS: dict[str, dict[str, Any]] = {
    "swap_events.csv": {
        "key": ["event_id"],
        "dates": ["event_ts"],
        "categories": ["event_type", "tariff_code", "payment_mode", "station_firmware", "sync_mode"],
    },
    "station_hourly_status.csv": {
        "key": ["station_id", "hour_start"],
        "dates": ["hour_start"],
        "categories": ["telemetry_status"],
    },
    "riders.csv": {
        "key": ["rider_id"],
        "dates": ["signup_date"],
        "categories": ["vehicle_class", "vehicle_model", "signup_channel", "plan_type", "declared_shift", "age_band", "kyc_verified", "home_city"],
    },
    "batteries.csv": {
        "key": ["battery_id"],
        "dates": ["manufacture_date", "commission_date", "retired_date"],
        "categories": ["pack_type", "supplier", "manufacturing_lot", "bms_firmware", "retirement_reason"],
    },
    "stations.csv": {
        "key": ["station_id"],
        "dates": ["commissioned_date", "decommissioned_date", "firmware_updated_date", "competitor_within_1_5km_since"],
        "categories": ["city", "zone", "location_type", "host_type", "expansion_wave", "charger_generation", "connectivity_tier", "firmware_version"],
    },
    "support_tickets.csv": {
        "key": ["ticket_id"],
        "dates": ["created_ts"],
        "categories": ["channel", "category", "priority", "resolution_status"],
    },
    "city_daily_context.csv": {
        "key": ["city", "date"],
        "dates": ["date"],
        "categories": ["city", "heat_alert", "flood_disruption", "festival_or_event", "is_public_holiday", "competitor_promo_active", "ecommerce_sale_event"],
    },
    "fleet_partners.csv": {
        "key": ["partner_id"],
        "dates": ["contract_start_date", "amendment_date"],
        "categories": ["partner_name", "partner_segment", "vehicle_class", "contract_type", "peak_surcharge_billable", "cities_active"],
    },
}

EXPECTED_ORDER = [
    "batteries.csv",
    "city_daily_context.csv",
    "fleet_partners.csv",
    "riders.csv",
    "stations.csv",
    "support_tickets.csv",
    "station_hourly_status.csv",
    "swap_events.csv",
]


@dataclass
class DatasetResult:
    filename: str
    columns: list[str] = field(default_factory=list)
    rows: int = 0
    dtypes: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    missing: Counter[str] = field(default_factory=Counter)
    dates: dict[str, dict[str, Any]] = field(default_factory=dict)
    numeric: dict[str, dict[str, Any]] = field(default_factory=dict)
    categories: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    null_key_rows: int = 0
    duplicate_key_rows: int = 0
    duplicate_full_rows: int = 0


class AuditError(RuntimeError):
    """Raised when an input is unreadable or has an unusable audit schema."""


def _insert_hashes(connection: sqlite3.Connection, table: str, values: np.ndarray) -> int:
    """Insert hash fingerprints and return how many values were not seen before."""
    before = connection.total_changes
    connection.executemany(
        f"INSERT OR IGNORE INTO {table}(fingerprint) VALUES (?)",
        ((int(value),) for value in values),
    )
    return connection.total_changes - before


def _as_text_set(path: Path, column: str) -> set[str]:
    try:
        frame = pd.read_csv(path, usecols=[column], dtype="string")
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        raise AuditError(f"Could not load reference column {column!r} from {path}: {exc}") from exc
    return set(frame[column].dropna().astype(str))


def _load_references(raw_dir: Path) -> dict[str, Any]:
    references: dict[str, Any] = {}
    for filename, column, name in [
        ("riders.csv", "rider_id", "riders"),
        ("stations.csv", "station_id", "stations"),
        ("batteries.csv", "battery_id", "batteries"),
        ("fleet_partners.csv", "partner_id", "partners"),
    ]:
        references[name] = _as_text_set(raw_dir / filename, column)

    try:
        riders = pd.read_csv(raw_dir / "riders.csv", usecols=["rider_id", "partner_id"], dtype="string")
        stations = pd.read_csv(raw_dir / "stations.csv", usecols=["station_id", "city"], dtype="string")
        context = pd.read_csv(raw_dir / "city_daily_context.csv", usecols=["city", "date"], dtype="string")
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        raise AuditError(f"Could not load relationship reference data: {exc}") from exc

    references["rider_partner"] = dict(zip(riders["rider_id"].dropna(), riders["partner_id"].fillna("")))
    references["station_city"] = dict(zip(stations["station_id"].dropna(), stations["city"].fillna("")))
    references["context_cities"] = set(context["city"].dropna().astype(str))
    references["context_city_dates"] = set(
        zip(context["city"].fillna("").astype(str), context["date"].fillna("").astype(str))
    )
    return references


def _init_database(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("CREATE TABLE telemetry_hours (station_id TEXT, hour_epoch INTEGER, PRIMARY KEY(station_id, hour_epoch))")
    connection.execute(
        """CREATE TABLE event_pairs (
            row_id INTEGER PRIMARY KEY,
            event_id TEXT,
            rider_id TEXT,
            station_id TEXT,
            event_type TEXT,
            battery_in_id TEXT,
            battery_out_id TEXT,
            sync_mode TEXT,
            ts_epoch INTEGER
        )"""
    )
    connection.execute(
        """CREATE TABLE firmware_events (
            event_id TEXT,
            station_id TEXT,
            ts_epoch INTEGER
        )"""
    )


def _numeric_state() -> dict[str, Any]:
    return {"count": 0, "sum": 0.0, "min": None, "max": None, "sample": np.array([], dtype="float64")}


def _sample_numeric(state: dict[str, Any], values: pd.Series) -> None:
    vals = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype="float64", copy=False)
    if not len(vals):
        return
    state["count"] += int(len(vals))
    state["sum"] += float(vals.sum())
    vmin, vmax = float(vals.min()), float(vals.max())
    state["min"] = vmin if state["min"] is None else min(state["min"], vmin)
    state["max"] = vmax if state["max"] is None else max(state["max"], vmax)
    # Equal-sized chunk samples provide a bounded, reproducible approximation of percentiles.
    sample = vals if len(vals) <= 2_000 else pd.Series(vals).sample(n=2_000, random_state=17).to_numpy()
    merged = np.concatenate((state["sample"], sample))
    if len(merged) > SAMPLE_LIMIT:
        merged = pd.Series(merged).sample(n=SAMPLE_LIMIT, random_state=17).to_numpy()
    state["sample"] = merged


def _record_dates(result: DatasetResult, column: str, values: pd.Series) -> None:
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    valid = parsed.dropna()
    state = result.dates.setdefault(column, {"minimum": None, "maximum": None, "invalid_nonempty": 0})
    state["invalid_nonempty"] += int((values.notna() & parsed.isna()).sum())
    if not valid.empty:
        low, high = valid.min().isoformat(sep=" "), valid.max().isoformat(sep=" ")
        state["minimum"] = low if state["minimum"] is None or low < state["minimum"] else state["minimum"]
        state["maximum"] = high if state["maximum"] is None or high > state["maximum"] else state["maximum"]


def _track_foreign_key(
    stats: dict[str, dict[str, Any]], relation: str, values: pd.Series, allowed: set[str], nullable: bool = False
) -> None:
    nonnull = values.dropna().astype(str)
    if nullable:
        nonnull = nonnull[nonnull != ""]
    entry = stats.setdefault(relation, {"rows_checked": 0, "unmatched_rows": 0, "unmatched_ids": set()})
    entry["rows_checked"] += int(len(nonnull))
    invalid = nonnull[~nonnull.isin(allowed)]
    entry["unmatched_rows"] += int(len(invalid))
    entry["unmatched_ids"].update(invalid.unique().tolist())


def _city_normalization(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def audit_dataset(
    filename: str,
    raw_dir: Path,
    connection: sqlite3.Connection,
    references: dict[str, Any],
    fk_stats: dict[str, dict[str, Any]],
    quality: Counter[str],
    extra: dict[str, Any],
    chunk_size: int,
) -> DatasetResult:
    path = raw_dir / filename
    if not path.is_file():
        raise AuditError(f"Required dataset not found: {path}")
    result = DatasetResult(filename)
    spec = SPECS[filename]
    full_hash_table = f"full_{EXPECTED_ORDER.index(filename)}"
    key_hash_table = f"key_{EXPECTED_ORDER.index(filename)}"
    connection.execute(f"CREATE TABLE {full_hash_table} (fingerprint INTEGER PRIMARY KEY)")
    connection.execute(f"CREATE TABLE {key_hash_table} (fingerprint INTEGER PRIMARY KEY)")

    try:
        chunks = pd.read_csv(path, chunksize=chunk_size, low_memory=False, encoding="utf-8-sig")
        for frame in chunks:
            if not result.columns:
                result.columns = list(frame.columns)
                absent = [column for column in spec["key"] if column not in frame.columns]
                if absent:
                    raise AuditError(f"{filename} is missing expected key column(s): {', '.join(absent)}")
            result.rows += len(frame)
            for col in frame.columns:
                result.dtypes[col].add(str(frame[col].dtype))
                result.missing[col] += int(frame[col].isna().sum())
            for col in spec["dates"]:
                if col in frame:
                    _record_dates(result, col, frame[col])
            for col in spec["categories"]:
                if col in frame:
                    result.categories[col].update(frame[col].dropna().astype(str).value_counts().to_dict())

            for col in frame.select_dtypes(include="number").columns:
                state = result.numeric.setdefault(col, _numeric_state())
                _sample_numeric(state, frame[col])

            row_hashes = pd.util.hash_pandas_object(frame, index=False).to_numpy(dtype="uint64").view("int64")
            new_full = _insert_hashes(connection, full_hash_table, row_hashes)
            result.duplicate_full_rows += len(frame) - new_full

            key_frame = frame[spec["key"]]
            key_missing = key_frame.isna().any(axis=1)
            result.null_key_rows += int(key_missing.sum())
            valid_keys = key_frame.loc[~key_missing].astype("string")
            if len(valid_keys):
                key_hashes = pd.util.hash_pandas_object(valid_keys, index=False).to_numpy(dtype="uint64").view("int64")
                new_keys = _insert_hashes(connection, key_hash_table, key_hashes)
                result.duplicate_key_rows += len(valid_keys) - new_keys

            if filename == "station_hourly_status.csv":
                station = frame["station_id"].astype("string")
                _track_foreign_key(fk_stats, "station_hourly_status.station_id -> stations.station_id", station, references["stations"])
                parsed = pd.to_datetime(frame["hour_start"], errors="coerce", format="mixed")
                valid = station.notna() & parsed.notna()
                epochs = (parsed[valid].astype("int64") // 1_000_000_000 // 3600 * 3600).astype("int64")
                connection.executemany(
                    "INSERT OR IGNORE INTO telemetry_hours(station_id, hour_epoch) VALUES (?, ?)",
                    zip(station[valid].astype(str), epochs),
                )
                quality["telemetry_timestamp_invalid"] += int(parsed.isna().sum())

            elif filename == "swap_events.csv":
                for col, allowed, relation in [
                    ("rider_id", references["riders"], "swap_events.rider_id -> riders.rider_id"),
                    ("station_id", references["stations"], "swap_events.station_id -> stations.station_id"),
                    ("battery_in_id", references["batteries"], "swap_events.battery_in_id -> batteries.battery_id"),
                    ("battery_out_id", references["batteries"], "swap_events.battery_out_id -> batteries.battery_id"),
                ]:
                    _track_foreign_key(fk_stats, relation, frame[col], allowed, nullable=col.startswith("battery_"))
                partners = frame["rider_id"].astype("string").map(references["rider_partner"])
                _track_foreign_key(
                    fk_stats,
                    "swap_events.rider_id -> riders.partner_id -> fleet_partners.partner_id",
                    partners,
                    references["partners"],
                    nullable=True,
                )

                event_ts = pd.to_datetime(frame["event_ts"], errors="coerce", format="mixed")
                quality["swap_timestamp_invalid"] += int(event_ts.isna().sum())
                epochs = pd.Series(pd.NA, index=frame.index, dtype="Int64")
                valid_ts = event_ts.notna()
                epochs.loc[valid_ts] = event_ts.loc[valid_ts].astype("int64") // 1_000_000_000
                near = frame[["event_id", "rider_id", "station_id", "event_type", "battery_in_id", "battery_out_id", "sync_mode"]].copy()
                near["ts_epoch"] = epochs
                near = near.loc[valid_ts]
                connection.executemany(
                    """INSERT INTO event_pairs(event_id, rider_id, station_id, event_type, battery_in_id,
                       battery_out_id, sync_mode, ts_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tuple("" if pd.isna(value) else str(value) for value in row[:7]) + (int(row[7]),)
                        for row in near.itertuples(index=False, name=None)
                    ),
                )

                completed = frame["event_type"].astype("string").eq("swap_completed")
                quality["swap_completed_rows"] += int(completed.sum())
                quality["swap_event_rows"] += len(frame)
                sync = frame["sync_mode"].astype("string")
                quality["offline_batch_rows"] += int(sync.eq("offline_batch").sum())

                fw = frame["station_firmware"].astype("string").eq("v3.2.0")
                in_dates = event_ts.ge(pd.Timestamp("2025-03-10")) & event_ts.lt(pd.Timestamp("2025-04-15"))
                affected = fw & in_dates & event_ts.notna()
                quality["firmware_window_rows"] += int(affected.sum())
                candidate = pd.DataFrame({
                    "event_id": frame.loc[affected, "event_id"].astype("string"),
                    "station_id": frame.loc[affected, "station_id"].astype("string"),
                    "ts_epoch": (event_ts.loc[affected].astype("int64") // 1_000_000_000).astype("int64"),
                })
                connection.executemany(
                    "INSERT INTO firmware_events(event_id, station_id, ts_epoch) VALUES (?, ?, ?)",
                    ((str(row.event_id), str(row.station_id), int(row.ts_epoch)) for row in candidate.itertuples(index=False)),
                )

                # Screen ranges without altering source values; thresholds are triage markers only.
                for col in ("soc_in_pct", "soc_out_pct", "soh_in_pct", "soh_out_pct"):
                    values = pd.to_numeric(frame[col], errors="coerce")
                    quality[f"{col}_over_100"] += int((values > 100).sum())
                    quality[f"{col}_over_101"] += int((values > 101).sum())
                    quality[f"{col}_negative"] += int((values < 0).sum())
                distance = pd.to_numeric(frame["km_since_last_swap"], errors="coerce")
                quality["km_negative"] += int((distance < 0).sum())
                quality["km_over_1000_screen"] += int((distance > 1000).sum())
                test_station = frame["station_id"].astype("string").str.startswith("STN-TST", na=False)
                amount = pd.to_numeric(frame["amount_charged_inr"], errors="coerce")
                quality["test_station_events"] += int(test_station.sum())
                quality["test_station_zero_amount"] += int((test_station & amount.eq(0)).sum())

            elif filename == "support_tickets.csv":
                for col, allowed, relation in [
                    ("rider_id", references["riders"], "support_tickets.rider_id -> riders.rider_id"),
                    ("station_id", references["stations"], "support_tickets.station_id -> stations.station_id"),
                    ("battery_id", references["batteries"], "support_tickets.battery_id -> batteries.battery_id"),
                ]:
                    _track_foreign_key(fk_stats, relation, frame[col], allowed, nullable=True)
                csat_missing = frame["csat_score"].isna() | frame["csat_score"].astype("string").eq("")
                quality["ticket_rows"] += len(frame)
                quality["ticket_csat_missing"] += int(csat_missing.sum())
                for status, group in frame.assign(_csat_missing=csat_missing).groupby("resolution_status", dropna=False):
                    label = "<missing>" if pd.isna(status) else str(status)
                    extra.setdefault("csat_by_resolution", {}).setdefault(label, {"tickets": 0, "csat_missing": 0})
                    extra["csat_by_resolution"][label]["tickets"] += int(len(group))
                    extra["csat_by_resolution"][label]["csat_missing"] += int(group["_csat_missing"].sum())

            elif filename == "riders.csv":
                normalized: dict[str, set[str]] = extra.setdefault("city_spellings", defaultdict(set))
                for value in frame["home_city"].dropna().astype(str).unique():
                    normalized[_city_normalization(value)].add(value)

            elif filename == "batteries.csv":
                retired = frame["retired_date"].isna() | frame["retired_date"].astype("string").eq("")
                quality["batteries_total"] += len(frame)
                quality["batteries_missing_retired_date"] += int(retired.sum())
                quality["batteries_missing_retirement_reason"] += int(
                    (frame["retirement_reason"].isna() | frame["retirement_reason"].astype("string").eq("")).sum()
                )
                quality["batteries_active_missing_retirement_fields"] += int(
                    (retired & (frame["retirement_reason"].isna() | frame["retirement_reason"].astype("string").eq(""))).sum()
                )

    except FileNotFoundError as exc:
        raise AuditError(f"Required dataset not found: {path}") from exc
    except pd.errors.ParserError as exc:
        raise AuditError(f"CSV parsing failed for {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise AuditError(f"Text decoding failed for {path}: {exc}") from exc

    result.duplicate_full_rows = result.rows - connection.execute(f"SELECT COUNT(*) FROM {full_hash_table}").fetchone()[0]
    result.duplicate_key_rows = result.rows - result.null_key_rows - connection.execute(
        f"SELECT COUNT(*) FROM {key_hash_table}"
    ).fetchone()[0]

    if filename == "city_daily_context.csv":
        _track_foreign_key(
            fk_stats,
            "stations.city -> city_daily_context.city",
            pd.Series(list(references["station_city"].values()), dtype="string"),
            references["context_cities"],
            nullable=True,
        )
    return result


def _write_csv(name: str, rows: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows)
    frame.to_csv(TABLE_DIR / name, index=False, encoding="utf-8")


def _render_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    headers = columns or list(rows[0])

    def cell(value: Any) -> str:
        if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
            return ""
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = [
        "| " + " | ".join(cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(cell(row.get(header, "")) for header in headers) + " |" for row in rows)
    return "\n".join(lines)


def _finalize(connection: sqlite3.Connection, results: list[DatasetResult], fk_stats: dict[str, dict[str, Any]], quality: Counter[str], extra: dict[str, Any], raw_dir: Path) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    inventory, missing_rows, key_rows, category_rows, numeric_rows, date_rows = [], [], [], [], [], []

    for result in results:
        path = raw_dir / result.filename
        inventory.append({
            "dataset": result.filename,
            "rows": result.rows,
            "columns": len(result.columns),
            "column_names": "|".join(result.columns),
            "size_bytes": path.stat().st_size,
            "size_mib": round(path.stat().st_size / (1024 * 1024), 3),
        })
        for col in result.columns:
            count = result.missing[col]
            missing_rows.append({
                "dataset": result.filename, "column": col, "missing_count": count,
                "missing_pct": round(100 * count / result.rows, 5) if result.rows else 0,
                "observed_dtypes": "|".join(sorted(result.dtypes[col])),
            })
        key_rows.append({
            "dataset": result.filename,
            "candidate_key": "|".join(SPECS[result.filename]["key"]),
            "rows": result.rows,
            "null_key_rows": result.null_key_rows,
            "duplicate_key_rows": result.duplicate_key_rows,
            "duplicate_full_row_fingerprints": result.duplicate_full_rows,
            "key_unique": result.null_key_rows == 0 and result.duplicate_key_rows == 0,
        })
        for col, counts in result.categories.items():
            for value, count in counts.most_common():
                category_rows.append({"dataset": result.filename, "column": col, "value": value, "count": count})
        for col, stats in result.numeric.items():
            sample = stats["sample"]
            numeric_rows.append({
                "dataset": result.filename,
                "column": col,
                "nonmissing_count": stats["count"],
                "minimum": stats["min"],
                "maximum": stats["max"],
                "mean": stats["sum"] / stats["count"] if stats["count"] else None,
                "median_sampled": float(np.percentile(sample, 50)) if len(sample) else None,
                "p01_sampled": float(np.percentile(sample, 1)) if len(sample) else None,
                "p25_sampled": float(np.percentile(sample, 25)) if len(sample) else None,
                "p75_sampled": float(np.percentile(sample, 75)) if len(sample) else None,
                "p99_sampled": float(np.percentile(sample, 99)) if len(sample) else None,
                "percentiles_note": f"deterministic bounded sample, at most {SAMPLE_LIMIT} values",
            })
        for col, dates in result.dates.items():
            date_rows.append({
                "dataset": result.filename, "column": col, **dates,
            })

    fk_rows = []
    for relation, values in sorted(fk_stats.items()):
        fk_rows.append({
            "relationship": relation,
            "rows_checked": values["rows_checked"],
            "unmatched_rows": values["unmatched_rows"],
            "unmatched_distinct_ids": len(values["unmatched_ids"]),
            "sample_unmatched_ids": "|".join(sorted(values["unmatched_ids"])[:20]),
        })
    _write_csv("dataset_inventory.csv", inventory)
    _write_csv("missing_value_summary.csv", missing_rows)
    _write_csv("duplicate_key_summary.csv", key_rows)
    _write_csv("categorical_value_summary.csv", category_rows)
    _write_csv("numerical_summary.csv", numeric_rows)
    _write_csv("foreign_key_validation.csv", fk_rows)

    # Dataset-specific joins and anomaly screens based on the observed tables.
    event_total, event_matched = connection.execute(
        """SELECT COUNT(*), SUM(CASE WHEN t.station_id IS NOT NULL THEN 1 ELSE 0 END)
           FROM event_pairs e LEFT JOIN telemetry_hours t
           ON t.station_id=e.station_id AND t.hour_epoch=(e.ts_epoch / 3600) * 3600"""
    ).fetchone()
    firmware_counts = connection.execute(
        """SELECT COUNT(*),
           SUM(CASE WHEN t1.station_id IS NOT NULL THEN 1 ELSE 0 END),
           SUM(CASE WHEN t2.station_id IS NOT NULL THEN 1 ELSE 0 END)
           FROM firmware_events f
           LEFT JOIN telemetry_hours t1 ON t1.station_id=f.station_id AND t1.hour_epoch=(f.ts_epoch / 3600) * 3600
           LEFT JOIN telemetry_hours t2 ON t2.station_id=f.station_id
             AND t2.hour_epoch=((f.ts_epoch+19800) / 3600) * 3600"""
    ).fetchone()
    pair_rows = connection.execute(
        """SELECT COUNT(*) FROM event_pairs a JOIN event_pairs b
           ON a.rider_id=b.rider_id AND a.station_id=b.station_id AND a.event_type=b.event_type
           AND a.battery_in_id=b.battery_in_id AND a.battery_out_id=b.battery_out_id
           AND a.row_id < b.row_id AND b.ts_epoch-a.ts_epoch BETWEEN -120 AND 120
           AND (a.sync_mode='offline_batch' OR b.sync_mode='offline_batch')"""
    ).fetchone()[0]
    offline_candidates = connection.execute(
        """SELECT COUNT(DISTINCT CASE WHEN a.sync_mode='offline_batch' THEN a.event_id ELSE b.event_id END)
           FROM event_pairs a JOIN event_pairs b
           ON a.rider_id=b.rider_id AND a.station_id=b.station_id AND a.event_type=b.event_type
           AND a.battery_in_id=b.battery_in_id AND a.battery_out_id=b.battery_out_id
           AND a.row_id < b.row_id AND b.ts_epoch-a.ts_epoch BETWEEN -120 AND 120
           AND (a.sync_mode='offline_batch' OR b.sync_mode='offline_batch')"""
    ).fetchone()[0]
    quality["event_rows_with_timestamp"] = int(event_total or 0)
    quality["event_telemetry_hour_matched"] = int(event_matched or 0)
    quality["event_telemetry_hour_unmatched"] = int((event_total or 0) - (event_matched or 0))
    quality["firmware_original_hour_matched"] = int(firmware_counts[1] or 0)
    quality["firmware_plus_5h_matched"] = int(firmware_counts[2] or 0)
    quality["firmware_candidate_count"] = int(firmware_counts[0] or 0)
    quality["offline_near_duplicate_pairs_120s"] = int(pair_rows)
    quality["offline_near_duplicate_events_120s"] = int(offline_candidates)

    quality_rows = [{"issue": name, "affected_records": int(value)} for name, value in sorted(quality.items())]
    _write_csv("data_quality_issue_summary.csv", quality_rows)

    dataset_lines = []
    for row in inventory:
        dataset_lines.append(
            f"| `{row['dataset']}` | {row['rows']:,} | {row['columns']} | {row['size_mib']:.3f} MiB |"
        )
    missing_nonzero = [row for row in missing_rows if row["missing_count"]]
    key_lines = [
        f"| `{r['dataset']}` | `{r['candidate_key']}` | {r['null_key_rows']:,} | "
        f"{r['duplicate_key_rows']:,} | {r['duplicate_full_row_fingerprints']:,} |"
        for r in key_rows
    ]
    date_text = "\n".join(
        f"- `{row['dataset']}.{row['column']}`: {row['minimum'] or 'no valid values'} to "
        f"{row['maximum'] or 'no valid values'}; invalid non-empty values: {row['invalid_nonempty']:,}."
        for row in date_rows
    )
    missing_markdown = _render_table(missing_nonzero, ["dataset", "column", "missing_count", "missing_pct"])
    category_focus = [row for row in category_rows if row["column"] in ("event_type", "sync_mode", "telemetry_status", "resolution_status", "home_city", "firmware_version", "station_firmware")]
    category_markdown = _render_table(category_focus, ["dataset", "column", "value", "count"])
    fk_markdown = _render_table(fk_rows)
    numeric_markdown = _render_table(numeric_rows)
    quality_markdown = _render_table(quality_rows)
    city_variants = {
        key: sorted(values) for key, values in extra.get("city_spellings", {}).items() if len(values) > 1
    }
    city_variant_lines = [
        f"- Normalized spellings `{key}`: " + ", ".join(f"`{v}`" for v in values)
        for key, values in sorted(city_variants.items())
    ] or ["- No spelling variants that normalize to the same alphanumeric form were found."]
    csat_rows = [
        {"resolution_status": status, **counts, "missing_csat_pct": round(100 * counts["csat_missing"] / counts["tickets"], 3) if counts["tickets"] else 0}
        for status, counts in sorted(extra.get("csat_by_resolution", {}).items())
    ]
    csat_markdown = _render_table(csat_rows)

    report = f"""# Stage 1 — VOLT SCOPE Data Audit

## Scope and method

This is a read-only audit of all eight expected CSV files in `data/raw/`. The large swap and station telemetry files were streamed in Pandas chunks of {CHUNK_SIZE:,} rows; no raw data was changed, cleaned, excluded, or copied into processed data. A temporary SQLite database was used for cross-chunk duplicate/key fingerprints, telemetry-hour joins, and the offline event candidate join, then discarded. Full-row and key duplicate checks use 64-bit Pandas row-hash fingerprints; accidental hash collisions are theoretically possible but highly unlikely. Numerical minima, maxima, counts, and means are streamed over all parsed numeric values; medians and percentiles are estimated from deterministic bounded samples, as identified in the CSV summary.

The script reports errors for missing or unparseable inputs rather than substituting empty results. All conclusions below are measured from the available files. The `outputs/tables/` CSVs contain column-level details not repeated in full here.

## Project structure

Created the requested directories: `data/raw/`, `data/interim/`, `data/processed/`, `notebooks/`, `src/data/`, `src/analysis/`, `src/utils/`, `reports/`, `outputs/figures/`, and `outputs/tables/`.

## Dataset inventory

| Dataset | Rows | Columns | File size |
|---|---:|---:|---:|
{chr(10).join(dataset_lines)}

### Observed data types and date/time ranges

Each dataset's observed Pandas dtype(s) per column and date/time fields are available in `dataset_inventory.csv`, `missing_value_summary.csv`, and `numerical_summary.csv`. Date ranges and invalid non-empty timestamp/date values:

{date_text}

## Missing values

The `missing_value_summary.csv` lists every column, missing count, percentage, and observed dtype. Columns with missing values:

{missing_markdown}

Interpretation: empty/NA values are reported as missing, not converted to zero. Missing retirement dates/reasons may be expected for active batteries; verify against operational status before treatment. Missing CSAT can be resolution-related; observed counts by resolution status follow below. Telemetry missingness is an availability/data-quality concern, not evidence of zero inventory or zero performance.

### CSAT missingness by ticket resolution status

{csat_markdown}

## Duplicate and candidate-key checks

Candidate keys were selected from the observed schema: event ID; station ID + hour start; rider ID; battery ID; station ID; ticket ID; city + date; and partner ID. Duplicate counts are repeated rows after the first occurrence; null composite-key rows are counted separately.

| Dataset | Candidate key | Null key rows | Duplicate key rows | Duplicate full-row fingerprints |
|---|---|---:|---:|---:|
{chr(10).join(key_lines)}

Review key violations before using these fields as dimensions. A repeated event ID, event fingerprint, or near-match does not by itself establish that a real swap should be removed.

## Relationship validation

Foreign-key checks compare the child IDs against IDs in the parent datasets; optional blank battery/ticket references are excluded from the unmatched denominator. Fleet-partner validation follows each event's rider to the rider's partner ID and then to the fleet table. Station city names are checked against city names in the daily context table. Full counts and examples are in `foreign_key_validation.csv`.

{fk_markdown}

City context operates at city/date grain. Joining it to station records is therefore many-to-one at city grain; joining station telemetry or swaps requires matching city and date after mapping station ID to city. Station-hour/swaps matching is an hour-level availability comparison, not a claim that the telemetry represents each individual event.

## Categorical and numerical checks

Important categories and their frequencies:

{category_markdown}

Numerical measures, exact streamed extrema/mean, and sampled percentiles:

{numeric_markdown}

Percentages and bounds are not assumed valid merely because they are close to expected values. In particular, SOC/SOH above 100% are quantified in the quality table; threshold findings need product/BMS definitions before cleaning.

## Explicit data-quality risk investigations

| Check | Finding / measured count | Stage 1 disposition |
|---|---|---|
| Event completion distribution | `{quality['swap_completed_rows']:,}` of `{quality['swap_event_rows']:,}` events have `event_type=swap_completed` ({(100 * quality['swap_completed_rows'] / quality['swap_event_rows'] if quality['swap_event_rows'] else 0):.3f}%). Full `event_type` frequencies are in the categorical summary. | Verify denominator/category semantics before treating as attempt completion rate. |
| Offline synchronization | `{quality['offline_batch_rows']:,}` rows have `sync_mode=offline_batch`; exact sync mode frequencies are in the categorical summary. | Retain; synchronization mode alone is not duplicate evidence. |
| Offline near-duplicate candidates | `{quality['offline_near_duplicate_pairs_120s']:,}` matching event pairs / `{quality['offline_near_duplicate_events_120s']:,}` distinct offline events have the same rider, station, event type, incoming/outgoing battery IDs and timestamp within ±120 seconds of another event. | Candidate screen only; not deduplicated. Confirm event semantics and inspect examples in Stage 2 if candidates exist. |
| Firmware timestamp window | `{quality['firmware_candidate_count']:,}` events have `station_firmware=v3.2.0` and timestamps from 2025-03-10 through 2025-04-14 inclusive. Of these, `{quality['firmware_original_hour_matched']:,}` match a telemetry row at the recorded hour and `{quality['firmware_plus_5h_matched']:,}` match at recorded timestamp + 5h30 (rounded down to an hourly bucket). | No timestamps corrected. This association can support further investigation, but hour-level matches cannot prove an exact 5h30 shift. |
| Rider city spelling | {len(city_variants):,} normalized spelling groups have multiple observed `home_city` values. | Preserve source labels; map only after approved canonicalization. |
| `km_since_last_swap` | `{quality['km_negative']:,}` values are negative; `{quality['km_over_1000_screen']:,}` exceed the 1,000 km screening threshold. The threshold is a triage screen, not an asserted physical/product limit. | Investigate trip/reset semantics and validate outliers; do not modify. |
| SOC/SOH | Counts above 100%, above 101%, and below zero per input/output SOC/SOH field are in `data_quality_issue_summary.csv`. | Confirm instrument tolerance and BMS rules before clipping or exclusion. |
| Test stations | `{quality['test_station_events']:,}` events use station IDs beginning `STN-TST`; `{quality['test_station_zero_amount']:,}` of those have zero `amount_charged_inr`. | Identify test traffic and decide analytical scope later; no exclusions now. |
| Telemetry completeness | `{quality['telemetry_timestamp_invalid']:,}` invalid/missing `hour_start` values. Of `{quality['event_rows_with_timestamp']:,}` timestamp-valid events, `{quality['event_telemetry_hour_matched']:,}` share a station/hour present in telemetry; `{quality['event_telemetry_hour_unmatched']:,}` do not. Per-column missingness, duplicate station/hour keys, and `telemetry_status` frequencies are reported separately. | Missing/partial observations must not be interpreted as zero; check coverage windows and status codes. |
| CSAT missingness | `{quality['ticket_csat_missing']:,}` of `{quality['ticket_rows']:,}` tickets have missing CSAT; the resolution breakdown is above. | Evaluate response bias and resolution-dependent collection before customer-experience comparisons. |
| Battery retirement fields | `{quality['batteries_missing_retired_date']:,}` batteries lack retirement dates; `{quality['batteries_active_missing_retirement_fields']:,}` lack both retirement date and reason. | Likely expected for active units; confirm status lifecycle in data dictionary. |

### Detailed quality counts

{quality_markdown}

### Rider city spelling groups

{chr(10).join(city_variant_lines)}

## Checks not completed / limitations

- The provided snapshot contains all eight expected files and the audit script completed for the observed files.
- The source schema does not contain a separate authoritative timestamp to independently prove a 5h30 event-time correction. Comparison to hourly telemetry is a coarse corroboration only.
- Event/telemetry overlap means that a station-hour row exists; it does not validate telemetry values, prove a swap happened at that hour, or imply absent telemetry means zero.
- Candidate keys are schema-based and need business-owner confirmation. Hash-based duplicate checks have a negligible fingerprint collision risk.
- Percentiles in `numerical_summary.csv` are deterministic bounded-sample estimates; min/max/count/mean are computed over parsed numeric observations.
- City spelling analysis identifies only variants that reduce to the same normalized alphanumeric spelling; semantic aliases such as an urban area and its suburb may remain distinct.
- No charts, causal claims, data cleaning, exclusions, or business recommendations were produced, per Stage 1 scope.

## Risks for subsequent analysis

1. Event type denominator/attempt semantics may differ from a simple completion percentage.
2. Offline retries, event ID uniqueness, and test traffic could bias event counts.
3. Firmware timestamp concerns can shift event-to-hour/day attribution, especially around dates and city context.
4. Telemetry coverage and status must be modeled explicitly; nulls are not zeros.
5. IDs, event batteries, optional ticket references, and city/date joins need the reported unmatched and duplicate checks applied in downstream query design.
6. CSAT response availability may be non-random; rider cities, SOC/SOH tolerances, and distance outliers require definitions.
7. Repeated swaps or near-duplicate candidates can be legitimate attempts; exclusions need documented business rules.

## Recommended Stage 2 actions

1. Review the CSV summaries and resolve key/FK exceptions with the dataset owner.
2. Confirm event types, valid status transitions, attempt semantics, and the completion-rate denominator.
3. Investigate firmware suspect events against release notes, device logs, and finer-grain trusted timestamps; defer all timestamp correction until a rule is validated.
4. Inspect offline near-duplicate examples and define a reproducible adjudication rule, without deleting raw rows.
5. Confirm acceptable SOC/SOH ranges, distance semantics, test-station identification, active battery lifecycle, and city canonical labels.
6. Quantify station-hour coverage and ticket CSAT nonresponse before selecting analysis denominators.
7. Preserve immutable raw inputs and record any approved transformations as separate, reversible processed outputs.

## Rerunning the audit

From the project root:

```powershell
python src\\data\\audit_data.py
```

Optional chunk-size override:

```powershell
python src\\data\\audit_data.py --chunk-size 50000
```

The command refreshes audit CSV outputs and this report. It reads but never rewrites files under `data/raw/`.

## Assumptions and unresolved issues

- The eight filenames are the expected source tables, and their observed field names are used as intended.
- Candidate key and near-duplicate definitions are explicit operational screens, not approved business rules.
- The 1,000 km cutoff is used only to identify records for investigation.
- Firmware comparison uses a recorded-hour versus +5-hour hour-bucket match; it is insufficient to verify the hypothesized 5h30 correction.

## Status

**READY FOR STAGE 2** — all eight expected datasets were found and the read-only audit and requested outputs completed. This status means the Stage 1 audit is available for review; it does not certify data as clean or resolve the listed business-rule and timestamp questions. Stage 2 should start only after your review and instructions.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def run_audit(raw_dir: Path = RAW_DIR, chunk_size: int = CHUNK_SIZE) -> None:
    if chunk_size <= 0:
        raise ValueError("--chunk-size must be a positive integer")
    for filename in SPECS:
        if not (raw_dir / filename).is_file():
            raise FileNotFoundError(f"Required dataset not found: {raw_dir / filename}")
    references = _load_references(raw_dir)
    fk_stats: dict[str, dict[str, Any]] = {}
    quality: Counter[str] = Counter()
    extra: dict[str, Any] = {}
    results: list[DatasetResult] = []
    with tempfile.TemporaryDirectory(prefix="volt_scope_audit_") as temp_dir:
        database_path = Path(temp_dir) / "audit.sqlite"
        with sqlite3.connect(database_path) as connection:
            _init_database(connection)
            for filename in EXPECTED_ORDER:
                print(f"Auditing {filename} ...", flush=True)
                results.append(
                    audit_dataset(filename, raw_dir, connection, references, fk_stats, quality, extra, chunk_size)
                )
                connection.commit()
            connection.execute("CREATE INDEX event_pair_signature ON event_pairs(rider_id, station_id, event_type, battery_in_id, battery_out_id)")
            _finalize(connection, results, fk_stats, quality, extra, raw_dir)
    print(f"Audit complete. Report: {REPORT_PATH}")
    print(f"CSV summaries: {TABLE_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the read-only, chunked VOLT SCOPE data audit.")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE, help=f"Rows per Pandas chunk (default: {CHUNK_SIZE})")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="Directory containing the eight raw CSV files")
    args = parser.parse_args()
    try:
        run_audit(args.raw_dir, args.chunk_size)
    except (AuditError, FileNotFoundError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
