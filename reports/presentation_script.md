# VoltRelay Energy Analytics — 3-Minute Presentation Script

**Hackathon Final Presentation | VoltRelay Energy Intelligence**
**Total duration: 3:00 minutes**

---

## [0:00 – 0:30] The Problem — Setting the Stage

> *"VoltRelay Energy runs battery swaps across six Indian cities. Riders pull in, swap out, and go. It's a 3-minute transaction. But three things are quietly breaking the model."*

Three challenges management flagged:
1. **Battery-swap failures are rising** — riders arriving and leaving empty-handed.
2. **New rider retention appears to be declining** — are we losing customers?
3. **Profit per swap is shrinking** — revenue up, but margins under pressure.

> *"We analysed 18 months of real operations — 3.8 million swaps, 20,000 riders, 150 stations, 8 datasets — to find out what's actually happening."*

**[Suggested visual: Title slide with the three business problems and dataset overview card]**

---

## [0:30 – 1:00] The Data — What We Had to Work With

> *"Eight datasets, 820 megabytes, 3.87 million raw records. After cleaning and validating, we had 3.81 million production swaps we could trust."*

Key data facts:
- **Swap events**: 3.87M rows, every swap attempt with outcome, pricing, firmware, battery ID
- **Station telemetry**: 1.49M hourly readings across 150 stations
- **Batteries**: 6,500 packs with State-of-Health readings and retirement status
- **Riders**: 20,000 profiles with cohort, channel, and plan type
- **Support tickets**: 44,000 tickets with resolution data and CSAT

> *"We excluded 62,000 test-station events, resolved 24 chronological anomalies, and ran all analytics on cleaned Parquet files using DuckDB for speed."*

**[Suggested visual: Dataset inventory table or data-flow diagram]**

---

## [1:00 – 1:45] The Discoveries — Three Root Causes

### Discovery 1: Batteries Are the Smoking Gun *(1:00 – 1:20)*

> *"The #1 failure mode — 'no charged battery available' — accounts for 92% of all 144,000 failures. And we know exactly why."*

- **Kyron 2W batteries**: All 1,385 packs are retired at an average State-of-Health of just 60.5%.
- Amptek and Cellora 2W batteries, same age, same usage: **still active at 80% SOH**.
- Kyron is degrading **twice as fast**.
- Batteries below 65% SOH fail at **6.63%** vs **3.20%** for healthy batteries.
- Two failure spikes — May 2024 (87.6% success) and May 2025 (90.0%) — align exactly with high-demand, high-temperature months when low-SOH packs can't hold a full charge.

> *"The Kyron procurement decision is costing VoltRelay thousands of failed swaps every month."*

**[Suggested visual: Grouped bar chart — SOH by supplier; Line chart — monthly success rate with failure spikes labelled]**

### Discovery 2: Discounts Are Eroding Margins *(1:20 – 1:38)*

> *"Revenue per swap has declined from ₹63 to ₹58 since October 2024. Volume is growing — but we're giving more away."*

- Average discount per swap jumped **44%** in November 2024: from ₹4.57 to ₹6.59.
- **ZipDrop** — our highest-volume partner at 584,000 swaps — had a contract amendment that pushed their discount from **12% to 28%**.
- That single amendment cost VoltRelay approximately **₹52 lakh** in additional foregone revenue.
- Gen3 chargers deliver **₹5–8 more contribution** per swap than Gen1 — but Delhi NCR and Jaipur are still running mostly Gen1.

**[Suggested visual: Revenue/swap trend line showing the decline; Partner discount comparison bar]**

### Discovery 3: Retention Is Actually Fine *(1:38 – 1:45)*

> *"Good news: the retention concern is a perception issue, not a real one."*

- Monthly retention is **85–88%** — stable and even improving in early 2025.
- Rider activation is near-universal: **99.6–100%** across all cohorts.
- **76.8% of riders are power users** — 51+ swaps — contributing **97.2% of all volume**.
- These are professional EV operators. They don't churn; they depend on us.

> *"The growing churn count is simply because the base is growing 20x. The rate is healthy."*

**[Suggested visual: Monthly retention trend line; Power-user concentration donut chart]**

---

## [1:45 – 2:20] The Impact — What It Means in Numbers

