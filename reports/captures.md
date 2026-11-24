# Captures side by side

14 captures from 12 devices, 2,065,558 frames. 1 holds no frames at all.

Written by `j1939cap summarize`; `captures.csv` has every column. Source addresses are hexadecimal.

## What was recorded

The recorder is asked for a number of seconds and reports how long it ran. Frames end at is the timestamp of the
last frame in the file.

| capture | vehicle | asked for (s) | ran (s) | frames end at (s) | frames | sources | proprietary | top speed (km/h) |
|---|---|---|---|---|---|---|---|---|
| DEV-811a412817_20261019T170225Z | no VIN on the bus | 180 | 181.0 | 179.9 | 94,273 | 4 | 0.2% | 0.0 |
| DEV-af3adba16c_20261019T180323Z | Peterbilt 2021 | 60 | 61.1 | 60.1 | 83,408 | 15 | 18.9% | 0.0 |
| DEV-af3adba16c_20261019T181835Z | Peterbilt 2021 | 120 | 121.1 | 119.9 | 151,624 | 16 | 18.9% | 0.0 |
| DEV-af3adba16c_20261019T182127Z | Peterbilt 2021 | 60 | 61.1 | 33.2 | 46,042 | 15 | 18.9% | 0.0 |
| DEV-ab9206a74a_20261110T033059Z | - | 200 | 201.1 | - | 0 | 0 | - | - |
| DEV-f5bdc8e9a4_20261110T225347Z | Volvo 2021 | 180 | 181.0 | 179.9 | 162,345 | 13 | 44.5% | 117.7 |
| DEV-d809a4dd22_20261110T233159Z | Volvo 2022 | 240 | 241.0 | 239.8 | 225,893 | 13 | 43.7% | 115.8 |
| DEV-8d8a03ff7c_20261112T225142Z | no VIN on the bus | 240 | 241.0 | 239.8 | 9,442 | 12 | 54.9% | - |
| DEV-e755bd6f48_20261112T225304Z | no VIN on the bus | 240 | 241.0 | 239.8 | 197,756 | 27 | 8.4% | 93.3 |
| DEV-c4acf10b41_20261113T020108Z | Volvo 2021 | 240 | 241.0 | 239.8 | 221,043 | 14 | 44.9% | 0.0 |
| DEV-6e418af0fe_20261114T010004Z | no VIN on the bus | 240 | 241.0 | 239.8 | 263,130 | 29 | 22.5% | 114.7 |
| DEV-3d9ec3f623_20261114T011727Z | no VIN on the bus | 240 | 241.1 | 239.9 | 196,678 | 26 | 8.4% | 96.8 |
| DEV-b3302c1cd0_20261114T011821Z | Volvo 2019 | 240 | 241.0 | 239.8 | 229,579 | 15 | 42.0% | 115.2 |
| DEV-e08a7a160f_20261114T032203Z | Volvo 2016 | 240 | 241.0 | 239.8 | 184,345 | 9 | 29.8% | 122.1 |

## Is the recording intact

The first two columns are the recorder's own account. Rows out of frame were cut at the wrong byte offset;
frames recovered were read back out of them. Frames missing is an estimate: the median, over every steadily
repeating identifier, of the share of its frames that are absent, with the quartiles in brackets. Long messages
complete counts the multi-packet messages that arrived whole out of those announced.

| capture | says dropped | says complete | rows out of frame | frames recovered | frames missing | long messages complete |
|---|---|---|---|---|---|---|
| DEV-811a412817_20261019T170225Z | 0 | yes | 0 | 0 | 7.3% (4.8 to 9.7) | 197 of 231 (85.3%) |
| DEV-af3adba16c_20261019T180323Z | 0 | yes | 0 | 0 | 0.4% (0.0 to 0.7) | 217 of 223 (97.3%) |
| DEV-af3adba16c_20261019T181835Z | 0 | no | 41 | 35 | 9.3% (8.2 to 10.1) | 266 of 408 (65.2%) |
| DEV-af3adba16c_20261019T182127Z | 0 | yes | 0 | 0 | 0.7% (0.6 to 0.9) | 119 of 127 (93.7%) |
| DEV-ab9206a74a_20261110T033059Z | 0 | yes | 0 | 0 | - | - |
| DEV-f5bdc8e9a4_20261110T225347Z | 0 | yes | 0 | 0 | 0.4% (0.0 to 0.6) | 328 of 334 (98.2%) |
| DEV-d809a4dd22_20261110T233159Z | 0 | yes | 0 | 0 | 0.8% (0.5 to 0.8) | 688 of 706 (97.5%) |
| DEV-8d8a03ff7c_20261112T225142Z | 0 | yes | 0 | 0 | - | - |
| DEV-e755bd6f48_20261112T225304Z | 0 | yes | 0 | 0 | 0.5% (0.4 to 0.8) | 368 of 373 (98.7%) |
| DEV-c4acf10b41_20261113T020108Z | 0 | yes | 23 | 21 | 12.2% (10.8 to 13.6) | 383 of 586 (65.4%) |
| DEV-6e418af0fe_20261114T010004Z | 0 | yes | 0 | 0 | 0.7% (0.5 to 0.8) | 328 of 335 (97.9%) |
| DEV-3d9ec3f623_20261114T011727Z | 0 | yes | 0 | 0 | 0.7% (0.4 to 0.8) | 104 of 110 (94.5%) |
| DEV-b3302c1cd0_20261114T011821Z | 0 | yes | 0 | 0 | 0.7% (0.6 to 0.8) | 1098 of 1129 (97.2%) |
| DEV-e08a7a160f_20261114T032203Z | 0 | yes | 0 | 0 | 0.4% (0.4 to 0.6) | 439 of 444 (98.9%) |

