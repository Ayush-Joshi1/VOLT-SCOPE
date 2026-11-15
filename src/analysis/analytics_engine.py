"""
Phase 2 — Comprehensive Business Analytics Engine
VoltRelay Energy Intelligence | Hackathon Analytics

Runs all 6 business-dimension analyses using DuckDB on cleaned Parquet files.
Saves results to outputs/analytics/ as CSVs for notebook rendering.
Never modifies raw or cleaned data.

Run from project root: python src/analysis/analytics_engine.py
"""
import sys, json
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
import pandas as pd
import duckdb

ROOT     = Path(r"c:\Users\ADMIN\OneDrive\Desktop\VOLT-SCOPE")
INTERIM  = ROOT / "data" / "interim"
PROC     = ROOT / "data" / "processed"
OUT      = ROOT / "outputs" / "analytics"
OUT.mkdir(parents=True, exist_ok=True)

SWAPS    = str(PROC    / "swap_events_clean.parquet")
TELE     = str(PROC    / "station_hourly_status_clean.parquet")
FLEET    = str(INTERIM / "fleet_partners_clean.parquet")
STATIONS = str(INTERIM / "stations_clean.parquet")
CDC      = str(INTERIM / "city_daily_context_clean.parquet")
BATT     = str(INTERIM / "batteries_clean.parquet")
RIDERS   = str(INTERIM / "riders_clean.parquet")
TICKETS  = str(INTERIM / "support_tickets_clean.parquet")
EXC      = str(INTERIM / "chronology_exceptions_24_events.parquet")

con = duckdb.connect()

def save(df: pd.DataFrame, name: str):
    path = OUT / f"{name}.csv"
    df.to_csv(str(path), index=False, encoding="utf-8")
    print(f"  -> {name}.csv  ({len(df)} rows)")
    return df

print("=" * 70)
print("VOLTRELAY PHASE 2 ANALYTICS ENGINE")
print("=" * 70)

# ============================================================
# A. NETWORK & SERVICE PERFORMANCE
# ============================================================
print("\n[A] Network & Service Performance")

# A1. Overall KPIs
a1 = con.execute(f"""
    SELECT
        COUNT(*)                                                AS total_swaps,
        SUM(CASE WHEN event_type='swap_completed' THEN 1 END)  AS completed,
        SUM(CASE WHEN event_type LIKE 'failed%'   THEN 1 END)  AS failed,
        SUM(CASE WHEN event_type LIKE 'abandoned%' OR event_type LIKE 'cancelled%' THEN 1 END) AS abandoned,
        ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_rate_pct,
        ROUND(100.0 * SUM(CASE WHEN event_type LIKE 'failed%' THEN 1 END) / COUNT(*), 3) AS failure_rate_pct,
        ROUND(AVG(queue_wait_sec), 1)                          AS avg_queue_wait_sec,
        ROUND(AVG(amount_charged_inr), 2)                      AS avg_revenue_inr,
        ROUND(SUM(amount_charged_inr), 0)                      AS total_revenue_inr,
        COUNT(DISTINCT rider_id)                                AS unique_riders,
        COUNT(DISTINCT station_id)                              AS unique_stations
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%'
""").df()
save(a1, "A1_overall_kpis")

# A2. Failure reason breakdown
a2 = con.execute(f"""
    SELECT event_type,
           COUNT(*) AS cnt,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 3) AS pct
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%'
    GROUP BY event_type ORDER BY cnt DESC
""").df()
save(a2, "A2_failure_reason_distribution")

# A3. Monthly success rate trend
a3 = con.execute(f"""
    SELECT event_month,
           COUNT(*) AS total,
           SUM(CASE WHEN event_type='swap_completed' THEN 1 END) AS completed,
           SUM(CASE WHEN event_type LIKE 'failed%' THEN 1 END)   AS failed,
           ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_pct,
           ROUND(AVG(queue_wait_sec), 1) AS avg_queue_sec,
           ROUND(AVG(amount_charged_inr), 2) AS avg_revenue_inr
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%' AND event_month IS NOT NULL
    GROUP BY event_month ORDER BY event_month
""").df()
save(a3, "A3_monthly_trends")