> *"Let's put numbers on it."*

| Problem | Quantified Impact |
|---|---|
| Kyron battery failures | 144,186 failed swaps over 18 months; avg revenue/swap fell from ₹63 to ₹52 during crisis months |
| ZipDrop discount amendment | ~₹52 lakh foregone on 584K swaps vs original 12% terms |
| Nov 2024 network-wide discount step-up | ~₹7.2 Cr annualised revenue impact at current volumes |
| Delhi NCR + Jaipur underperformance | ~16,000 additional recoverable swaps worth ~₹1 Cr if parity with Bengaluru achieved |
| CSAT blind spot | 65.6% of satisfaction scores missing; 100% missing for unresolved tickets — quality management is flying blind |

> *"The good news: all three root causes are actionable. None of them require fundamental business model changes."*

**[Suggested visual: Impact summary table or waterfall chart showing revenue leakage]**

---

## [2:20 – 2:50] The Recommendations — Three Actions

> *"We recommend three immediate actions, prioritised by impact."*

### Action 1 — Emergency Battery Programme *(2:20 – 2:30)*

> *"Retire every battery below 70% SOH. Blacklist Kyron 2W for future procurement. Pursue warranty claims."*

- Implement real-time SOH monitoring with auto-flagging at 72%.
- Prioritise high-SOH packs for evening peak hours (19h–22h — worst failure window).
- Estimated replacement cost: ₹2–4 Cr for replacement packs. ROI payback within 6 months through recovered swaps.

### Action 2 — Renegotiate ZipDrop, Audit All Nov 2024 Amendments *(2:30 – 2:40)*

> *"Cap volume discounts at 15%. Tie discounts to committed swap volumes and payment compliance."*

- Recovery potential: ₹30+ lakh/year from ZipDrop alone.
- Freeze all partner discount step-ups above 10% pending executive review.

### Action 3 — Accelerate Gen3 Rollout in Delhi NCR and Jaipur *(2:40 – 2:50)*

> *"Gen3 stations are more reliable AND more profitable. The two worst-performing cities are still on Gen1."*

- Gen3 delivers 95%+ success rates; Gen1 cities at 92–93%.
- Gen3 estimated contribution: ₹46–51/swap vs Gen1 ₹40–43/swap.
- Target: Both cities at network parity (>94.5%) within 12 months.

**[Suggested visual: 3-action priority matrix with impact/effort bubbles]**

---

## [2:50 – 3:00] The Outcome — What Success Looks Like

> *"If we execute these three actions, here is the expected outcome by Q4 2026."*

| Metric | Current | Target |
|---|---|---|
| Network swap success rate | 94.02% (91–92% in crisis months) | >95.5% sustained |
| Avg revenue per completed swap | ₹58–60 | ₹62–64 |
| Delhi NCR / Jaipur success rate | 92.2–92.6% | >94.5% |
| CSAT response rate | 34.4% | >60% |
| ZipDrop effective discount | ~21.9% | <15% |

> *"VoltRelay has strong bones: near-universal activation, loyal power users, and a network that works well when the batteries do. The job now is to fix the battery supply chain, discipline the discount structure, and upgrade the remaining Gen1 cities. That's the path to profitable, reliable scale."*

**[Suggested visual: Before/After KPI summary card]**

---

## Notes for Presenter

- **Slides needed**: Title, Dataset overview, Monthly success rate trend, Kyron SOH grouped bar, Failure spike annotated line chart, Revenue/swap decline trend, Partner discount comparison, Monthly retention, Power-user donut, Impact table, 3-action priority matrix, Target KPI card.
- **Data source for all numbers**: `outputs/analytics/*.csv` — computed from 3.81M production swaps using DuckDB.
- **Important caveats to mention**: CSAT is 65.6% missing (mention when discussing customer satisfaction). Unit economics are contribution estimates, not full profit (mention during economics slide).
- **If asked about Kyron**: Kyron 3W packs are also showing early-stage degradation (20.9% retired vs 0% for competitors). Procurement blacklist should cover all Kyron pack types.
- **If asked about machine learning**: This phase focused on descriptive and inferential statistics. A predictive failure model (using SOH, hour, temperature, firmware) would be a natural Phase 3 extension.
