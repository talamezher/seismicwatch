# Business rules and data tests

Every rule below is a decision we made for the question, not a fact about the data. Each is
written once, in `src/transform/rules.py` (rules) or `src/quality/checks.py` (tests), with its
threshold in `src/config.py`, and has unit tests on its edges in `tests/test_rules.py`.
No rule reads the clock: the run date (`as_of`) is passed in.

## Business rules

| Id | Rule | Why |
|---|---|---|
| BR-1 | **Country of an earthquake** = country of its nearest populated place (GeoNames settlement with a known population), if that place is at most **100 km** away; otherwise `XX` (at sea or remote). Only LB, TR and CY count for the question. | The USGS `place` text is free text ("12 km SW of Malatya, Turkey") and not reliable. Neighbouring towns (Syria, Greece, Iraq, Iran...) are loaded from `cities1000`, so an earthquake in Syria near the border is not given to Turkey. |
| BR-2 | **Magnitude bands**: 4.0-4.9 (light), 5.0-5.9 (moderate), 6.0+ (strong or larger). Lower bound included: 5.0 is moderate. | USGS classes, grouped to the three bands of the proposal. |
| BR-3 | **Exposure** of an earthquake = number of settlements and sum of their population within **25 km** and **50 km** of the epicentre (haversine distance). Sections of a city (`PPLX`) and historical or abandoned places (`PPLH`, `PPLQ`, `PPLW`) are not settlements. | A city's districts carry their own population in GeoNames; counting them too would count the same people twice. |
| BR-4 | **Documented major earthquake**: an event matches a row of the Wikipedia list when the dates differ by at most **1 day** (UTC) and the magnitudes by at most **0.3**. Closest date wins, then closest magnitude. | Wikipedia dates are local dates and its magnitudes come from several agencies. |
| BR-5 | **Depth class**: shallow (< 70 km), intermediate (70-300 km), deep (> 300 km). | Standard seismology classes; shallow earthquakes do the most damage. |
| BR-6 | **Study period**: 14 Nov 2016 (included) to 14 Nov 2026 (excluded), and never after the run date. | The question covers ten years; the pipeline keeps running after it. |

## Data tests (run on every pipeline run)

Severity, decided in advance (Week 5): **stop** = load nothing, the warehouse keeps the last good
data; **quarantine** = set the rows aside with the reason, load the rest; **warn** = load, log it.

| Id | Test | Table | Severity |
|---|---|---|---|
| Q1 | event id and time not null | stg_events | quarantine |
| Q2 | event not deleted at the source (USGS status `deleted`) | stg_events | quarantine |
| Q3 | magnitude not null and at least 4.0 (an event can be revised below 4.0) | stg_events | quarantine |
| Q4 | coordinates inside the study box, depth between -5 and 700 km | stg_events | quarantine |
| Q5 | event time in the study period and not after the run date | stg_events | quarantine |
| Q6 | event type is `earthquake` (not a quarry blast or explosion) | stg_events | quarantine |
| P1 | place coordinates valid | stg_places | quarantine |
| K1 | historical earthquake magnitude readable | stg_wiki_quakes | quarantine |
| S1 | at least 1,000 valid earthquakes (catches an extract that returned 12 rows) | stg_events | stop |
| S2 | no more than 1% fewer earthquakes than the warehouse already holds | stg_events | stop |
| S3 | `event_id` unique and not null | stg_events | stop |
| S4 | `place_id` unique and not null | stg_places | stop |
| S5 | row accounting: raw records = older versions + quarantined + loaded | pipeline | stop |
| R1-R4 | relationships: every fact row's date, place, country and magnitude band exist in their dimension | warehouse | stop |
| R5 | every fact row points to the current version in `dim_event_version` | warehouse | stop |
| R6 | every exposure row points to an earthquake and a place that exist | warehouse | stop |
| R7 | SQLite `PRAGMA foreign_key_check` finds nothing | warehouse | stop |
| W1 | magnitude type in the accepted list | stg_events | warn |
| W2 | review status is `reviewed` or `automatic` | stg_events | warn |
| W3 | at most 10% of earthquakes farther than 100 km from any town | events_enriched | warn |
| W4 | at least 50 dated historical earthquakes read from Wikipedia | stg_wiki_quakes | warn |

The R-tests run inside the load transaction: if one fails, the transaction is rolled back.
Results of every run are stored in `data_test_results`, quarantined rows in `quarantine`
(with the test, the reason, the time and the whole record as JSON).
