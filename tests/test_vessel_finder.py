"""Tests for vessel_finder.score_availability and is_africa_trade."""

import pytest

from vessel_finder import score_availability, is_africa_trade


# ---------------------------------------------------------------------------
# score_availability
# ---------------------------------------------------------------------------


def test_score_availability_stationary_no_dest():
    """Stationary vessel (speed=0), no destination, Cargo type, distance=2.0."""
    vessel = {
        "speed": 0,
        "destination": "",
        "type_specific": "Cargo",
        "distance": 2.0,
    }
    result = score_availability(vessel)

    assert result["score"] >= 0.5
    assert "Available" in result["label"]


def test_score_availability_en_route():
    """Speed=12, destination set, Container type -> low score, En Route."""
    vessel = {
        "speed": 12,
        "destination": "ROTTERDAM",
        "type_specific": "Container",
    }
    result = score_availability(vessel)

    assert result["score"] < 0.2
    assert result["label"] == "En Route"


def test_score_availability_capped_at_one():
    """All positive signals simultaneously -> score must not exceed 1.0."""
    vessel = {
        "speed": 0,
        "destination": "",
        "type_specific": "General Cargo",
        "distance": 5.0,
    }
    result = score_availability(vessel)

    assert result["score"] <= 1.0


def test_score_availability_missing_distance():
    """Vessel dict without 'distance' key -> no anchorage bonus (+0.20)."""
    vessel_with_dist = {
        "speed": 0,
        "destination": "MOMBASA",
        "type_specific": "Cargo",
        "distance": 2.0,
    }
    vessel_no_dist = {
        "speed": 0,
        "destination": "MOMBASA",
        "type_specific": "Cargo",
    }

    score_with = score_availability(vessel_with_dist)["score"]
    score_without = score_availability(vessel_no_dist)["score"]

    # The vessel with distance should get the +0.20 anchorage bonus
    assert score_with == score_without + 0.20


# ---------------------------------------------------------------------------
# is_africa_trade
# ---------------------------------------------------------------------------


def test_is_africa_trade_destination_match():
    """dest='MOMBASA', type='General Cargo' -> returns a non-None reason."""
    vessel = {
        "destination": "MOMBASA",
        "type_specific": "General Cargo",
    }
    reason = is_africa_trade(vessel)

    assert reason is not None


def test_is_africa_trade_flag_match():
    """country_iso='KE', type='Cargo', dest='' -> returns a non-None reason."""
    vessel = {
        "country_iso": "KE",
        "type_specific": "Cargo",
        "destination": "",
    }
    reason = is_africa_trade(vessel)

    assert reason is not None


def test_is_africa_trade_no_match():
    """dest='ROTTERDAM', type='Cargo', flag='NL' -> returns None."""
    vessel = {
        "destination": "ROTTERDAM",
        "type_specific": "Cargo",
        "country_iso": "NL",
    }
    reason = is_africa_trade(vessel)

    assert reason is None


def test_is_africa_trade_no_false_positive_dar():
    """dest='DARDANELLES', type='Cargo' -> returns None.

    AFRICA_KEYWORDS uses 'DAR ES SALAAM' (not bare 'DAR'), so 'DARDANELLES'
    should not match. This test verifies no false positives from substring matching.
    """
    vessel = {
        "destination": "DARDANELLES",
        "type_specific": "Cargo",
    }
    reason = is_africa_trade(vessel)

    # The intent is that DARDANELLES should NOT be flagged as Africa trade.
    # If this test fails, the keyword matching needs to be tightened (e.g.
    # use whole-word matching or replace 'DAR' with 'DAR ES SALAAM').
    assert reason is None