# A4. Performance by city (join via stations)
a4 = con.execute(f"""
    SELECT st.city,
           COUNT(*)  AS total_swaps,
           SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) AS completed,
           SUM(CASE WHEN se.event_type LIKE 'failed%' THEN 1 END)   AS failed,
           ROUND(100.0 * SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_pct,
           ROUND(AVG(se.queue_wait_sec), 1)         AS avg_queue_sec,
           ROUND(AVG(se.amount_charged_inr), 2)     AS avg_revenue_inr,
           ROUND(SUM(se.amount_charged_inr), 0)     AS total_revenue_inr,
           COUNT(DISTINCT se.station_id)             AS n_stations
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
    WHERE se.station_id NOT LIKE 'STN-TST%'
    GROUP BY st.city ORDER BY total_swaps DESC
""").df()
save(a4, "A4_performance_by_city")

# A5. Performance by firmware version
a5 = con.execute(f"""
    SELECT station_firmware,
           COUNT(*) AS total,
           SUM(CASE WHEN event_type='swap_completed' THEN 1 END) AS completed,
           SUM(CASE WHEN event_type LIKE 'failed%' THEN 1 END)   AS failed,
           ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_pct,
           ROUND(100.0 * SUM(CASE WHEN event_type LIKE 'failed%' THEN 1 END) / COUNT(*), 3)  AS failure_pct,
           ROUND(AVG(queue_wait_sec), 1) AS avg_queue_sec
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%'
    GROUP BY station_firmware ORDER BY total DESC
""").df()
save(a5, "A5_performance_by_firmware")

# A6. Peak hour performance (avg hourly success by hour of day)
a6 = con.execute(f"""
    SELECT event_hour,
           COUNT(*) AS total_swaps,
           ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_pct,
           ROUND(AVG(queue_wait_sec), 1) AS avg_queue_sec,
           ROUND(AVG(amount_charged_inr), 2) AS avg_revenue_inr
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%' AND event_hour IS NOT NULL
    GROUP BY event_hour ORDER BY event_hour
""").df()
save(a6, "A6_hourly_performance")

# A7. Top 15 worst stations by failure rate (min 200 events)
a7 = con.execute(f"""
    SELECT se.station_id, st.city, st.charger_generation,
           COUNT(*) AS total,
           SUM(CASE WHEN se.event_type LIKE 'failed%' THEN 1 END) AS failed,
           ROUND(100.0 * SUM(CASE WHEN se.event_type LIKE 'failed%' THEN 1 END) / COUNT(*), 2) AS failure_pct,
           ROUND(AVG(se.queue_wait_sec), 1) AS avg_queue_sec
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
    WHERE se.station_id NOT LIKE 'STN-TST%'
    GROUP BY se.station_id, st.city, st.charger_generation
    HAVING COUNT(*) >= 200
    ORDER BY failure_pct DESC LIMIT 15
""").df()
save(a7, "A7_worst_stations")

# A8. Performance by charger generation
a8 = con.execute(f"""
    SELECT st.charger_generation,
           COUNT(*) AS total,
           ROUND(100.0 * SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_pct,
           ROUND(100.0 * SUM(CASE WHEN se.event_type LIKE 'failed%' THEN 1 END) / COUNT(*), 3)   AS failure_pct,
           ROUND(AVG(se.queue_wait_sec), 1) AS avg_queue_sec,
           COUNT(DISTINCT se.station_id) AS n_stations
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
    WHERE se.station_id NOT LIKE 'STN-TST%'
    GROUP BY st.charger_generation ORDER BY success_pct ASC
""").df()
save(a8, "A8_performance_by_generation")

# ============================================================
# B. CUSTOMER EXPERIENCE & SUPPORT
# ============================================================
print("\n[B] Customer Experience & Support")

# B1. Ticket categories
b1 = con.execute(f"""
    SELECT category,
           COUNT(*) AS tickets,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) AS pct,
           ROUND(AVG(resolution_hours), 1) AS avg_resolution_hours,
           ROUND(AVG(CASE WHEN csat_score IS NOT NULL THEN csat_score END), 2) AS avg_csat_nonmissing,
           SUM(CASE WHEN csat_score IS NULL THEN 1 END) AS csat_missing,
           ROUND(100.0 * SUM(CASE WHEN csat_score IS NULL THEN 1 END) / COUNT(*), 1) AS csat_missing_pct
    FROM read_parquet('{TICKETS}')
    GROUP BY category ORDER BY tickets DESC
""").df()
save(b1, "B1_ticket_categories")

