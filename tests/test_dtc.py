import pytest

from j1939cap.core.dtc import Dtc, decode_dm, decode_dtc


def test_single_code_with_amber_lamp():
    # lamp byte 0x04: amber warning on. SPN 1569 (0x621), FMI 31, seen 2 times.
    message = decode_dm(bytes.fromhex("04FF21061F02FFFF"))
    assert message.lamps == {"protect": "off", "amber_warning": "on", "red_stop": "off", "malfunction_indicator": "off"}
    assert message.dtcs == (Dtc(1569, 31, 2, 0),)
    assert message.any_lamp_on


def test_no_active_codes():
    message = decode_dm(bytes.fromhex("00FF00000000FFFF"))
    assert message.dtcs == () and not message.any_lamp_on


def test_parameter_number_uses_the_top_three_bits_of_the_third_byte():
    # SPN 520372 = 0x7F0B4: low bytes B4 F0, high three bits 0b111 with FMI 16 in the low five
    assert decode_dtc(bytes([0xB4, 0xF0, 0xF0, 0x81])) == Dtc(520372, 16, 1, 1)


def test_several_codes_from_a_transport_message():
    payload = bytes.fromhex("50FF" + "6E000003" + "BE000201" + "A7060B7E")
    message = decode_dm(payload)
    assert message.lamps["malfunction_indicator"] == "on" and message.lamps["red_stop"] == "on"
    assert message.dtcs == (Dtc(110, 0, 3, 0), Dtc(190, 2, 1, 0), Dtc(1703, 11, 126, 0))


def test_lamp_not_available_and_ragged_tail():
    message = decode_dm(bytes.fromhex("FFFF6E000003FF"))  # one code, then a single stray byte
    assert set(message.lamps.values()) == {"not_available"}
    assert message.dtcs == (Dtc(110, 0, 3, 0),)


def test_too_short():
    with pytest.raises(ValueError):
        decode_dm(b"\x00")
    with pytest.raises(ValueError):
        decode_dtc(b"\x00\x00\x00")
