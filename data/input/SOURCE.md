# Published input

`captures/` holds fourteen recordings of raw J1939 traffic. They were taken between 2026-10-19 and 2026-11-14 by
twelve telematics devices of a small US provider of electronic logging devices, each from the diagnostic connector
of the heavy truck it is fitted in. This is real operational data, de-identified; it is not a simulation. The
numbers describe those trucks and those devices during those minutes. They are not statistics about a fleet, and
they say nothing about how the provider's devices perform today.

| | |
|---|---|
| Captures | 14, from 12 devices; 13 hold frames, 1 holds none |
| Frames | 2,065,558, in 2,065,566 rows; 64 rows are out of frame and 56 frames can be read back out of them |
| Length asked for | 60 to 240 seconds per capture |
| Size | 9.8 MB, gzip |

## Files

- `<device key>_<start time, UTC>.csv.gz` is the capture: `timestamp_ms,can_id,data`, one frame per row, in the
  recorder's own layout. Milliseconds since the capture started, the 29-bit identifier as eight hex digits, eight
  data bytes as hex.
- `<same name>_summary.txt` is the recorder's account of the capture, `key=value` per line: firmware versions,
  seconds asked for, start and end time (UTC), duration, records written, its drop counters and whether it thinks
  the capture is complete.
- `MANIFEST.csv` has one line per capture: device key, vehicle key when the truck broadcast a VIN, how many
  frames each rule below touched, size and SHA-256 of the `.csv.gz`.

## De-identification

Every frame keeps its timestamp and its identifier. What happens to its eight data bytes depends on the parameter
group. The rules are code, in `src/j1939cap/deid/`, and the counts are in the manifest.

| what | published as | treatment |
|---|---|---|
| Device serial number (file name, `sn` in the summary) | `DEV-` + 10 hex | keyed pseudonym, HMAC-SHA256 |
| VIN (group 0xFEEC, three transport packets) | a stand-in VIN in the same packets; `vehicle_key` (`VIN-` + 10 hex) in the manifest | manufacturer (positions 1 to 3) and model year (position 10) kept; the other positions carry the pseudonym and end in `ZZ`; check digit recomputed |
| Vehicle position (0xFEF3) | same frame | latitude and longitude moved to a 0.05-degree grid (about 5 km) |
| Request (0xEA00) | same frame | the three bytes naming the requested group kept, the rest 0xFF: the recorder stores eight bytes for every frame and the tail of a short one is left over from its buffer |
| Numeric signal groups on the reviewed list (`KEPT_GROUPS`), trouble codes, acknowledgments | unchanged | |
| Every other group: proprietary, component and software identification (free text), anything not reviewed | data 0xFF | |
| Transport data frames | by message | kept for a complete message of a kept group; 0xFF behind the sequence number for an incomplete message, a masked group, or a packet with no announcement |
| Rows out of frame | rebuilt | recovered frames follow the rules above; every other byte of the run is 0xFF, except the timestamp and identifier that follow a recovered frame |
| Time, distance, engine hours, fuel, firmware versions | unchanged | they are what the project studies |

In all, 63.8% of the frames carry their original data bytes, 36.0% have them filled with 0xFF and 0.2% were
rewritten.

The tool reads its own output back and refuses to write a capture that does not split into the same runs and give
the same frames as the original. Before publishing, each file was also compared with its original: the report
`j1939cap inspect` gives is identical apart from the VIN text, and no serial number, VIN, component or software
identification string of the original survives as bytes anywhere in the data, including across packet boundaries.

Publishing needs the raw captures and the key; neither is in the repository:

```
J1939CAP_DEID_KEY_FILE=/path/to/key j1939cap deidentify raw/*.csv --out data/input/captures
```

## Joining other projects

The pseudonyms use the same key and namespaces as the tables of
[telematics-data-foundation](https://github.com/JacobCCMorgan/telematics-data-foundation), a snapshot of the same
provider's platform from September 2026.

- All 12 device keys are in its `dim_device`.
- Six trucks broadcast a VIN. For four of them the vehicle key equals the `vin_key` on record for that device; for
  the other two the platform had no VIN on record. No device has a different one.
- Of the six devices whose captures hold no VIN, four have a `vin_key` on record.
- `dim_device.is_internal_test` is true for nine of the twelve devices. The flag marks the group that receives new
  firmware first, and a device is put in that group after it has shown anomalies in the field. These are working
  trucks in service, not bench units, so the captures are kept and not filtered. They are also not a random sample:
  they lean toward trucks whose devices have been hard to get right.
