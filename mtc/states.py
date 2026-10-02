"""State names shared across modules. Defined once so no module relies on a magic string."""

ACTIVE = "ACTIVE"
UNAVAILABLE = "UNAVAILABLE"      # agent/machine data not available (also written when a poll fails)
AGENT_RESTART = "AGENT_RESTART"  # marker: the agent restarted, so earlier history has a gap
DATA_GAP = "DATA_GAP"            # marker: the agent's buffer wrapped past us, so observations were lost

# Time in these states is unknown, so it is left out of the utilization denominator.
UNMEASURED = frozenset({UNAVAILABLE, AGENT_RESTART, DATA_GAP})

EXECUTION = "Execution"  # MTConnect element name that holds the machine's execution state

# --- Conditions (alarms) ---
CONDITION = "Condition"  # pseudo element name: `--track Condition` turns on condition logging
CONDITION_LEVELS = {"Normal": "NORMAL", "Warning": "WARNING", "Fault": "FAULT", "Unavailable": "UNAVAILABLE"}
NORMAL = "NORMAL"
WARNING = "WARNING"
FAULT = "FAULT"
ACTIVE_LEVELS = frozenset({WARNING, FAULT})  # a condition is "active" while it is a warning or fault