# B2. Resolution status and CSAT
b2 = con.execute(f"""
    SELECT resolution_status,
           COUNT(*) AS tickets,
           ROUND(AVG(resolution_hours), 1) AS avg_resolution_hours,
           ROUND(AVG(CASE WHEN csat_score IS NOT NULL THEN csat_score END), 2) AS avg_csat,
           SUM(CASE WHEN csat_score IS NULL THEN 1 END) AS csat_missing,
           ROUND(100.0 * SUM(CASE WHEN csat_score IS NULL THEN 1 END) / COUNT(*), 1) AS csat_missing_pct,
           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY resolution_hours), 1) AS median_resolution_hours
    FROM read_parquet('{TICKETS}')
    GROUP BY resolution_status ORDER BY tickets DESC
""").df()
save(b2, "B2_resolution_csat")

# B3. Monthly ticket volume trend
b3 = con.execute(f"""
    SELECT STRFTIME(ticket_date::DATE, '%Y-%m') AS ym,
           COUNT(*) AS tickets,
           SUM(CASE WHEN priority IN ('high','critical') THEN 1 END) AS high_priority,
           ROUND(AVG(CASE WHEN csat_score IS NOT NULL THEN csat_score END), 2) AS avg_csat
    FROM read_parquet('{TICKETS}')
    WHERE ticket_date IS NOT NULL
    GROUP BY ym ORDER BY ym
""").df()
save(b3, "B3_monthly_tickets")

# B4. Tickets per city (join via station)
b4 = con.execute(f"""
    SELECT st.city,
           COUNT(tk.ticket_id) AS tickets,
           ROUND(AVG(tk.resolution_hours), 1) AS avg_resolution_hours,
           ROUND(AVG(CASE WHEN tk.csat_score IS NOT NULL THEN tk.csat_score END), 2) AS avg_csat,
           ROUND(100.0 * SUM(CASE WHEN tk.csat_score IS NULL THEN 1 END) / COUNT(*), 1) AS csat_missing_pct
    FROM read_parquet('{TICKETS}') tk
    JOIN read_parquet('{STATIONS}') st ON tk.station_id = st.station_id
    GROUP BY st.city ORDER BY tickets DESC
""").df()
save(b4, "B4_tickets_by_city")

# B5. Tickets per channel
b5 = con.execute(f"""
    SELECT channel, COUNT(*) AS tickets,
           ROUND(AVG(resolution_hours), 1) AS avg_resolution_hours,
           ROUND(AVG(CASE WHEN csat_score IS NOT NULL THEN csat_score END), 2) AS avg_csat
    FROM read_parquet('{TICKETS}')
    GROUP BY channel ORDER BY tickets DESC
""").df()
save(b5, "B5_tickets_by_channel")

# B6. CSAT distribution (non-missing only)
b6 = con.execute(f"""
    SELECT CAST(ROUND(csat_score, 0) AS INT) AS csat_rounded,
           COUNT(*) AS cnt
    FROM read_parquet('{TICKETS}')
    WHERE csat_score IS NOT NULL
    GROUP BY CAST(ROUND(csat_score, 0) AS INT) ORDER BY csat_rounded
""").df()
save(b6, "B6_csat_distribution")

# ============================================================
# C. STATION & CITY OPERATIONS
# ============================================================
print("\n[C] Station & City Operations")

# C1. Station utilization (swaps per station per day)
c1 = con.execute(f"""
    SELECT se.station_id, st.city, st.charger_generation, st.location_type,
           COUNT(*)                                               AS total_swaps,
           COUNT(DISTINCT se.event_date::DATE)                   AS active_days,
           ROUND(1.0 * COUNT(*) / NULLIF(COUNT(DISTINCT se.event_date::DATE), 0), 1) AS swaps_per_active_day,
           ROUND(SUM(se.amount_charged_inr), 0)                  AS total_revenue_inr,
           ROUND(AVG(se.queue_wait_sec), 1)                      AS avg_queue_sec,
           ROUND(100.0 * SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) / COUNT(*), 2) AS success_pct
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
    WHERE se.station_id NOT LIKE 'STN-TST%'
    GROUP BY se.station_id, st.city, st.charger_generation, st.location_type
    ORDER BY swaps_per_active_day DESC
""").df()
save(c1, "C1_station_utilization")

