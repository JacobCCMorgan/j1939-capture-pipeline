# Published input

`captures/` holds 79 recordings of raw J1939 traffic, `device_logs/` holds extracts of 58 device logs that go
with them, and `devices.csv` gives make and model year of the 75 trucks. The captures were taken between
2026-10-19 and 2026-11-23 by 75 telematics devices of a small US provider of electronic logging devices, each
from the diagnostic connector of the heavy truck it is fitted in. This is real operational data, de-identified; it
is not a simulation. A capture is taken with the device's remote capture command, a routine support operation at
the provider, on trucks in normal service. The numbers describe those trucks and those devices during those
minutes. They are not statistics about a fleet, and they are not a measure of how the provider's devices perform
in general.

| | |
|---|---|
| Captures | 79, from 75 devices; 76 hold frames, 3 hold none |
| Frames | 15,139,878, in 15,139,956 rows; 399 rows are out of frame and 321 frames can be read back out of them |
| Length asked for | 60 to 240 seconds per capture |
| Size of the captures | 73.6 MB, gzip |
| Device logs | 58 extracts from 57 devices, taken from 9,232,776 log lines |
| Rows in the extracts | 53,328 readings and 15,139 events, 6.8 MB of CSV |

## Files

- `captures/<device key>_<start time, UTC>.csv.gz` is a capture: `timestamp_ms,can_id,data`, one frame per row, in
  the recorder's own layout. Milliseconds since the capture started, the 29-bit identifier as eight hex digits,
  eight data bytes as hex.
- `captures/<same name>_summary.txt` is the recorder's account of the capture, `key=value` per line: firmware
  versions, seconds asked for, start and end time (UTC), duration, records written, its drop counters and whether
  it thinks the capture is complete.
- `captures/MANIFEST.csv` has one line per capture: device key, vehicle key when the truck broadcast a VIN, how
  many frames each rule below touched, size and SHA-256 of the `.csv.gz`.
- `device_logs/<device key>_<first line time, UTC>_readings.csv` has one row per reading cycle of the device, about
  every thirty seconds while it is driving: device state, the VIN it read, speed, engine speed, distance, engine
  hours, the parameter group and source address the distance came from, GPS speed, the distance each source
  address reports, and whether the reading was uploaded.
- `device_logs/<same name>_events.csv` has one row per event: the steps of a capture, the error codes the device
  raised, readings it retried, and trouble on its link to the server.
- `device_logs/MANIFEST.csv` has one line per log: device key, vehicle key, first and last time, lines read and
  used, rows written, and size and SHA-256 of both files.
- `devices.csv` has one line per device: make, model year, and `known_from`. `vin` means the published stand-in
  VINs decode to that make and year. `records` means the truck sends no VIN the manufacturer table can decode,
  and the values come from the provider's records.

## De-identification of the captures

Every frame keeps its timestamp and its identifier. What happens to its eight data bytes depends on the parameter
group. The rules are code, in `src/j1939cap/deid/`, and the counts are in the manifest.

| what | published as | treatment |
|---|---|---|
| Device serial number (file name, `sn` in the summary) | `DEV-` + 10 hex | keyed pseudonym, HMAC-SHA256 |
| VIN (group 0xFEEC, three transport packets) | a stand-in VIN in the same packets; `vehicle_key` (`VIN-` + 10 hex) in the manifest | manufacturer (positions 1 to 3) and model year (position 10) kept; the other positions carry the pseudonym and end in `ZZ`; check digit recomputed |
| A mistyped VIN: seventeen characters, up to three of which cannot be part of a VIN | a stand-in of the same layout | the characters that cannot be part of a VIN stay where they are, so the text is still not a VIN and for the same reason |
| Other text in the VIN group: a fragment, padded text | same length | every VIN character replaced by a pseudonym digit; padding alone is filled with 0xFF |
| Vehicle position (0xFEF3) | same frame | latitude and longitude moved to a 0.05-degree grid (about 5 km) |
| Request (0xEA00) | same frame | the three bytes naming the requested group kept, the rest 0xFF: the recorder stores eight bytes for every frame and the tail of a short one is left over from its buffer |
| Address claim (0xEE00) | same frame | the identity number cleared; manufacturer code and function kept |
| Numeric signal groups on the reviewed list (`KEPT_GROUPS`), trouble codes, acknowledgments | unchanged | |
| Every other group: proprietary, component and software identification (free text), anything not reviewed | data 0xFF | |
| Transport data frames | by message | kept for a complete message of a kept group; 0xFF behind the sequence number for an incomplete message, a masked group, or a packet with no announcement |
| Rows out of frame | rebuilt | recovered frames follow the rules above; every other byte of the run is 0xFF, except the timestamp and identifier that follow a recovered frame |
| Time, distance, engine hours, fuel, firmware versions | unchanged | they are what the project studies |

