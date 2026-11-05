from openlaser.machine.states import (
    Action, ConnState, DisplayState, GrblState, JobState, allowed_actions, derive_display_state,
)


def test_display_priority():
    assert derive_display_state(ConnState.CONNECTED, GrblState.RUN, JobState.RUNNING, True) == DisplayState.ESTOP
    assert derive_display_state(ConnState.DISCONNECTED, GrblState.UNKNOWN, JobState.NONE, False) == DisplayState.DISCONNECTED
    assert derive_display_state(ConnState.DISCONNECTED, GrblState.UNKNOWN, JobState.NONE, False, True) == DisplayState.ERROR
    assert derive_display_state(ConnState.CONNECTING, GrblState.UNKNOWN, JobState.NONE, False) == DisplayState.CONNECTING
    assert derive_display_state(ConnState.CONNECTED, GrblState.ALARM, JobState.NONE, False) == DisplayState.ALARM
    assert derive_display_state(ConnState.CONNECTED, GrblState.HOLD, JobState.RUNNING, False) == DisplayState.PAUSED
    assert derive_display_state(ConnState.CONNECTED, GrblState.DOOR, JobState.RUNNING, False) == DisplayState.DOOR
    for g in GrblState:
        derive_display_state(ConnState.CONNECTED, g, JobState.NONE, False)


def test_every_display_state_has_label_and_symbol():
    for d in DisplayState:
        assert d.label and d.symbol


def test_actions_disconnected_only_connect():
    assert allowed_actions(ConnState.DISCONNECTED, GrblState.UNKNOWN, JobState.NONE, False) == {Action.CONNECT}


def test_alarm_allows_unlock_home_but_not_motion():
    acts = allowed_actions(ConnState.CONNECTED, GrblState.ALARM, JobState.NONE, False)
    assert {Action.UNLOCK, Action.HOME, Action.STOP, Action.ESTOP} <= acts
    assert Action.JOG not in acts and Action.START not in acts and Action.FIRE not in acts


def test_job_running_blocks_jog_and_fire():
    acts = allowed_actions(ConnState.CONNECTED, GrblState.RUN, JobState.RUNNING, False)
    assert Action.PAUSE in acts and Action.STOP in acts
    assert Action.JOG not in acts and Action.FIRE not in acts and Action.START not in acts


def test_estop_and_fire_block_everything_but_stop():
    for acts in (allowed_actions(ConnState.CONNECTED, GrblState.IDLE, JobState.NONE, True),
                 allowed_actions(ConnState.CONNECTED, GrblState.IDLE, JobState.NONE, False, fire_active=True)):
        assert acts == {Action.DISCONNECT, Action.ESTOP, Action.STOP}


def test_stop_always_available_when_connected():
    for g in GrblState:
        for j in JobState:
            acts = allowed_actions(ConnState.CONNECTED, g, j, False)
            assert Action.STOP in acts and Action.ESTOP in acts