# C2. Telemetry health by city
c2 = con.execute(f"""
    SELECT st.city,
           COUNT(*) AS tele_rows,
           SUM(CASE WHEN t.telemetry_status='ok'      THEN 1 END) AS ok_rows,
           SUM(CASE WHEN t.telemetry_status='partial'  THEN 1 END) AS partial_rows,
           SUM(CASE WHEN t.telemetry_status='missing'  THEN 1 END) AS missing_rows,
           ROUND(100.0 * SUM(CASE WHEN t.telemetry_status='ok' THEN 1 END) / COUNT(*), 2) AS pct_ok,
           ROUND(AVG(t.outage_minutes), 2)             AS avg_outage_min,
           ROUND(AVG(t.packs_quarantined), 3)          AS avg_quarantined,
           ROUND(AVG(t.charged_2w_avg), 2)             AS avg_charged_2w
    FROM read_parquet('{TELE}') t
    JOIN read_parquet('{STATIONS}') st ON t.station_id = st.station_id
    GROUP BY st.city ORDER BY avg_outage_min DESC
""").df()
save(c2, "C2_telemetry_by_city")

# C3. Outage correlation with failures (station-day level)
c3 = con.execute(f"""
    WITH station_day AS (
        SELECT station_id,
               telemetry_date::DATE AS day,
               AVG(outage_minutes) AS avg_outage,
               AVG(charged_2w_avg) AS avg_charged_2w,
               AVG(packs_quarantined) AS avg_quarantined
        FROM read_parquet('{TELE}')
        WHERE telemetry_status='ok'
        GROUP BY station_id, telemetry_date::DATE
    ),
    swap_day AS (
        SELECT station_id,
               event_date::DATE AS day,
               COUNT(*) AS total_swaps,
               ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 2) AS success_pct,
               AVG(queue_wait_sec) AS avg_queue_sec
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%'
        GROUP BY station_id, event_date::DATE
    )
    SELECT sd.avg_outage,
           sd.avg_charged_2w,
           sd.avg_quarantined,
           sw.success_pct,
           sw.avg_queue_sec,
           sw.total_swaps
    FROM station_day sd
    JOIN swap_day sw ON sd.station_id=sw.station_id AND sd.day=sw.day
    WHERE sw.total_swaps >= 5
""").df()
save(c3, "C3_outage_vs_performance")

# C4. City context: weather/disruptions vs performance
c4 = con.execute(f"""
    WITH swap_city_day AS (
        SELECT st.city,
               se.event_date::DATE AS day,
               COUNT(*) AS total,
               ROUND(100.0 * SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) / COUNT(*), 3) AS success_pct,
               AVG(se.queue_wait_sec) AS avg_queue
        FROM read_parquet('{SWAPS}') se
        JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
        WHERE se.station_id NOT LIKE 'STN-TST%'
        GROUP BY st.city, se.event_date::DATE
    )
    SELECT ctx.city, ctx.date,
           ctx.max_temp_c, ctx.rainfall_mm, ctx.heat_alert,
           ctx.flood_disruption, ctx.grid_outage_hours,
           ctx.is_public_holiday, ctx.festival_or_event,
           ctx.competitor_promo_active,
           scd.total AS daily_swaps,
           scd.success_pct,
           scd.avg_queue
    FROM read_parquet('{CDC}') ctx
    JOIN swap_city_day scd ON ctx.city=scd.city AND ctx.date::DATE=scd.day
""").df()
save(c4, "C4_context_vs_performance")

# C5. Queue by location type
c5 = con.execute(f"""
    SELECT st.location_type,
           COUNT(*) AS total_swaps,
           ROUND(AVG(se.queue_wait_sec), 1) AS avg_queue_sec,
           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY se.queue_wait_sec), 1) AS median_queue_sec,
           ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY se.queue_wait_sec), 1) AS p95_queue_sec,
           ROUND(100.0 * SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) / COUNT(*), 2) AS success_pct
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
    WHERE se.station_id NOT LIKE 'STN-TST%'
    GROUP BY st.location_type ORDER BY avg_queue_sec DESC
""").df()
save(c5, "C5_queue_by_location_type")

# ============================================================
# D. BATTERY HEALTH & PERFORMANCE
# ============================================================
print("\n[D] Battery Health & Performance")

# D1. SOH distribution by pack type
d1 = con.execute(f"""
    SELECT pack_type, supplier,
           COUNT(*) AS n_batteries,
           ROUND(AVG(current_soh_pct), 2) AS avg_soh_pct,
           ROUND(MIN(current_soh_pct), 2) AS min_soh_pct,
           ROUND(MAX(current_soh_pct), 2) AS max_soh_pct,
           ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY current_soh_pct), 2) AS p25_soh,
           ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY current_soh_pct), 2) AS median_soh,
           ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY current_soh_pct), 2) AS p75_soh,
           SUM(CASE WHEN is_retired THEN 1 END) AS retired_count,
           ROUND(100.0 * SUM(CASE WHEN is_retired THEN 1 END) / COUNT(*), 2) AS retired_pct
    FROM read_parquet('{BATT}')
    GROUP BY pack_type, supplier ORDER BY avg_soh_pct ASC
""").df()
save(d1, "D1_soh_by_type_supplier")

