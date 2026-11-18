import pytest

from j1939cap.core.vin import check_digit, clean, is_valid, manufacturer, model_year

SAMPLE = "1M8GDM9AXKP042788"  # the worked example used in most descriptions of the check digit


def with_check_digit(vin):
    return vin[:8] + check_digit(vin) + vin[9:]


def test_check_digit_of_the_reference_example():
    assert check_digit(SAMPLE) == "X" and is_valid(SAMPLE)
    assert not is_valid(SAMPLE[:-1] + "9")


def test_all_ones_checks_to_one():
    assert check_digit("11111111111111111") == "1"


def test_payload_is_cut_at_the_asterisk_and_stripped():
    assert clean(SAMPLE.encode() + b"*\xff\xff\xff") == SAMPLE
    assert clean(b"\x00" + SAMPLE.lower().encode() + b"\r\n") == SAMPLE
    assert clean(b"\xff\xff\xff\xff") == ""


def test_invalid_shapes():
    assert not is_valid("SHORT") and not is_valid("1M8GDM9AXKP04278O")  # letter O is not a VIN character
    with pytest.raises(ValueError):
        check_digit("1M8GDM9AXKP04278")


@pytest.mark.parametrize(
    "prefix,make",
    [("1FU", "Freightliner"), ("4V4", "Volvo"), ("1XK", "Kenworth"), ("2NP", "Peterbilt"), ("3HS", "International")],
)
def test_manufacturer(prefix, make):
    vin = with_check_digit(prefix + "AB1CD0LE000001")
    assert is_valid(vin) and manufacturer(vin) == make
    assert manufacturer("ZZZ" + vin[3:]) is None


@pytest.mark.parametrize(
    "code,not_after,year",
    [("K", 2027, 2019), ("K", 2000, 1989), ("N", 2027, 2022), ("T", 2027, 2026), ("V", 2026, 1997), ("1", 2027, 2001)],
)
def test_model_year_picks_the_latest_cycle_not_in_the_future(code, not_after, year):
    vin = "4V4AB1CD0" + code + "N000001"
    assert model_year(vin, not_after=not_after) == year


def test_model_year_unknown():
    assert model_year("4V4AB1CD0UN000001") is None  # U is not a year code
    assert model_year("4V4AB1CD0AN000001", not_after=1970) is None
    assert model_year("TOO SHORT") is None
