"""Full-duplex audio state machine and interruption coordinator."""
import asyncio
from enum import Enum
from typing import Callable, Optional

class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"

class DuplexCoordinator:
    """Coordinates state transitions, barge-in detection, and audio interruption."""

    def __init__(self, on_interrupt: Optional[Callable[[], None]] = None):
        self.state = VoiceState.IDLE
        self.on_interrupt = on_interrupt
        self._lock = asyncio.Lock()

    def set_state(self, new_state: VoiceState):
        self.state = new_state

    def handle_barge_in(self) -> bool:
        """
        Triggered when user speech activity is detected while agent is
        SPEAKING or THINKING.
        Returns True if barge-in was handled.
        """
        if self.state in [VoiceState.SPEAKING, VoiceState.THINKING]:
            self.state = VoiceState.INTERRUPTED
            if self.on_interrupt:
                self.on_interrupt()
            return True
        return False