# D2. Retirement reasons
d2 = con.execute(f"""
    SELECT retirement_reason,
           COUNT(*) AS cnt,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) AS pct,
           ROUND(AVG(current_soh_pct), 2) AS avg_soh_at_retirement
    FROM read_parquet('{BATT}')
    WHERE is_retired AND retirement_reason IS NOT NULL
    GROUP BY retirement_reason ORDER BY cnt DESC
""").df()
save(d2, "D2_retirement_reasons")

# D3. SOH degradation rate by manufacturing lot (commission date vs current SOH)
d3 = con.execute(f"""
    SELECT manufacturing_lot, supplier,
           COUNT(*) AS n_packs,
           ROUND(AVG(DATE_DIFF('month', commission_date::DATE, CURRENT_DATE)), 1) AS avg_months_in_service,
           ROUND(AVG(initial_soh_pct), 2) AS avg_initial_soh,
           ROUND(AVG(current_soh_pct),  2) AS avg_current_soh,
           ROUND(AVG(initial_soh_pct - current_soh_pct), 3) AS avg_soh_decline,
           ROUND(AVG((initial_soh_pct - current_soh_pct) /
                 NULLIF(DATE_DIFF('month', commission_date::DATE, CURRENT_DATE), 0)), 4) AS monthly_soh_decline_rate
    FROM read_parquet('{BATT}')
    WHERE commission_date IS NOT NULL AND initial_soh_pct IS NOT NULL AND current_soh_pct IS NOT NULL
    GROUP BY manufacturing_lot, supplier
    HAVING COUNT(*) >= 5
    ORDER BY monthly_soh_decline_rate DESC
""").df()
save(d3, "D3_degradation_by_lot")

# D4. Battery health vs swap failures (via swap_events SOH readings)
d4 = con.execute(f"""
    SELECT
        CASE
            WHEN soh_in_pct < 65  THEN '< 65%'
            WHEN soh_in_pct < 75  THEN '65-75%'
            WHEN soh_in_pct < 85  THEN '75-85%'
            WHEN soh_in_pct < 100 THEN '85-100%'
            ELSE '>100% (anomaly)'
        END AS soh_band,
        COUNT(*) AS swaps,
        ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 2) AS success_pct,
        ROUND(100.0 * SUM(CASE WHEN event_type LIKE 'failed%'   THEN 1 END) / COUNT(*), 2) AS failure_pct,
        ROUND(AVG(queue_wait_sec), 1) AS avg_queue_sec
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%' AND soh_in_pct IS NOT NULL
    GROUP BY soh_band ORDER BY soh_band
""").df()
save(d4, "D4_soh_band_vs_performance")

# D5. SOH anomaly (>100%) investigation
d5 = con.execute(f"""
    SELECT station_firmware,
           SUM(CASE WHEN soh_in_pct > 100  THEN 1 END) AS soh_in_over100,
           SUM(CASE WHEN soh_out_pct > 100 THEN 1 END) AS soh_out_over100,
           SUM(CASE WHEN soc_out_pct > 100 THEN 1 END) AS soc_out_over100,
           COUNT(*) AS total_swaps,
           ROUND(100.0 * SUM(CASE WHEN soh_in_pct > 100 THEN 1 END) / COUNT(*), 3) AS soh_in_over100_pct
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%'
    GROUP BY station_firmware ORDER BY total_swaps DESC
""").df()
save(d5, "D5_soh_anomaly_by_firmware")

# ============================================================
# E. PRICING & FLEET PARTNER ECONOMICS
# ============================================================
print("\n[E] Pricing & Fleet Partner Economics")

# E1. Revenue by tariff code and payment mode
e1 = con.execute(f"""
    SELECT tariff_code, payment_mode,
           COUNT(*) AS swaps,
           ROUND(AVG(list_price_inr), 2)      AS avg_list_price,
           ROUND(AVG(discount_inr), 2)         AS avg_discount,
           ROUND(AVG(amount_charged_inr), 2)   AS avg_net_revenue,
           ROUND(SUM(amount_charged_inr), 0)   AS total_revenue,
           ROUND(100.0 * AVG(discount_inr) / NULLIF(AVG(list_price_inr), 0), 2) AS avg_discount_pct
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%'
    GROUP BY tariff_code, payment_mode ORDER BY total_revenue DESC
""").df()
save(e1, "E1_revenue_by_tariff")

