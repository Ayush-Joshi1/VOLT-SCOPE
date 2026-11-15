# VoltRelay Energy Analytics — Data Dictionary
## Phase 1: Data Audit and Preparation

> [!NOTE]
> This dictionary describes all eight raw datasets in `data/raw/`. It documents column names, data types, business meaning, observed ranges, and known data-quality notes based on Phase 1 profiling.

---

## 1. `swap_events.csv` — Core Transaction Log

**Grain:** One row per battery-swap attempt
**Approx. rows:** ~3,900,000
**Primary key:** `event_id`
**Date range:** January 2024 – June 2025

| Column | Type | Description | Notes |
|---|---|---|---|
| `event_id` | string | Unique swap event identifier (format: `SW-XXXXXXXXX`) | Primary key; confirmed unique |
| `rider_id` | string | Rider performing the swap | FK → `riders.rider_id` |
| `station_id` | string | Station where swap occurred | FK → `stations.station_id` |
| `event_ts` | datetime | Timestamp of the swap event (naive, assumed IST) | See chronology exception §9 |
| `event_type` | category | Outcome: `swap_completed`, `swap_failed`, `swap_abandoned`, etc. | Key analytical field |
| `attempt_seq` | integer | Attempt number within a session (1 = first attempt) | Use for failure analysis |
| `queue_wait_sec` | integer | Seconds waited in queue before swap | Relates to service congestion |
| `battery_in_id` | string | Battery received by station (depleted) | FK → `batteries.battery_id`; nullable |
| `battery_out_id` | string | Battery dispensed to rider (charged) | FK → `batteries.battery_id`; nullable |
| `soc_in_pct` | float | State of Charge of incoming battery (%) | Some values >100%; await BMS definition |
| `soh_in_pct` | float | State of Health of incoming battery (%) | Some values >100 (e.g., 100.6%); await BMS definition |
| `soc_out_pct` | float | State of Charge of outgoing battery (%) | Same caveats |
| `soh_out_pct` | float | State of Health of outgoing battery (%) | Same caveats |
| `km_since_last_swap` | float | Distance ridden since last swap (km) | Some negative values and >1000 km outliers; investigate |
| `tariff_code` | category | Pricing tier applied (`PARTNER`, `STANDARD`, `PEAK`, etc.) | Join to fleet_partners for partner discount |
| `list_price_inr` | float | Base list price before discount (INR) | Gross revenue dimension |
| `discount_inr` | float | Discount applied (INR) | May link to partner contract |
| `amount_charged_inr` | float | Net revenue collected (INR) | `list_price - discount`; key profit metric |
| `payment_mode` | category | How payment was collected (`partner_invoice`, `upi`, `wallet`, etc.) | |
| `energy_to_recharge_kwh` | float | Energy needed to recharge incoming battery (kWh) | Operational cost input |
| `station_firmware` | category | Firmware version running at station during swap | Critical: v3.2.0 has chronology issues |
| `sync_mode` | category | How event was recorded (`realtime`, `offline_batch`) | Offline events risk near-duplication |

**Derived columns added in cleaning:**
- `event_date` — date portion of `event_ts` (for daily aggregation)
- `event_hour` — hour of day (0–23)
- `event_month` — YYYY-MM period string (for cohort analysis)

---

## 2. `station_hourly_status.csv` — Station Telemetry

**Grain:** One row per station per hour
**Approx. rows:** ~1,100,000
**Composite primary key:** `station_id` + `hour_start`
**Date range:** January 2024 – June 2025

