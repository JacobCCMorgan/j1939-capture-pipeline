import pytest

from j1939cap.core import vin as vin_rules
from j1939cap.core.ids import is_proprietary
from j1939cap.deid import rules


def test_actions_by_parameter_group():
    assert rules.action(0xF004) == rules.KEEP  # engine speed
    assert rules.action(0xFECA) == rules.KEEP  # active trouble codes
    assert rules.action(0xFF21) == rules.MASK  # proprietary
    assert rules.action(0xEF00) == rules.MASK  # proprietary, addressed
    assert rules.action(0xFEEB) == rules.MASK  # component identification is free text
    assert rules.action(0xFEDA) == rules.MASK  # software identification is free text
    assert rules.action(0xFD11) == rules.MASK  # standard, but nobody has reviewed it
    assert rules.action(0xFEEC) == rules.VIN
    assert rules.action(0xFEF3) == rules.POSITION
    assert rules.action(0xEE00) == rules.NAME
    assert rules.action(0xEA00) == rules.REQUEST


def test_the_kept_list_holds_no_proprietary_group_and_no_group_with_its_own_rule():
    assert not any(is_proprietary(pgn) for pgn in rules.KEPT_GROUPS)
    assert not rules.KEPT_GROUPS & {0xFEEC, 0xFEF3, 0xEE00, 0xEA00, 0xEC00, 0xEB00, 0xFEEB, 0xFEDA}


def test_mask_and_request():
    assert rules.mask(bytes(range(8))) == b"\xff" * 8
    assert rules.trim_request(bytes.fromhex("ECFE00D0007D0041")) == bytes.fromhex("ECFE00FFFFFFFFFF")
    assert rules.trim_request(bytes.fromhex("ECFE00")) == bytes.fromhex("ECFE00")


def position(latitude, longitude):
    raw = [round((value + 210) * 10_000_000) for value in (latitude, longitude)]
    return b"".join(v.to_bytes(4, "little") for v in raw)


def test_position_moves_to_a_grid_of_a_twentieth_of_a_degree():
    assert rules.coarsen_position(position(34.0522, -118.2437)) == position(34.05, -118.25)
    assert rules.coarsen_position(position(34.0749, -118.2251)) == position(34.05, -118.25)
    assert rules.coarsen_position(position(34.0751, -118.2249)) == position(34.10, -118.20)
    assert rules.coarsen_position(position(-33.8688, 151.2093)) == position(-33.85, 151.20)


def test_a_position_that_is_not_available_stays_as_it_is():
    not_available = b"\xff" * 8
    assert rules.coarsen_position(not_available) == not_available
    half = position(34.0522, 0)[:4] + b"\xff\xff\xff\xfe"
    assert rules.coarsen_position(half) == position(34.05, 0)[:4] + b"\xff\xff\xff\xfe"
    assert rules.coarsen_position(b"\x01\x02\x03") == b"\xff" * 3


def test_identity_number_is_cleared_and_the_rest_of_the_name_stays():
    identity, manufacturer, upper = 0x1ABCDE, 0x2F5, 0x80_81_00_05
    name = identity | (manufacturer << 21) | (upper << 32)
    cleared = int.from_bytes(rules.clear_identity(name.to_bytes(8, "little")), "little")
    assert cleared & 0x1FFFFF == 0
    assert (cleared >> 21) & 0x7FF == manufacturer and cleared >> 32 == upper


KEY = "VIN-0a1b2c3d4e"


def test_rewritten_vin_keeps_maker_and_year_and_carries_the_pseudonym():
    original = "1M8GDM9AXKP042788"
    new = rules.rewrite_vin(original, KEY)
    assert new[:3] == "1M8" and new[9] == "K"
    assert new[3:8] + new[10:15] == "0A1B2C3D4E" and new[15:] == "ZZ"
    assert vin_rules.is_valid(new) and new != original
    assert vin_rules.model_year(new, not_after=2026) == vin_rules.model_year(original, not_after=2026) == 2019
    assert rules.rewrite_vin(original, KEY) == new


def test_a_vin_with_a_wrong_check_digit_stays_wrong():
    wrong = "1M8GDM9A0KP042788"
    assert not vin_rules.is_valid(wrong)
    new = rules.rewrite_vin(wrong, KEY)
    assert not vin_rules.is_valid(new) and new[:3] == "1M8" and new[9] == "K"


def test_rewrite_needs_a_whole_vin_and_a_whole_pseudonym():
    with pytest.raises(ValueError):
        rules.rewrite_vin("1M8GDM9AXKP04278", KEY)
    with pytest.raises(ValueError):
        rules.rewrite_vin("1M8GDM9AXKP042788", "VIN-0a1b")