# E2. Revenue trend (monthly)
e2 = con.execute(f"""
    SELECT event_month,
           ROUND(SUM(amount_charged_inr), 0)   AS total_revenue_inr,
           ROUND(AVG(amount_charged_inr), 2)   AS avg_revenue_per_swap,
           ROUND(AVG(list_price_inr), 2)        AS avg_list_price,
           ROUND(AVG(discount_inr), 2)          AS avg_discount,
           ROUND(100.0 * AVG(discount_inr) / NULLIF(AVG(list_price_inr), 0), 2) AS avg_discount_pct,
           COUNT(*) AS swaps
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%' AND event_month IS NOT NULL
    GROUP BY event_month ORDER BY event_month
""").df()
save(e2, "E2_revenue_trend")

# E3. Fleet partner economics
e3 = con.execute(f"""
    SELECT fp.partner_name, fp.partner_segment, fp.contract_type,
           fp.discount_pct, fp.discount_pct_after_amendment,
           fp.payment_terms_days,
           COUNT(se.event_id)                     AS swaps,
           ROUND(SUM(se.amount_charged_inr), 0)   AS total_revenue_inr,
           ROUND(AVG(se.amount_charged_inr), 2)   AS avg_revenue_per_swap,
           ROUND(AVG(se.list_price_inr), 2)        AS avg_list_price,
           ROUND(AVG(se.discount_inr), 2)          AS avg_discount_given,
           ROUND(100.0 * AVG(se.discount_inr) / NULLIF(AVG(se.list_price_inr), 0), 2) AS effective_discount_pct,
           COUNT(DISTINCT se.rider_id)              AS unique_riders,
           ROUND(100.0 * SUM(CASE WHEN se.event_type='swap_completed' THEN 1 END) / COUNT(*), 2) AS success_pct
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{RIDERS}') r ON se.rider_id = r.rider_id
    JOIN read_parquet('{FLEET}') fp ON r.partner_id = fp.partner_id
    WHERE se.station_id NOT LIKE 'STN-TST%'
    GROUP BY fp.partner_name, fp.partner_segment, fp.contract_type,
             fp.discount_pct, fp.discount_pct_after_amendment, fp.payment_terms_days
    ORDER BY total_revenue_inr DESC
""").df()
save(e3, "E3_partner_economics")

# E4. Revenue by city per swap (unit economics)
e4 = con.execute(f"""
    SELECT st.city, st.charger_generation,
           COUNT(se.event_id)                     AS total_swaps,
           ROUND(AVG(se.amount_charged_inr), 2)   AS avg_net_revenue_inr,
           ROUND(AVG(se.list_price_inr), 2)        AS avg_list_price_inr,
           ROUND(AVG(se.discount_inr), 2)          AS avg_discount_inr,
           ROUND(100.0 * AVG(se.discount_inr) / NULLIF(AVG(se.list_price_inr), 0), 2) AS discount_pct,
           ROUND(AVG(se.energy_to_recharge_kwh), 3) AS avg_kwh_recharged,
           ROUND(AVG(st.grid_tariff_inr_kwh), 4)    AS avg_grid_tariff,
           ROUND(AVG(se.energy_to_recharge_kwh * st.grid_tariff_inr_kwh), 2) AS estimated_energy_cost_inr,
           ROUND(AVG(se.amount_charged_inr - se.energy_to_recharge_kwh * st.grid_tariff_inr_kwh), 2) AS estimated_contribution_inr
    FROM read_parquet('{SWAPS}') se
    JOIN read_parquet('{STATIONS}') st ON se.station_id = st.station_id
    WHERE se.station_id NOT LIKE 'STN-TST%' AND se.event_type='swap_completed'
    GROUP BY st.city, st.charger_generation ORDER BY estimated_contribution_inr DESC
""").df()
save(e4, "E4_unit_economics_by_city")

# E5. Revenue trend by tariff code month over month
e5 = con.execute(f"""
    SELECT event_month, tariff_code,
           COUNT(*) AS swaps,
           ROUND(SUM(amount_charged_inr), 0) AS revenue_inr,
           ROUND(AVG(amount_charged_inr), 2) AS avg_rev_per_swap
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%' AND event_month IS NOT NULL
    GROUP BY event_month, tariff_code ORDER BY event_month, tariff_code
""").df()
save(e5, "E5_revenue_by_tariff_monthly")

