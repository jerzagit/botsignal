"""
core/state.py
Shared in-memory state between listener, notifier, and agent.
"""

import time
from datetime import datetime
from core.signal import Signal

# Signals waiting for user confirmation: {signal_id: Signal}
pending: dict[str, Signal] = {}

# Close alerts waiting for user confirmation: {close_id: [ticket, ...]}
pending_closes: dict[str, list] = {}

# Daily loss tracking
_daily_loss_reset_time: float = 0.0
_daily_loss: float = 0.0


def get_daily_loss() -> float:
    """Return current day's累计 loss."""
    return _daily_loss


def add_daily_loss(amount: float) -> None:
    """Add to daily loss (positive = loss, negative = profit)."""
    global _daily_loss, _daily_loss_reset_time
    now = time.time()
    
    # Reset at midnight local time
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    
    if now >= _daily_loss_reset_time:
        _daily_loss = 0.0
        # Next midnight
        _daily_loss_reset_time = today_start + 86400
    
    _daily_loss += amount
