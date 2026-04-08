"""Tests for ais_stream._parse_vessel."""

import time

from ais_stream import _parse_vessel


# ---------------------------------------------------------------------------
# Helper: build a realistic Datalastic vessel dict
# ---------------------------------------------------------------------------

def _make_vessel(**overrides) -> dict:
    """Return a complete Datalastic-style vessel dict with sensible defaults.

    Any key in *overrides* replaces the default value. Pass a key with value
    ``_MISSING`` (the sentinel string) to remove it from the dict entirely.
    """
    base = {
        "mmsi": 636092398,
        "lat": 5.6219,
        "lon": -0.0185,
        "speed": 11.4,
        "course": 182.7,
        "heading": 180,
        "name": "MARIA S",
        "type": "Cargo",
        "type_specific": "General Cargo",
        "country_iso": "LR",
        "destination": "TEMA",
        "eta_epoch": 1711605600,
        "imo": 9356753,
    }
    for k, v in overrides.items():
        if v == "_MISSING":
            base.pop(k, None)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_vessel_valid():
    """A complete vessel dict returns a well-formed dict with all keys."""
    raw = _make_vessel()
    result = _parse_vessel(raw)

    assert result is not None
    assert result["mmsi"] == 636092398
    assert result["lat"] == 5.6219
    assert result["lon"] == -0.0185
    assert result["speed"] == 11.4
    assert result["course"] == 182.7
    assert result["heading"] == 180
    assert result["name"] == "MARIA S"
    assert result["ship_type"] == "Cargo"
    assert result["type_specific"] == "General Cargo"
    assert result["country_iso"] == "LR"
    assert result["destination"] == "TEMA"
    assert result["eta_epoch"] == 1711605600
    assert result["imo"] == 9356753
    assert result["nav_status"] is None
    assert isinstance(result["timestamp"], float)
    # timestamp should be recent (within last 5 seconds)
    assert abs(result["timestamp"] - time.time()) < 5


def test_parse_vessel_mmsi_zero():
    """mmsi=0 is invalid -> returns None."""
    raw = _make_vessel(mmsi=0)
    result = _parse_vessel(raw)

    assert result is None


def test_parse_vessel_missing_lat():
    """lat=None -> returns None."""
    raw = _make_vessel(lat=None)
    result = _parse_vessel(raw)

    assert result is None


def test_parse_vessel_missing_lon():
    """lon=None -> returns None."""
    raw = _make_vessel(lon=None)
    result = _parse_vessel(raw)

    assert result is None


def test_parse_vessel_none_speed():
    """speed=None -> returns dict with speed coerced to 0.0."""
    raw = _make_vessel(speed=None)
    result = _parse_vessel(raw)

    assert result is not None
    assert result["speed"] == 0.0


def test_parse_vessel_name_strip():
    """Leading/trailing whitespace on name is stripped."""
    raw = _make_vessel(name="  VESSEL A  ")
    result = _parse_vessel(raw)

    assert result is not None
    assert result["name"] == "VESSEL A"


def test_parse_vessel_empty_name():
    """Whitespace-only name normalises to None."""
    raw = _make_vessel(name="   ")
    result = _parse_vessel(raw)

    assert result is not None
    assert result["name"] is None