| Column | Type | Description | Notes |
|---|---|---|---|
| `station_id` | string | Station identifier | FK → `stations.station_id` |
| `hour_start` | datetime | Start of the hourly measurement window | Key for joining to swap_events |
| `charged_2w_avg` | float | Average charged 2W packs available | Inventory health indicator |
| `charged_2w_min` | integer | Minimum charged 2W packs during the hour | Stock-out risk |
| `charged_3w_min` | integer | Minimum charged 3W packs during the hour | |
| `packs_charging` | integer | Packs actively charging during the hour | |
| `packs_quarantined` | integer | Packs removed from service | Degradation / fault indicator |
| `chargers_online` | integer | Active charger slots | Hardware availability |
| `ambient_temp_c` | float | Ambient temperature (°C) | Environmental factor |
| `cabinet_temp_c` | float | Cabinet/enclosure temperature (°C) | Thermal management |
| `avg_charge_minutes` | float | Average charge cycle duration (minutes) | Throughput indicator |
| `outage_minutes` | integer | Station downtime minutes in the hour | Reliability metric |
| `grid_kwh` | float | Grid energy consumed (kWh) | Operational cost |
| `telemetry_status` | category | Signal quality: `ok`, `partial`, `missing` | Filter out `missing` before analysis |

**Derived columns added in cleaning:**
- `telemetry_date` — date portion of `hour_start`

> [!WARNING]
> Missing telemetry rows ≠ zero inventory. Absent rows mean the station did not report, not that it had zero packs. Always condition telemetry analysis on `telemetry_status = 'ok'`.

---

## 3. `riders.csv` — Rider Profiles

**Grain:** One row per rider
**Approx. rows:** ~150,000
**Primary key:** `rider_id`

| Column | Type | Description | Notes |
|---|---|---|---|
| `rider_id` | string | Unique rider identifier (format: `RD-XXXXXXX`) | Primary key |
| `partner_id` | string | Fleet partner the rider belongs to | FK → `fleet_partners.partner_id`; NULL for retail/independent riders |
| `vehicle_class` | category | Vehicle type: `2W` (two-wheeler) or `3W` (three-wheeler) | |
| `vehicle_model` | category | Specific vehicle model name | |
| `home_zone` | string | Rider's home operational zone (city + sub-area) | |
| `signup_date` | date | Date rider registered | Key for cohort analysis |
| `signup_channel` | category | Acquisition channel (`field_agent`, `partner_onboarding`, `app`, etc.) | Retention analysis dimension |
| `plan_type` | category | Pricing plan (`partner_billed`, `retail_prepaid`, `retail_postpaid`) | |
| `declared_shift` | category | Rider's stated working shift (`day`, `night`, `mixed`) | |
| `age_band` | category | Age group bracket | |
| `kyc_verified` | boolean | Whether KYC verification is complete | |
| `home_city` | category | Rider's home city | Check for spelling variants |

**Derived columns added in cleaning:**
- `signup_cohort` — YYYY-MM period (for monthly retention cohort analysis)

---

## 4. `batteries.csv` — Battery Pack Inventory

**Grain:** One row per battery pack
**Approx. rows:** ~8,000
**Primary key:** `battery_id`

| Column | Type | Description | Notes |
|---|---|---|---|
| `battery_id` | string | Unique battery identifier (format: `BAT-2W-XXXXXX`) | Primary key |
| `pack_type` | category | Physical pack category (`2W_2.1kWh`, `3W_4.2kWh`, etc.) | |
| `supplier` | category | Battery supplier/manufacturer name | |
| `manufacturing_lot` | category | Production batch identifier | Useful for defect tracing |
| `manufacture_date` | date | Date of manufacture | |
| `commission_date` | date | Date deployed to network | |
| `rated_capacity_kwh` | float | Nominal energy capacity (kWh) | |
| `purchase_cost_inr` | float | Acquisition cost (INR) | Depreciation/unit economics |
| `initial_soh_pct` | float | State of Health at commissioning (%) | Baseline for degradation tracking |
| `bms_firmware` | category | Battery Management System firmware version | May explain SoH anomalies |
| `retired_date` | date | Date retired from service | NULL = active battery |
| `retirement_reason` | category | Reason for retirement | NULL = active battery (expected) |
| `current_soh_pct` | float | Current State of Health (%) | Key degradation metric |

**Derived columns added in cleaning:**
- `is_retired` — boolean flag (retired_date is not null)

---

## 5. `stations.csv` — Station Master Data

