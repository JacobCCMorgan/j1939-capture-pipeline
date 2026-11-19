import pytest

from j1939cap.core.signals import BY_NAME, CATALOG, ERROR, NOT_AVAILABLE, RESERVED, VALID, decode, raw_state


def readings(pgn, hex_payload):
    return {r.name: r for r in decode(pgn, bytes.fromhex(hex_payload))}


def test_engine_speed():
    r = readings(0xF004, "F07D7D401F00F0FF")["engine_speed"]  # 0x1F40 = 8000 counts of 0.125 rpm
    assert (r.value, r.state) == (1000.0, VALID)


def test_vehicle_speed_in_1_256_km_per_hour():
    r = readings(0xFEF1, "FF8059FFFFFFFFFF")["wheel_based_vehicle_speed"]  # 0x5980 = 22912 / 256
    assert r.value == pytest.approx(89.5)


def test_high_resolution_distance_and_both_fields_of_the_group():
    got = readings(0xFEC1, "40420F00E8030000")  # 1,000,000 * 5 m and 1,000 * 5 m
    assert got["total_vehicle_distance_hr"].value == pytest.approx(5000.0)
    assert got["trip_distance_hr"].value == pytest.approx(5.0)


def test_standard_distance_sits_in_the_second_half():
    got = readings(0xFEE0, "FFFFFFFF00127A00")  # trip not available, total 8,000,000 * 0.125 km
    assert got["trip_distance"].state == NOT_AVAILABLE and got["trip_distance"].value is None
    assert got["total_vehicle_distance"].value == pytest.approx(1_000_000.0)


def test_engine_hours_and_fuel():
    assert readings(0xFEE5, "A0860100FFFFFFFF")["engine_total_hours"].value == pytest.approx(5000.0)
    assert readings(0xFEE9, "FFFFFFFF40420F00")["engine_total_fuel_used"].value == pytest.approx(500_000.0)
    assert readings(0xFD09, "FFFFFFFF00CA9A3B")["engine_total_fuel_used_hr"].value == pytest.approx(1_000_000.0)
    assert readings(0xFEF2, "E803FFFFFFFFFFFF")["engine_fuel_rate"].value == pytest.approx(50.0)


def test_scaled_values_carry_no_binary_noise():
    assert 3 * 0.05 != 0.15  # what plain multiplication gives
    assert readings(0xFEE5, "03000000FFFFFFFF")["engine_total_hours"].value == 0.15
    assert readings(0xFEF1, "FF0100FFFFFFFFFF")["wheel_based_vehicle_speed"].value == 0.00390625  # the smallest step stays exact


@pytest.mark.parametrize(
    "raw,length,state",
    [
        (0xFAFF, 2, VALID),
        (0xFB00, 2, RESERVED),
        (0xFE00, 2, ERROR),
        (0xFEFF, 2, ERROR),
        (0xFF00, 2, NOT_AVAILABLE),
        (0xFFFF, 2, NOT_AVAILABLE),
        (0xFAFFFFFF, 4, VALID),
        (0xFE000001, 4, ERROR),
        (0xFFFFFFFF, 4, NOT_AVAILABLE),
        (0xFA, 1, VALID),
        (0xFF, 1, NOT_AVAILABLE),
    ],
)
def test_reserved_ranges(raw, length, state):
    assert raw_state(raw, length) == state


def test_error_and_not_available_are_never_scaled():
    got = readings(0xF004, "F07D7D00FE00F0FF")["engine_speed"]
    assert (got.value, got.state) == (None, ERROR)
    got = readings(0xFEF1, "FFFFFFFFFFFFFFFF")["wheel_based_vehicle_speed"]
    assert (got.value, got.state) == (None, NOT_AVAILABLE)


def test_unknown_group_and_short_payload():
    assert decode(0xFFFF, bytes(8)) == []
    assert decode(0xF004, bytes(4)) == []  # the field needs bytes 4 and 5
    assert [r.name for r in decode(0xFEC1, bytes(6))] == ["total_vehicle_distance_hr"]


def test_catalog_is_consistent():
    assert len(BY_NAME) == len(CATALOG)
    for s in CATALOG:
        assert s.start + s.length <= 8 and s.length in (1, 2, 4) and s.scale > 0