# ============================================================
# F. RIDER RETENTION & BEHAVIOR
# ============================================================
print("\n[F] Rider Retention & Behavior")

# F1. Signup cohort swap activity
f1 = con.execute(f"""
    WITH rider_first AS (
        SELECT rider_id, MIN(event_date::DATE) AS first_swap_date
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%'
        GROUP BY rider_id
    ),
    rider_cohort AS (
        SELECT r.rider_id,
               STRFTIME(r.signup_date::DATE, '%Y-%m') AS signup_cohort,
               r.signup_channel, r.plan_type, r.vehicle_class, r.home_city,
               r.partner_id
        FROM read_parquet('{RIDERS}') r
    )
    SELECT rc.signup_cohort,
           COUNT(DISTINCT rc.rider_id)      AS cohort_size,
           COUNT(DISTINCT CASE WHEN se.event_id IS NOT NULL THEN rc.rider_id END) AS ever_swapped,
           ROUND(100.0 * COUNT(DISTINCT CASE WHEN se.event_id IS NOT NULL THEN rc.rider_id END)
                 / COUNT(DISTINCT rc.rider_id), 2) AS activation_pct
    FROM rider_cohort rc
    LEFT JOIN read_parquet('{SWAPS}') se ON rc.rider_id = se.rider_id
         AND se.station_id NOT LIKE 'STN-TST%'
    WHERE rc.signup_cohort IS NOT NULL
    GROUP BY rc.signup_cohort ORDER BY rc.signup_cohort
""").df()
save(f1, "F1_cohort_activation")

# F2. Monthly retention: riders active in month T who were also active in T-1
f2 = con.execute(f"""
    WITH rider_months AS (
        SELECT DISTINCT rider_id, event_month
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%' AND event_month IS NOT NULL
    )
    SELECT a.event_month,
           COUNT(DISTINCT a.rider_id) AS active_riders,
           COUNT(DISTINCT b.rider_id) AS retained_from_prev_month,
           ROUND(100.0 * COUNT(DISTINCT b.rider_id) / NULLIF(COUNT(DISTINCT a.rider_id), 0), 2) AS retention_pct
    FROM rider_months a
    LEFT JOIN rider_months b
        ON a.rider_id = b.rider_id
        AND b.event_month = STRFTIME(DATE_TRUNC('month', STRPTIME(a.event_month, '%Y-%m') - INTERVAL '1 month'), '%Y-%m')
    GROUP BY a.event_month ORDER BY a.event_month
""").df()
save(f2, "F2_monthly_retention")

# F3. Swap frequency distribution per rider
f3 = con.execute(f"""
    WITH rider_total AS (
        SELECT rider_id, COUNT(*) AS total_swaps
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%'
        GROUP BY rider_id
    )
    SELECT
        CASE
            WHEN total_swaps = 1   THEN '1 swap (one-time)'
            WHEN total_swaps <= 5  THEN '2-5 swaps'
            WHEN total_swaps <= 20 THEN '6-20 swaps'
            WHEN total_swaps <= 50 THEN '21-50 swaps'
            ELSE '51+ swaps (power users)'
        END AS frequency_band,
        COUNT(*) AS riders,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) AS pct_riders,
        ROUND(SUM(total_swaps), 0) AS total_swaps_contributed,
        ROUND(100.0 * SUM(total_swaps) / SUM(SUM(total_swaps)) OVER(), 2) AS pct_swaps
    FROM rider_total
    GROUP BY frequency_band ORDER BY MIN(total_swaps)
""").df()
save(f3, "F3_frequency_distribution")

# F4. Retention by signup channel
f4 = con.execute(f"""
    WITH rider_swaps AS (
        SELECT rider_id, COUNT(*) AS total_swaps,
               MIN(event_date::DATE) AS first_swap,
               MAX(event_date::DATE) AS last_swap
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%'
        GROUP BY rider_id
    )
    SELECT r.signup_channel,
           COUNT(DISTINCT r.rider_id) AS total_riders,
           COUNT(DISTINCT rs.rider_id) AS ever_swapped,
           ROUND(100.0 * COUNT(DISTINCT rs.rider_id) / COUNT(DISTINCT r.rider_id), 2) AS activation_pct,
           ROUND(AVG(rs.total_swaps), 2) AS avg_swaps_per_active_rider,
           ROUND(AVG(DATE_DIFF('day', rs.first_swap, rs.last_swap)), 1) AS avg_active_span_days
    FROM read_parquet('{RIDERS}') r
    LEFT JOIN rider_swaps rs ON r.rider_id = rs.rider_id
    GROUP BY r.signup_channel ORDER BY avg_swaps_per_active_rider DESC
""").df()
save(f4, "F4_retention_by_channel")

