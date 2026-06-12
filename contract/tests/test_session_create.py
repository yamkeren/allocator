"""SessionCreate validation: the API-level guard for session requests."""

import pytest
from pydantic import ValidationError

from allocator_contract.session import SessionCreate


def test_accepts_unique_names():
    sc = SessionCreate(devices=["wifi_0", "hid_1"])
    assert sc.devices == ["wifi_0", "hid_1"]
    assert sc.node is None


def test_rejects_duplicates_and_names_them():
    with pytest.raises(ValidationError) as exc:
        SessionCreate(devices=["a", "b", "a", "c", "c"])
    msg = str(exc.value)
    assert "duplicate device names" in msg
    assert "a" in msg and "c" in msg


def test_rejects_empty_list():
    with pytest.raises(ValidationError):
        SessionCreate(devices=[])


@pytest.mark.parametrize("bad", ["WiFi_0", "wifi-0", "wifi 0", "wifi/0", ""])
def test_rejects_invalid_name_pattern(bad):
    with pytest.raises(ValidationError):
        SessionCreate(devices=[bad])


def test_rejects_overlong_name():
    with pytest.raises(ValidationError):
        SessionCreate(devices=["x" * 101])


def test_node_pin_optional():
    sc = SessionCreate(devices=["wifi_0"], node="lab-1")
    assert sc.node == "lab-1"
