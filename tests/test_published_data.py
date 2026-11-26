"""The captures in data/input are what the manifest says they are."""

import hashlib
from pathlib import Path

import pytest

from j1939cap.core.capture import find_summary, read_recorder_csv, read_summary
from j1939cap.core.integrity import check
from j1939cap.deid import rules
from j1939cap.deid.capture import MANIFEST_COUNTS, deidentify_rows, read_manifest
from j1939cap.deid.pseudonym import Pseudonymizer

CAPTURES = Path(__file__).resolve().parent.parent / "data" / "input" / "captures"
MANIFEST = read_manifest(CAPTURES) if CAPTURES.is_dir() else []


def test_every_file_is_listed_and_every_listed_file_is_there():
    listed = {entry["capture"] for entry in MANIFEST}
    present = {p.name[: -len(".csv.gz")] for p in CAPTURES.glob("*.csv.gz")}
    assert listed == present and len(listed) == 79
    assert all((CAPTURES / f"{name}_summary.txt").is_file() for name in listed)


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda entry: entry["capture"])
def test_capture_matches_its_manifest_line(entry):
    path = CAPTURES / f"{entry['capture']}.csv.gz"
    blob = path.read_bytes()
    assert (len(blob), hashlib.sha256(blob).hexdigest()) == (int(entry["bytes"]), entry["sha256"])

    rows, malformed = read_recorder_csv(path)
    summary = read_summary(find_summary(path))
    assert malformed == 0 and len(rows) == int(entry["rows"]) == summary.records_written
    assert summary.fields["sn"] == entry["device_key"] == entry["capture"].split("_")[0]
    assert check(rows, summary.duration_ms).rows_out_of_frame == int(entry["rows_out_of_frame"])

    # De-identifying a published capture again must leave every row as it is: nothing in it still needs a rule.
    # The key does not matter for that, except that a VIN would be rewritten once more. Only those frames may
    # change, and not each of them does: a packet that carries one pseudonym digit keeps it one time in sixteen,
    # and a packet of padding in front of a fragment has nothing to rewrite.
    again = deidentify_rows(rows, summary.duration_ms, Pseudonymizer(b"any key of sixteen bytes or more"))
    for name in MANIFEST_COUNTS:
        assert again.counts.get(name, 0) == int(entry[name]), name
    assert again.rows_changed <= int(entry[rules.VIN])
    assert (again.rows_changed > 0) == (int(entry[rules.VIN]) > 0)
