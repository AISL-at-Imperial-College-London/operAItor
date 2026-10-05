"""Regression checks for v0.5.6 Overview alarm annunciation."""
from integrated_plant.alarms import AlarmState
import operator_console as oc


def test_returned_unack_alarm_remains_faceplate_annunciated():
    s = oc.env.alarms.status["R_P_H"]
    original = s.state
    try:
        s.state = AlarmState.RTN_UNACK
        assert oc._tag_alarm_visual_state("cstr_pressure_bar") == "RTN_UNACK"

        oc._overview_flash_phase = True
        oc._refresh_overview_indicators()
        idx = next(i for i, spec in enumerate(oc._OVERVIEW_INDICATOR_SPECS) if spec[0] == "r_press")
        assert oc.overview_indicator_source.data["fill"][idx] == "#ffd400"
        assert oc.overview_indicator_source.data["line"][idx] == "#fff176"

        oc._overview_flash_phase = False
        oc._refresh_overview_indicators()
        assert oc.overview_indicator_source.data["fill"][idx] == "#6f5900"
    finally:
        s.state = original


def test_acknowledged_active_alarm_is_steady_amber():
    s = oc.env.alarms.status["R_T_H"]
    original = s.state
    try:
        s.state = AlarmState.ACTIVE_ACK
        oc._overview_flash_phase = True
        oc._refresh_overview_indicators()
        idx = next(i for i, spec in enumerate(oc._OVERVIEW_INDICATOR_SPECS) if spec[0] == "r_temp")
        c1 = oc.overview_indicator_source.data["fill"][idx]
        oc._overview_flash_phase = False
        oc._refresh_overview_indicators()
        c2 = oc.overview_indicator_source.data["fill"][idx]
        assert c1 == c2 == "#ffb300"
    finally:
        s.state = original