**Grain:** One row per station
**Approx. rows:** ~200–300 stations
**Primary key:** `station_id`

| Column | Type | Description | Notes |
|---|---|---|---|
| `station_id` | string | Unique station identifier (e.g., `STN-BLR-001`) | Primary key |
| `city` | category | City: Bengaluru, Delhi NCR, Hyderabad, Mumbai, Pune, Jaipur | FK → `city_daily_context.city` |
| `zone` | string | Sub-city zone label | |
| `latitude` | float | GPS latitude | |
| `longitude` | float | GPS longitude | |
| `location_type` | category | Physical setting (`transit_hub`, `commercial_hub`, `residential`, etc.) | |
| `host_type` | category | Host venue type (`mall_parking`, `kirana`, `petrol_station`, etc.) | |
| `commissioned_date` | date | Date station began operating | Key for pre-commission event investigation |
| `decommissioned_date` | date | Date station closed | NULL = currently active |
| `expansion_wave` | category | Network rollout phase (`Launch`, `Wave2`, `Wave3`) | |
| `charger_generation` | category | Hardware generation (`Gen1`, `Gen2`, `Gen3`) | Failure analysis dimension |
| `slots_2w` | integer | 2W battery slot capacity | |
| `slots_3w` | integer | 3W battery slot capacity | |
| `inventory_target_2w` | integer | Target 2W inventory level | |
| `inventory_target_3w` | integer | Target 3W inventory level | |
| `monthly_rent_inr` | float | Monthly site rental cost (INR) | Fixed operating cost |
| `monthly_maintenance_inr` | float | Monthly maintenance cost (INR) | Fixed operating cost |
| `grid_tariff_inr_kwh` | float | Electricity cost (INR/kWh) | Variable operating cost |
| `connectivity_tier` | category | Network connectivity quality (`good`, `poor`, `fair`) | Offline sync risk factor |
| `firmware_version` | category | Current station firmware | v3.2.0 has known chronology anomaly |
| `firmware_updated_date` | date | Date of last firmware update | |
| `competitor_within_1_5km_since` | date | Date a competitor opened within 1.5 km | NULL = no nearby competitor |

**Derived columns added in cleaning:**
- `is_decommissioned` — boolean flag

---

## 6. `support_tickets.csv` — Customer Support Log

**Grain:** One row per support ticket
**Approx. rows:** ~50,000–100,000
**Primary key:** `ticket_id`

| Column | Type | Description | Notes |
|---|---|---|---|
| `ticket_id` | string | Unique ticket identifier (format: `TK-XXXXXXX`) | Primary key |
| `rider_id` | string | Rider who filed the ticket | FK → `riders.rider_id` |
| `station_id` | string | Station referenced in ticket | FK → `stations.station_id`; nullable |
| `battery_id` | string | Battery referenced in ticket | FK → `batteries.battery_id`; nullable |
| `created_ts` | datetime | Ticket creation timestamp | |
| `channel` | category | Submission channel (`call_center`, `app`, `partner_escalation`, etc.) | |
| `category` | category | Issue type (`billing_dispute`, `long_queue`, `swap_failure`, etc.) | Key for support analysis |
| `rider_comment` | text | Free-text rider description | Preserve as-is; NLP use in Phase 2+ |
| `priority` | category | Ticket priority (`low`, `medium`, `high`, `critical`) | |
| `resolution_status` | category | Outcome (`resolved`, `pending`, `duplicate`, `escalated`) | |
| `resolution_hours` | float | Hours to resolution | NULL if unresolved |
| `csat_score` | float | Customer satisfaction score (typically 1–5) | High missingness; response bias possible |

**Derived columns added in cleaning:**
- `ticket_date` — date portion of `created_ts`

> [!IMPORTANT]
> `csat_score` has significant missing values. The missingness pattern is non-random (varies by `resolution_status`). Analyse CSAT only within resolved tickets and test for response bias.

---

## 7. `city_daily_context.csv` — External Context

