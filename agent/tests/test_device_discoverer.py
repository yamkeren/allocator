"""Classification, fingerprint, and bindable-busid parsing — all on fake sysfs
trees in tmp_path; no real USB, no real subprocess.
"""

import subprocess
from pathlib import Path

import pytest

from allocator_agent.components import device_discoverer as dd


def make_device(tmp_path: Path, *, device_class: str = "00",
                interfaces: list[tuple[str, str | None, str | None]] = ()) -> Path:
    """Fake sysfs device dir: bDeviceClass + one subdir per interface."""
    dev = tmp_path / "1-1"
    dev.mkdir()
    (dev / "bDeviceClass").write_text(device_class + "\n")
    for i, (iclass, isub, iproto) in enumerate(interfaces):
        iface = dev / f"1-1:1.{i}"
        iface.mkdir()
        (iface / "bInterfaceClass").write_text(iclass + "\n")
        if isub is not None:
            (iface / "bInterfaceSubClass").write_text(isub + "\n")
        if iproto is not None:
            (iface / "bInterfaceProtocol").write_text(iproto + "\n")
    return dev


# _classify

def test_classify_plain_hid(tmp_path):
    dev = make_device(tmp_path, interfaces=[("03", "01", "01")])
    assert dd._classify(dev) == "HID"


def test_classify_composite_prefers_higher_priority(tmp_path):
    # Webcam with a HID control interface: VIDEO (90) must beat HID (50).
    dev = make_device(tmp_path, interfaces=[("03", None, None), ("0e", None, None)])
    assert dd._classify(dev) == "VIDEO"


def test_classify_bluetooth_requires_subclass_and_protocol(tmp_path):
    dev = make_device(tmp_path, interfaces=[("e0", "01", "01")])
    assert dd._classify(dev) == "BLUETOOTH"


def test_classify_e0_without_bt_subclass_is_not_bluetooth(tmp_path):
    dev = make_device(tmp_path, interfaces=[("e0", "02", "01")])
    assert dd._classify(dev) == "GENERIC"


def test_classify_no_known_classes_is_generic(tmp_path):
    dev = make_device(tmp_path)
    assert dd._classify(dev) == "GENERIC"


def test_classify_device_level_class_used_when_no_interfaces(tmp_path):
    dev = make_device(tmp_path, device_class="08")
    assert dd._classify(dev) == "MASS_STORAGE"


# _map_class

@pytest.mark.parametrize("triple,expected", [
    (("03", None, None), "HID"),
    (("e0", "01", "01"), "BLUETOOTH"),
    (("e0", "01", "02"), None),
    (("ff", None, None), "SERIAL"),
    (("zz", None, None), None),
])
def test_map_class(triple, expected):
    assert dd._map_class(*triple) == expected


# _fingerprint

def test_fingerprint_stable_and_distinct():
    a = dd._fingerprint("0bda", "8812", "S1", None)
    assert a == dd._fingerprint("0bda", "8812", "S1", None)
    assert len(a) == 64
    assert a != dd._fingerprint("0bda", "8812", "S2", None)
    assert a != dd._fingerprint("0bda", "8812", "S1", "aa:bb:cc:dd:ee:ff")


# usbip_bindable_busids

def _cp(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=["usbip"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def test_bindable_busids_parses_both_formats(monkeypatch):
    out = " - busid 1-1 (0bda:8812)\nbusid=2-1.4#usbid=1234:5678#\n"
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _cp(stdout=out))
    assert dd.usbip_bindable_busids() == {"1-1", "2-1.4"}


def test_bindable_busids_nonzero_returns_none(monkeypatch):
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _cp(rc=1, stderr="boom"))
    assert dd.usbip_bindable_busids() is None


def test_bindable_busids_oserror_returns_none(monkeypatch):
    def boom(*a, **k):
        raise OSError("usbip missing")
    monkeypatch.setattr(dd.subprocess, "run", boom)
    assert dd.usbip_bindable_busids() is None