In all, 66.5% of the frames carry their original data bytes, 33.2% have them filled with 0xFF and 0.3% were
rewritten.

The tool reads its own output back and refuses to write a capture that does not split into the same runs and give
the same frames as the original. Before publishing, each file was also compared with its original: the report
`j1939cap inspect` gives is identical apart from the VIN text, and no serial number, VIN, component or software
identification string of the original survives as bytes anywhere in the data, including across packet boundaries.

Publishing needs the raw captures and the key; neither is in the repository:

```
J1939CAP_DEID_KEY_FILE=/path/to/key j1939cap deidentify raw/*.csv --out data/input/captures
```

## The device log extracts

The device keeps a text log of its own work. The log is free text in the firmware's wording, with serial numbers,
positions, server addresses and upload payloads in it, and it is not published. An extract holds two tables and
nothing else. `src/j1939cap/devicelog/schema.py` lists their columns and events and the type of every value:
numbers, two-digit hex bytes, state names made of letters, error codes, and the VIN.

- Where each value stands in a log is written in a rules file of regular expressions that is kept private with
  the key. A rule can only choose among the columns and events of the schema.
- A value that does not fit its type, or that comes before the first reading cycle of a log, is dropped and
  counted. In these 58 extracts that happened 2 times, all of the second kind.
- The serial number becomes the same `DEV-` pseudonym as in the captures. The VIN becomes the stand-in a capture
  of the same truck carries, so the two can still be compared.
- Positions, time zone, battery and temperature readings, driver events, upload payloads and server addresses are
  not taken.
- One log arrived with its content written twice; the extractor passes over lines it has already used once time
  steps back (10,141 lines in all).
- The extract is read back with the strict reader and searched for the serial number and the VIN before it
  counts as written. Before publishing, all extracts were also searched for every serial number and VIN of the
  raw logs, for non-ASCII characters, addresses and coordinates: none.

```
J1939CAP_DEID_KEY_FILE=/path/to/key J1939CAP_LOG_RULES_FILE=/path/to/rules j1939cap extract-log raw/*.txt --out data/input/device_logs
```

A reader of this repository cannot repeat that step. What backs the extracts is in `reports/pairing.md`: the
device's readings and the capture agree wherever both can be checked.

## Joining other projects

The pseudonyms use the same key and namespaces as the tables of
[telematics-data-foundation](https://github.com/JacobCCMorgan/telematics-data-foundation), a snapshot of the same
provider's platform from September 2026.

- All 75 device keys are in its `dim_device`.
- 60 trucks have a vehicle key in a capture or a log. For 28 of them it equals the `vin_key` on record for that
  device, for 30 the platform had no VIN on record, and for 2 it had a different one.
- `dim_device.is_internal_test` is true for 22 of the 75 devices. The flag marks the group that receives new
  firmware first, and a device is put in that group after it has shown anomalies in the field. These are working
  trucks in service, not bench units, so the captures are kept and not filtered. They are also not a random sample:
  the trucks were chosen to cover five makes and to include some known to be hard to read, 13 of the 75.

## License

The MIT license covers the code. The files under `data/` and `reports/` are real operational data, published so the
results here can be checked; they are not licensed for reuse or redistribution.
