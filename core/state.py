"""
core/state.py
Shared in-memory state between listener, notifier, and agent.
"""

from core.signal import Signal

# Signals waiting for user confirmation: {signal_id: Signal}
pending: dict[str, Signal] = {}

# Close alerts waiting for user confirmation: {close_id: [ticket, ...]}
pending_closes: dict[str, list] = {}
