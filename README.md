# j1939-capture-pipeline

Decode and check J1939 CAN bus captures from heavy trucks, and run them through a data pipeline.

A capture is a few minutes of raw frames recorded from a truck's 9-pin diagnostic connector by a telematics device.
The command line tool, `j1939cap`, answers the questions a compatibility engineer asks of one: is the recording itself
intact, which controllers are on the bus, which of them sends speed, engine speed, distance and engine hours, do
multi-packet messages arrive whole, and does the truck broadcast its VIN.

Status: work in progress. The decoding core comes first; the batch pipeline, the web tool and the published
de-identified captures follow.

## Layout

- `src/j1939cap/core/` - identifiers, capture readers, recorder integrity, transport protocol, signals. No third-party dependencies.
- `tests/` - unit tests built on hand-made frames.

## Development

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q
```
