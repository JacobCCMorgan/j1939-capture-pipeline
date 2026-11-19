# j1939-capture-pipeline

Decode and check J1939 CAN bus captures from heavy trucks, and run them through a data pipeline.

A capture is a few minutes of raw frames recorded from a truck's 9-pin diagnostic connector by a telematics device.
The command line tool, `j1939cap`, answers the questions a compatibility engineer asks of one: is the recording itself
intact, which controllers are on the bus, which of them sends speed, engine speed, distance and engine hours, do
multi-packet messages arrive whole, and does the truck broadcast its VIN.

Status: work in progress. The decoding core comes first; the batch pipeline, the web tool and the published
de-identified captures follow.

## Use

```
j1939cap inspect capture.csv          # a short text report
j1939cap inspect capture.csv --json   # the full report
```

The capture is a recorder CSV, one frame per row: `timestamp_ms,can_id,data`. A `capture_summary.txt` next to it is read
when present.

The text report for the made-up twenty-second bus the tests use (`tests/bus.py`), where the engine and the instrument
cluster disagree about distance:

```
capture    capture.csv: 2,298 frames in 20.0 s (114.9 per second), 4 sources, 10 identifiers, 0.0% proprietary
recorder   requested 20 s, complete yes, reports 0 drops
integrity  0 rows out of frame in 0 runs, 0 frames recovered; steady broadcasts missing 0.0% of their frames (median of 2 identifiers)
transport  4 announced, 4 complete (100.0%), 0 data frames without an announcement
eld        vehicle speed 0x00 wheel_based_vehicle_speed = 89.500
eld        engine speed  0x00 engine_speed = 1,200.000
eld        distance      0x00 total_vehicle_distance_hr = 500,000.475; 0x17 total_vehicle_distance_hr = 499,688.075  ** sources disagree by 312.400 km **
eld        engine hours  0x00 engine_total_hours = 12,345.500
eld        vin           broadcast, 1 requests seen
vin        0x00 1FUAB1CD7LE000001 (check digit ok, Freightliner, 2020) x4
dm1        0x0B amber_warning; SPN 1569 FMI 31 x2
```

## Layout

- `src/j1939cap/core/` - identifiers, capture reader, recorder integrity, transport protocol, signals, trouble codes, VIN and
  the capture report. No third-party dependencies.
- `src/j1939cap/cli.py` - the command line.
- `tests/` - unit tests built on hand-made frames.

## Development

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q
```
