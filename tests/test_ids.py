import pytest

from j1939cap.core.ids import GLOBAL_ADDRESS, build_id, is_proprietary, parse_id


def test_broadcast_group_keeps_pdu_specific_in_the_pgn():
    jid = parse_id(0x18FEF100)  # cruise control / vehicle speed from the engine
    assert (jid.priority, jid.pgn, jid.sa, jid.da, jid.pdu1) == (6, 0xFEF1, 0x00, GLOBAL_ADDRESS, False)


def test_addressed_group_moves_pdu_specific_to_the_destination():
    jid = parse_id(0x18EAFFF9)  # request sent to everyone by a service tool
    assert (jid.pgn, jid.sa, jid.da, jid.pdu1) == (0xEA00, 0xF9, 0xFF, True)
    jid = parse_id(0x1CECFF3D)  # transport announcement
    assert (jid.priority, jid.pgn, jid.sa, jid.da) == (7, 0xEC00, 0x3D, 0xFF)


def test_engine_speed_identifier():
    jid = parse_id(0x0CF00400)
    assert (jid.priority, jid.pgn, jid.sa, jid.pdu1) == (3, 0xF004, 0x00, False)


def test_data_page_is_part_of_the_pgn():
    assert parse_id(0x19FEF100).pgn == 0x1FEF1


@pytest.mark.parametrize("can_id", [0x18FEF100, 0x18EAFFF9, 0x0CF00400, 0x1CECFF3D, 0x19FEF117, 0x18EF0B21])
def test_build_is_the_inverse_of_parse(can_id):
    jid = parse_id(can_id)
    assert build_id(jid.priority, jid.pgn, jid.sa, jid.da) == can_id


@pytest.mark.parametrize("can_id", [-1, 0x20000000, 0x9511FF04])
def test_identifiers_wider_than_29_bits_are_rejected(can_id):
    with pytest.raises(ValueError):
        parse_id(can_id)


def test_pdu1_group_cannot_be_built_with_a_low_byte():
    with pytest.raises(ValueError):
        build_id(6, 0xEA01, 0x00)


def test_proprietary_groups():
    assert is_proprietary(0xEF00) and is_proprietary(0xFF00) and is_proprietary(0xFFFF) and is_proprietary(0x1FF10)
    assert not is_proprietary(0xFEF1) and not is_proprietary(0xF004)
