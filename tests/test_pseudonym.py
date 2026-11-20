import pytest

from j1939cap.deid.pseudonym import KEY_FILE_VARIABLE, Pseudonymizer

KEY_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"


def test_known_values():
    p = Pseudonymizer(bytes.fromhex(KEY_HEX))
    assert p.device("AB12345678") == "DEV-cafba9262c"
    assert p.vehicle("1M8GDM9AXKP042788") == "VIN-031a910225"


def test_case_and_surrounding_space_do_not_change_the_pseudonym():
    p = Pseudonymizer(bytes.fromhex(KEY_HEX))
    assert p.device(" ab12345678\n") == p.device("AB12345678")


def test_the_same_value_is_a_different_pseudonym_in_another_namespace_or_under_another_key():
    p = Pseudonymizer(bytes.fromhex(KEY_HEX))
    assert p.device("AB12345678")[4:] != p.vehicle("AB12345678")[4:]
    assert Pseudonymizer(b"a plain text key of enough length").device("AB12345678") == "DEV-755ab8c8ea"


def test_key_file_holds_hex_or_text(tmp_path):
    hex_file, text_file = tmp_path / "hex.key", tmp_path / "text.key"
    hex_file.write_text(KEY_HEX + "\n")
    text_file.write_text("a plain text key of enough length\n")
    assert Pseudonymizer.from_file(hex_file).device("AB12345678") == "DEV-cafba9262c"
    assert Pseudonymizer.from_file(text_file).device("AB12345678") == "DEV-755ab8c8ea"


def test_key_comes_from_the_environment(tmp_path, monkeypatch):
    key_file = tmp_path / "k"
    key_file.write_text(KEY_HEX)
    monkeypatch.setenv(KEY_FILE_VARIABLE, str(key_file))
    assert Pseudonymizer.from_env().device("AB12345678") == "DEV-cafba9262c"
    monkeypatch.delenv(KEY_FILE_VARIABLE)
    with pytest.raises(RuntimeError, match=KEY_FILE_VARIABLE):
        Pseudonymizer.from_env()


def test_a_short_key_is_refused():
    with pytest.raises(ValueError):
        Pseudonymizer(b"too short")