**Grain:** One row per city per calendar day
**Approx. rows:** ~3,300 (6 cities × ~549 days)
**Composite primary key:** `city` + `date`

| Column | Type | Description | Notes |
|---|---|---|---|
| `city` | category | City name (matches `stations.city`) | Join dimension |
| `date` | date | Calendar date | Join dimension |
| `max_temp_c` | float | Maximum temperature (°C) | |
| `min_temp_c` | float | Minimum temperature (°C) | |
| `rainfall_mm` | float | Rainfall accumulation (mm) | |
| `heat_alert` | boolean | Official heat advisory issued | |
| `flood_disruption` | boolean | Flooding disruption reported | |
| `festival_or_event` | string | Named festival or event (NULL if none) | |
| `is_public_holiday` | boolean | Public holiday flag | |
| `grid_outage_hours` | float | Hours of grid disruption | |
| `competitor_promo_active` | boolean | Competitor running a promotion | |
| `ecommerce_sale_event` | boolean | Major e-commerce sale active | |

> [!CAUTION]
> Never join `city_daily_context` to `swap_events` on `city` alone. Always include `date` in the join condition. A city-only join multiplies each swap by all 549 days (fan-out factor ~549×).

---

## 8. `fleet_partners.csv` — B2B Partner Contracts

**Grain:** One row per fleet partner
**Approx. rows:** ~8–15 partners
**Primary key:** `partner_id`

| Column | Type | Description | Notes |
|---|---|---|---|
| `partner_id` | string | Unique partner identifier (format: `FP-XX`) | Primary key |
| `partner_name` | string | Company name | |
| `partner_segment` | category | Business type (`food_delivery`, `ecom_logistics`, `ride_hailing`, etc.) | |
| `vehicle_class` | category | Vehicle class served (`2W`, `3W`) | |
| `contract_type` | category | Pricing structure (`volume_tiered`, `pay_per_swap`) | |
| `contract_start_date` | date | Contract effective date | |
| `discount_pct` | float | Discount percentage on list price | Key for revenue analysis |
| `amendment_date` | date | Date of contract amendment | NULL if no amendment |
| `discount_pct_after_amendment` | float | Revised discount after amendment | NULL if no amendment |
| `peak_surcharge_billable` | category | Whether peak surcharges apply (`Y`/`N`) | |
| `payment_terms_days` | integer | Invoice payment terms (days) | Cash-flow dimension |
| `cities_active` | string | Pipe-delimited list of active cities | Parse: `BLR\|DEL\|HYD\|...` |

---

## Chronology Exception — 24 Events

**Location:** `data/interim/chronology_exceptions_24_events.parquet`

These 24 events belong to firmware version `v3.2.0` and are characterised by:

1. `event_date` = `stations.commissioned_date` − 1 day
2. No station-hour telemetry exists on the `event_date`
3. Station-hour telemetry exists on the `commissioned_date`
4. `event_ts` + 5h30m maps to an hour present in telemetry

**Disposition:** Retained in full analysis without timestamp modification. The likely explanation is that v3.2.0 firmware recorded timestamps in UTC rather than IST (+05:30), causing apparent pre-commission placement. No corrective action is taken in Phase 1.

---

## Join Guide

```
swap_events  ──(rider_id)──►  riders  ──(partner_id)──►  fleet_partners
swap_events  ──(station_id)──►  stations  ──(city)──►  city_daily_context [+date join]
swap_events  ──(battery_in_id / battery_out_id)──►  batteries
swap_events  ──(station_id + floor(event_ts,'h'))──►  station_hourly_status
support_tickets  ──(rider_id)──►  riders
support_tickets  ──(station_id)──►  stations [nullable]
support_tickets  ──(battery_id)──►  batteries [nullable]
```

**Cardinality rules:**
- Always `GROUP BY` appropriately to avoid row multiplication
- `station_hourly_status` joins require both `station_id` AND `hour_start` (floored to hour)
- `city_daily_context` joins require both `city` AND `date`
- Batteries appear twice per swap (`in` + `out`) — count battery instances carefully
