2026-11-25 13:40:40 INFO    seismicwatch                 run 20261125T134040Z | mode replay | as of 2026-11-25 | log /home/runner/work/seismicwatch/seismicwatch/logs/pipeline_20261125T134040Z.log
2026-11-25 13:40:42 INFO    seismicwatch                 staging: events {'raw_records': 2758, 'older_versions_removed': 5, 'staged_events': 2753} | places {'raw_records': 306958, 'not_populated_place': 75239, 'outside_region': 165854, 'duplicates_removed': 2995, 'staged_places': 62870, 'valid_places': 14922} | wikipedia {'raw_records': 100, 'duplicates_removed': 1, 'staged_rows': 99, 'valid_rows': 83}
2026-11-25 13:40:43 INFO    src.transform.enrich         join events -> nearest place (distance)       2,752 rows in,   2,752 out
2026-11-25 13:40:43 INFO    src.transform.enrich         join events -> nearest place (attributes)     2,752 rows in,   2,752 out, 0 without a match
2026-11-25 13:40:43 INFO    src.transform.enrich         join events -> exposure counts                2,752 rows in,   2,752 out
2026-11-25 13:40:43 INFO    src.transform.enrich         join events -> Wikipedia historical list      2,752 rows in,   2,752 out, 2,736 without a match
2026-11-25 13:40:43 INFO    src.transform.enrich         join exposure pairs -> places               151,611 rows in, 151,611 out
2026-11-25 13:40:43 INFO    src.transform.enrich         exposure: 151,611 earthquake-place pairs within 50 km
2026-11-25 13:40:43 INFO    src.quality.checks           test Q1  quarantine PASS event id and time not null
2026-11-25 13:40:43 INFO    src.quality.checks           test Q2  quarantine PASS event not deleted at the source
2026-11-25 13:40:43 INFO    src.quality.checks           test Q3  quarantine PASS magnitude not null and at least 4.0
2026-11-25 13:40:43 INFO    src.quality.checks           test Q4  quarantine PASS coordinates inside the study box, depth -5 to 700 km
2026-11-25 13:40:43 INFO    src.quality.checks           test Q5  quarantine PASS event time in the study period and not after the run date
2026-11-25 13:40:43 INFO    src.quality.checks           test Q6  quarantine PASS event type is earthquake
2026-11-25 13:40:43 WARNING src.quality.checks           test Q7  quarantine FAIL not a duplicate of another event (same earthquake under two ids) | 1 row(s) quarantined
2026-11-25 13:40:43 INFO    src.quality.checks           test P1  quarantine PASS place coordinates valid
2026-11-25 13:40:43 WARNING src.quality.checks           test K1  quarantine FAIL historical earthquake magnitude readable
2026-11-25 13:40:43 INFO    src.quality.checks           test S1  stop       PASS at least 1,000 valid earthquakes | 2,752 valid earthquakes
2026-11-25 13:40:43 INFO    src.quality.checks           test S2  stop       PASS no more than 1% fewer earthquakes than the warehouse holds | 2,752 now vs 2,752 loaded last time (an extract that silently returned less data must not replace good data)
2026-11-25 13:40:43 INFO    src.quality.checks           test S3  stop       PASS event_id unique and not null
2026-11-25 13:40:43 INFO    src.quality.checks           test S4  stop       PASS place_id unique and not null
2026-11-25 13:40:43 INFO    src.quality.checks           test W1  warn       PASS magnitude type in the accepted list
2026-11-25 13:40:43 INFO    src.quality.checks           test W2  warn       PASS review status is reviewed or automatic
2026-11-25 13:40:43 INFO    src.quality.checks           test W3  warn       PASS at most 10% of earthquakes farther than 100 km from any town | 127 (4.6%) at sea or remote
2026-11-25 13:40:43 WARNING src.quality.checks           test W5  warn       FAIL at most 50% of depths fixed at the 10 km default | 68% of depths are the USGS default: the depth classes are rough
2026-11-25 13:40:43 WARNING src.quality.checks           test W6  warn       FAIL USGS place text without lost characters ('?') | 298 place texts lost their accented letters at the source; the country comes from GeoNames, not from this text
2026-11-25 13:40:43 INFO    src.quality.checks           test W4  warn       PASS at least 50 dated historical earthquakes read from Wikipedia | 83 valid rows
2026-11-25 13:40:43 INFO    src.quality.checks           test S5  stop       PASS row accounting: raw = older versions + quarantined + loaded | 2,758 raw = 5 older versions + 1 quarantined + 2,752 loaded
2026-11-25 13:40:47 INFO    src.quality.checks           test R1  stop       PASS fact_earthquake.date_key exists in dim_date
2026-11-25 13:40:47 INFO    src.quality.checks           test R2  stop       PASS fact_earthquake.place_key exists in dim_place
2026-11-25 13:40:47 INFO    src.quality.checks           test R3  stop       PASS fact_earthquake.country_key exists in dim_country
2026-11-25 13:40:47 INFO    src.quality.checks           test R4  stop       PASS fact_earthquake.magnitude_band_key exists in dim_magnitude_band
2026-11-25 13:40:47 INFO    src.quality.checks           test R5  stop       PASS fact_earthquake.event_version_key is the current version in dim_event_version
2026-11-25 13:40:47 INFO    src.quality.checks           test R6  stop       PASS fact_place_exposure rows point to an earthquake and a place that exist
2026-11-25 13:40:47 INFO    src.quality.checks           test R7  stop       PASS SQLite foreign key check
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse dim_date                3,663 rows  sha256 130d646bea5eb5e6
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse dim_country                21 rows  sha256 db74eca8ecae33d0
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse dim_place              14,922 rows  sha256 2a609c89fbfd94fb
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse dim_magnitude_band          3 rows  sha256 d317f964ea96fd90
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse dim_event_version       2,752 rows  sha256 be21786036380a07
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse fact_earthquake         2,752 rows  sha256 e9feadb456a9d92d
2026-11-25 13:40:47 INFO    seismicwatch                 warehouse fact_place_exposure   151,611 rows  sha256 cd0e966e97a59f8e
2026-11-25 13:40:47 INFO    seismicwatch                 ROWS  raw USGS records 2,758 -> older versions 5 -> quarantined 1 -> loaded 2,752
2026-11-25 13:40:47 INFO    seismicwatch                 run 20261125T134040Z finished: success in 8 s, warehouse fingerprint b152f50bb04c8535
2026-11-25 13:40:48 INFO    seismicwatch                 run 20261125T134048Z | mode replay | as of 2026-11-25 | log /home/runner/work/seismicwatch/seismicwatch/logs/pipeline_20261125T134048Z.log
2026-11-25 13:40:50 INFO    seismicwatch                 staging: events {'raw_records': 2758, 'older_versions_removed': 5, 'staged_events': 2753} | places {'raw_records': 306958, 'not_populated_place': 75239, 'outside_region': 165854, 'duplicates_removed': 2995, 'staged_places': 62870, 'valid_places': 14922} | wikipedia {'raw_records': 100, 'duplicates_removed': 1, 'staged_rows': 99, 'valid_rows': 83}
2026-11-25 13:40:51 INFO    src.transform.enrich         join events -> nearest place (distance)       2,752 rows in,   2,752 out
2026-11-25 13:40:51 INFO    src.transform.enrich         join events -> nearest place (attributes)     2,752 rows in,   2,752 out, 0 without a match
2026-11-25 13:40:51 INFO    src.transform.enrich         join events -> exposure counts                2,752 rows in,   2,752 out
2026-11-25 13:40:51 INFO    src.transform.enrich         join events -> Wikipedia historical list      2,752 rows in,   2,752 out, 2,736 without a match
2026-11-25 13:40:51 INFO    src.transform.enrich         join exposure pairs -> places               151,611 rows in, 151,611 out
2026-11-25 13:40:51 INFO    src.transform.enrich         exposure: 151,611 earthquake-place pairs within 50 km
2026-11-25 13:40:51 INFO    src.quality.checks           test Q1  quarantine PASS event id and time not null
2026-11-25 13:40:51 INFO    src.quality.checks           test Q2  quarantine PASS event not deleted at the source
2026-11-25 13:40:51 INFO    src.quality.checks           test Q3  quarantine PASS magnitude not null and at least 4.0
2026-11-25 13:40:51 INFO    src.quality.checks           test Q4  quarantine PASS coordinates inside the study box, depth -5 to 700 km
2026-11-25 13:40:51 INFO    src.quality.checks           test Q5  quarantine PASS event time in the study period and not after the run date
2026-11-25 13:40:51 INFO    src.quality.checks           test Q6  quarantine PASS event type is earthquake
2026-11-25 13:40:51 WARNING src.quality.checks           test Q7  quarantine FAIL not a duplicate of another event (same earthquake under two ids) | 1 row(s) quarantined
2026-11-25 13:40:51 INFO    src.quality.checks           test P1  quarantine PASS place coordinates valid
2026-11-25 13:40:51 WARNING src.quality.checks           test K1  quarantine FAIL historical earthquake magnitude readable
2026-11-25 13:40:51 INFO    src.quality.checks           test S1  stop       PASS at least 1,000 valid earthquakes | 2,752 valid earthquakes
2026-11-25 13:40:51 INFO    src.quality.checks           test S2  stop       PASS no more than 1% fewer earthquakes than the warehouse holds | 2,752 now vs 2,752 loaded last time (an extract that silently returned less data must not replace good data)
2026-11-25 13:40:51 INFO    src.quality.checks           test S3  stop       PASS event_id unique and not null
2026-11-25 13:40:51 INFO    src.quality.checks           test S4  stop       PASS place_id unique and not null
2026-11-25 13:40:51 INFO    src.quality.checks           test W1  warn       PASS magnitude type in the accepted list
2026-11-25 13:40:51 INFO    src.quality.checks           test W2  warn       PASS review status is reviewed or automatic
2026-11-25 13:40:51 INFO    src.quality.checks           test W3  warn       PASS at most 10% of earthquakes farther than 100 km from any town | 127 (4.6%) at sea or remote
2026-11-25 13:40:51 WARNING src.quality.checks           test W5  warn       FAIL at most 50% of depths fixed at the 10 km default | 68% of depths are the USGS default: the depth classes are rough
2026-11-25 13:40:51 WARNING src.quality.checks           test W6  warn       FAIL USGS place text without lost characters ('?') | 298 place texts lost their accented letters at the source; the country comes from GeoNames, not from this text
2026-11-25 13:40:51 INFO    src.quality.checks           test W4  warn       PASS at least 50 dated historical earthquakes read from Wikipedia | 83 valid rows
2026-11-25 13:40:51 INFO    src.quality.checks           test S5  stop       PASS row accounting: raw = older versions + quarantined + loaded | 2,758 raw = 5 older versions + 1 quarantined + 2,752 loaded
2026-11-25 13:40:55 INFO    src.quality.checks           test R1  stop       PASS fact_earthquake.date_key exists in dim_date
2026-11-25 13:40:55 INFO    src.quality.checks           test R2  stop       PASS fact_earthquake.place_key exists in dim_place
2026-11-25 13:40:55 INFO    src.quality.checks           test R3  stop       PASS fact_earthquake.country_key exists in dim_country
2026-11-25 13:40:55 INFO    src.quality.checks           test R4  stop       PASS fact_earthquake.magnitude_band_key exists in dim_magnitude_band
2026-11-25 13:40:55 INFO    src.quality.checks           test R5  stop       PASS fact_earthquake.event_version_key is the current version in dim_event_version
2026-11-25 13:40:55 INFO    src.quality.checks           test R6  stop       PASS fact_place_exposure rows point to an earthquake and a place that exist
2026-11-25 13:40:55 INFO    src.quality.checks           test R7  stop       PASS SQLite foreign key check
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse dim_date                3,663 rows  sha256 130d646bea5eb5e6
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse dim_country                21 rows  sha256 db74eca8ecae33d0
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse dim_place              14,922 rows  sha256 2a609c89fbfd94fb
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse dim_magnitude_band          3 rows  sha256 d317f964ea96fd90
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse dim_event_version       2,752 rows  sha256 be21786036380a07
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse fact_earthquake         2,752 rows  sha256 e9feadb456a9d92d
2026-11-25 13:40:55 INFO    seismicwatch                 warehouse fact_place_exposure   151,611 rows  sha256 cd0e966e97a59f8e
2026-11-25 13:40:55 INFO    seismicwatch                 ROWS  raw USGS records 2,758 -> older versions 5 -> quarantined 1 -> loaded 2,752
2026-11-25 13:40:55 INFO    seismicwatch                 run 20261125T134048Z finished: success in 7 s, warehouse fingerprint b152f50bb04c8535

| table | rows | sha256 run 1 | sha256 run 2 | same |
|---|---:|---|---|---|
| dim_date | 3,663 | 130d646bea5eb5e6 | 130d646bea5eb5e6 | yes |
| dim_country | 21 | db74eca8ecae33d0 | db74eca8ecae33d0 | yes |
| dim_place | 14,922 | 2a609c89fbfd94fb | 2a609c89fbfd94fb | yes |
| dim_magnitude_band | 3 | d317f964ea96fd90 | d317f964ea96fd90 | yes |
| dim_event_version | 2,752 | be21786036380a07 | be21786036380a07 | yes |
| fact_earthquake | 2,752 | e9feadb456a9d92d | e9feadb456a9d92d | yes |
| fact_place_exposure | 151,611 | cd0e966e97a59f8e | cd0e966e97a59f8e | yes |

The second run produced exactly the same tables.
