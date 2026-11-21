# j1939-capture-pipeline

Decode and check J1939 CAN bus captures from heavy trucks, and run them through a data pipeline.

A capture is a few minutes of raw frames recorded from a truck's 9-pin diagnostic connector by the telematics device
fitted in it. The command line tool, `j1939cap`, answers the questions a compatibility engineer asks of one: is the
recording itself intact, which controllers are on the bus, which of them sends speed, engine speed, distance and
engine hours, do multi-packet messages arrive whole, and does the truck broadcast its VIN.

## What fourteen real captures show

The repository carries fourteen captures from twelve trucks, 2,065,558 frames: real operational data of a small
provider of electronic logging devices, de-identified ([data/input/SOURCE.md](data/input/SOURCE.md)). What follows
is a descriptive analysis of those recordings. Every number is in [reports/captures.md](reports/captures.md), which
`j1939cap summarize` rebuilds from the published files and CI compares on every push.

- **The recorder says it dropped nothing, in all fourteen. The data says otherwise.** In two captures runs of rows
  were cut at the wrong byte offset: 64 rows in three runs, out of which 56 frames can be read back. In three
  captures an estimated 7.3%, 9.3% and 12.2% of the frames are missing, against 0.4% to 0.8% in the other nine
  where it can be estimated. Those three are also where multi-packet messages arrive whole least often: 85.3%,
  65.2% and 65.4% of them, against 93.7% to 98.9% elsewhere.
- **"Complete" does not mean the frames are there.** One capture ran its 200 seconds, is marked complete and holds
  no frames. Another was asked for 60 seconds, reports 61.1 seconds and complete, and its last frame is at 33.2
  seconds. A third holds 39 frames per second where the other trucks carry 520 to 1,365.
- **What an electronic logging device must read is not always broadcast.** Vehicle speed, distance and engine hours
  are on the bus in 12 of the 13 captures that hold frames. The VIN is broadcast in 8 of them, by 6 of the 12
  trucks.
- **The same value can come from different controllers, and they can disagree.** On all five Volvos vehicle speed
  comes from source address 0x11 and distance from 0x17, not from the engine at 0x00. Three trucks broadcast
  distance from two controllers at once. On one they agree; on the other two they are 249.02 km and 746,221.675 km
  apart (in the second case the engine says 518,584 km and the controller at 0x31 says 1,264,806 km). A device
  has to pick one.
- **Between 0.2% and 54.9% of a truck's traffic is manufacturer-proprietary**: 42% to 45% on four of the Volvos,
  18.9% on the Peterbilt.

The device and vehicle pseudonyms join the tables of
[telematics-data-foundation](https://github.com/JacobCCMorgan/telematics-data-foundation). All twelve devices are
in its device table, and for four of the six trucks that broadcast a VIN it is the VIN the platform has on record
for that device; for the other two the platform has none.

## Use

```
pip install -e .
j1939cap inspect data/input/captures/DEV-811a412817_20261019T170225Z.csv.gz    # one capture, as text
j1939cap inspect capture.csv --json                                            # the full report
j1939cap summarize data/input/captures --out reports                           # every capture side by side
```

A capture is a recorder CSV, plain or gzip, one frame per row: `timestamp_ms,can_id,data`. The recorder's
`<name>_summary.txt` next to it is read when present. The first command prints:

```
capture    DEV-811a412817_20261019T170225Z.csv.gz: 94,273 frames in 181.0 s (520.7 per second), 4 sources, 86 identifiers, 0.17% proprietary
recorder   requested 180 s, complete yes, reports 0 drops
integrity  0 rows out of frame in 0 runs, 0 frames recovered; steady broadcasts missing 7.3% of their frames (median of 66 identifiers)
transport  231 announced, 197 complete (85.28%), 76 data frames without an announcement
eld        vehicle speed 0x00 wheel_based_vehicle_speed = 0.000
eld        engine speed  0x00 engine_speed = 599.125
eld        distance      0x00 total_vehicle_distance = 518,584.375; 0x00 total_vehicle_distance_hr = 518,584.435; 0x31 total_vehicle_distance_hr = 1,264,806.050  ** sources disagree by 746,221.675 km **
eld        engine hours  0x00 engine_total_hours = 28,149.950
eld        vin           not broadcast, 2 requests seen
dm1        0x00 malfunction_indicator; SPN 97 FMI 4 x1, SPN 651 FMI 7 x1
dm1        0x0B no lamp; no codes
dm1        0x31 no lamp; no codes
```

## How it works

| module | what it does |
|---|---|
| `core/ids.py` | splits a 29-bit identifier into priority, parameter group, source and destination |
| `core/capture.py` | reads and writes the recorder's CSV and its summary file |
| `core/integrity.py` | finds runs of rows cut at the wrong byte offset, sets them aside and reads frames back out of them |
| `core/transport.py` | reassembles multi-packet messages, broadcast and addressed, and says for each whether it arrived whole |
| `core/signals.py` | a small hand-written catalog: speed, engine speed, distance, engine hours, fuel; "not available" and "error" are never scaled into numbers |
| `core/dtc.py` | lamp status and trouble codes of DM1 and DM2 |
| `core/vin.py` | VIN clean-up, check digit, manufacturer, model year |
| `core/report.py` | all of the above for one capture, as one dictionary; estimates the frames a recorder lost from the gaps in steady broadcasts |
| `summarize.py` | one row per capture, as CSV and Markdown |
| `deid/` | keyed pseudonyms and the rules that decide what leaves with a frame; reads its own output back before writing it |

The core has no third-party dependencies.

Two of the checks are inferences and are named as such. The recorder's fifteen-byte record, on which the recovery
of out-of-frame rows rests, was worked out from the files, not from the recorder's source. The share of missing
frames is an estimate from timing: a steady broadcast with a gap of two to five periods is counted as short of one
to four frames, a longer silence as a pause the sender chose, and the capture's figure is the median over its
identifiers.

## Limits

- Twelve trucks, 33 to 240 seconds each. This is evidence about cases, not statistics about a fleet, and the data
  reflects October and November 2026, not how the provider's devices perform today.
- The trucks were not picked at random. Nine of the twelve devices are in the group that gets new firmware first,
  where a device goes after it has shown anomalies in the field, so the sample leans toward difficult trucks.
- A missing frame may have been lost by the recorder or never sent; the capture alone cannot tell, and there is no
  second recorder to compare with. That the loss is about the same across a capture's identifiers points at the
  recorder.
- When two controllers disagree about distance the report shows the gap; it does not say which is right.
- The signal catalog holds twelve signals. The J1939 Digital Annex is licensed and is not redistributed here.
- Only the recorder's own CSV is read so far.
- In the published files 36.0% of the frames have their data bytes replaced by 0xFF, proprietary groups among
  them, so proprietary traffic can be counted but not studied.

## Status

In place: the decoding core, de-identification, the published captures and the comparison report. Next: other
capture formats, a batch pipeline over the same captures, and a web tool that runs the same report in the browser.

## Development

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

`tests/` builds its frames by hand (`tests/bus.py`); `tests/test_published_data.py` checks the published captures
against their manifest.