# F5. Retention by plan type
f5 = con.execute(f"""
    WITH rider_swaps AS (
        SELECT rider_id, COUNT(*) AS total_swaps
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%'
        GROUP BY rider_id
    )
    SELECT r.plan_type,
           COUNT(DISTINCT r.rider_id) AS total_riders,
           COUNT(DISTINCT rs.rider_id) AS active_riders,
           ROUND(100.0 * COUNT(DISTINCT rs.rider_id) / COUNT(DISTINCT r.rider_id), 2) AS activation_pct,
           ROUND(AVG(rs.total_swaps), 2) AS avg_swaps
    FROM read_parquet('{RIDERS}') r
    LEFT JOIN rider_swaps rs ON r.rider_id = rs.rider_id
    GROUP BY r.plan_type ORDER BY avg_swaps DESC
""").df()
save(f5, "F5_retention_by_plan")

# F6. Impact of swap failures on rider churn (riders who had failures vs not)
f6 = con.execute(f"""
    WITH rider_exp AS (
        SELECT rider_id,
               COUNT(*) AS total_swaps,
               SUM(CASE WHEN event_type='swap_completed' THEN 1 END) AS completed_swaps,
               SUM(CASE WHEN event_type LIKE 'failed%' THEN 1 END)   AS failed_swaps,
               MAX(event_date::DATE) AS last_swap_date
        FROM read_parquet('{SWAPS}')
        WHERE station_id NOT LIKE 'STN-TST%'
        GROUP BY rider_id
    )
    SELECT
        CASE WHEN failed_swaps > 0 THEN 'experienced_failure' ELSE 'no_failure' END AS failure_exp,
        COUNT(*) AS riders,
        ROUND(AVG(total_swaps), 2) AS avg_total_swaps,
        ROUND(AVG(DATE_DIFF('day', '2024-01-01'::DATE, last_swap_date)), 1) AS avg_last_swap_days_since_start,
        ROUND(100.0 * SUM(CASE WHEN total_swaps >= 5 THEN 1 END) / COUNT(*), 2) AS pct_engaged_5plus
    FROM rider_exp
    GROUP BY failure_exp
""").df()
save(f6, "F6_failure_impact_on_retention")

# ============================================================
# CROSS-DIMENSIONAL SUMMARY
# ============================================================
print("\n[X] Cross-dimensional summary metrics")

x1 = con.execute(f"""
    SELECT
        -- Volume
        COUNT(*)                                                                AS total_swaps_excl_test,
        SUM(CASE WHEN event_type='swap_completed' THEN 1 END)                   AS completed_swaps,
        ROUND(100.0 * SUM(CASE WHEN event_type='swap_completed' THEN 1 END) / COUNT(*), 2) AS overall_success_pct,
        -- Revenue
        ROUND(SUM(amount_charged_inr), 0)                                       AS total_revenue_inr,
        ROUND(AVG(amount_charged_inr), 2)                                       AS avg_rev_per_swap_inr,
        ROUND(SUM(list_price_inr), 0)                                           AS total_list_price_inr,
        ROUND(SUM(discount_inr), 0)                                             AS total_discount_inr,
        ROUND(100.0 * SUM(discount_inr) / NULLIF(SUM(list_price_inr), 0), 2)   AS overall_discount_pct,
        -- Riders
        COUNT(DISTINCT rider_id)                                                 AS unique_riders,
        ROUND(1.0 * COUNT(*) / NULLIF(COUNT(DISTINCT rider_id), 0), 2)          AS avg_swaps_per_rider,
        -- Quality
        ROUND(AVG(queue_wait_sec), 1)                                            AS avg_queue_wait_sec,
        SUM(CASE WHEN station_id LIKE 'STN-TST%' THEN 1 END)                    AS test_station_swaps
    FROM read_parquet('{SWAPS}')
    WHERE station_id NOT LIKE 'STN-TST%'
""").df()
save(x1, "X1_executive_summary_metrics")

con.close()

print("\n" + "=" * 70)
print("Analytics engine complete.")
print(f"Output files in: outputs/analytics/")
files = sorted(OUT.glob("*.csv"))
for f in files:
    print(f"  {f.name:<45} {f.stat().st_size/1024:.1f} KB")