## What an electronic logging device needs

Which source addresses send each item. Distance spread is the gap between the last readings of the sources.
Km per engine hour is each controller's distance divided by the engine hours, a check on the distance: a
counter that was reset gives a figure no truck averages.

| capture | vehicle speed | engine speed | distance | distance spread (km) | engine hours | km per engine hour | VIN broadcast | VIN requests |
|---|---|---|---|---|---|---|---|---|
| DEV-811a412817_20261019T170225Z | 00 | 00 | 00 31 | 746221.675 | 00 | 00 18.4; 31 44.9 | no | 2 |
| DEV-af3adba16c_20261019T180323Z | 00 | 00 | 00 31 | 0.05 | 00 | 00 42.5; 31 42.5 | yes | 7 |
| DEV-af3adba16c_20261019T181835Z | 00 | 00 | 00 31 | 0.05 | 00 | 00 42.5; 31 42.5 | yes | 15 |
| DEV-af3adba16c_20261019T182127Z | 00 | 00 | 00 31 | 0.05 | 00 | 00 42.5; 31 42.5 | yes | 4 |
| DEV-f5bdc8e9a4_20261110T225347Z | 11 | 00 | 17 | 0.0 | 00 | 17 60.3 | yes | 0 |
| DEV-d809a4dd22_20261110T233159Z | 11 | 00 | 17 | 0.0 | 00 | 17 63.8 | yes | 0 |
| DEV-8d8a03ff7c_20261112T225142Z | - | 00 | - | - | - | - | no | 0 |
| DEV-e755bd6f48_20261112T225304Z | 00 | 00 | 00 | 0.08 | 00 | 00 51.8 | no | 0 |
| DEV-c4acf10b41_20261113T020108Z | 11 | 00 | 17 | 0.0 | 00 | 17 728.0 | yes | 0 |
| DEV-6e418af0fe_20261114T010004Z | 00 | 00 | 00 17 | 249.02 | 00 | 00 52.5; 17 52.5 | no | 0 |
| DEV-3d9ec3f623_20261114T011727Z | 00 | 00 | 00 | 0.05 | 00 | 00 53.7 | no | 0 |
| DEV-b3302c1cd0_20261114T011821Z | 11 | 00 | 17 | 0.0 | 00 | 17 62.4 | yes | 0 |
| DEV-e08a7a160f_20261114T032203Z | 11 | 00 | 17 | 0.0 | 00 | 17 325.8 | yes | 0 |

## Trouble codes and refusals

| capture | sources with active codes | active codes | negative acknowledgments |
|---|---|---|---|
| DEV-811a412817_20261019T170225Z | 1 | 2 | 17 |
| DEV-af3adba16c_20261019T180323Z | 2 | 7 | 0 |
| DEV-af3adba16c_20261019T181835Z | 2 | 7 | 0 |
| DEV-af3adba16c_20261019T182127Z | 2 | 7 | 0 |
| DEV-f5bdc8e9a4_20261110T225347Z | 1 | 1 | 0 |
| DEV-d809a4dd22_20261110T233159Z | 1 | 1 | 0 |
| DEV-8d8a03ff7c_20261112T225142Z | 0 | 0 | 0 |
| DEV-e755bd6f48_20261112T225304Z | 13 | 15 | 0 |
| DEV-c4acf10b41_20261113T020108Z | 1 | 2 | 0 |
| DEV-6e418af0fe_20261114T010004Z | 11 | 12 | 96 |
| DEV-3d9ec3f623_20261114T011727Z | 10 | 10 | 0 |
| DEV-b3302c1cd0_20261114T011821Z | 5 | 10 | 0 |
| DEV-e08a7a160f_20261114T032203Z | 1 | 1 | 0 |

## What the published files contain

Of 2,065,558 frames, 1,317,742 (63.8%) carry their original data bytes, 744,008 (36.0%)
keep timestamp and identifier with the data filled with 0xFF, and 3,808 (0.2%) were rewritten:
VIN, position, request or address claim. The 64 rows of the out-of-frame runs hold 56 of those frames; what is
left of those rows is 0xFF, apart from the timestamp and identifier that follow a recovered frame.
