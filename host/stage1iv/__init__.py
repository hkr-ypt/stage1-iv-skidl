"""stage1iv — host library for the Stage 1 I-V characterization instrument.

Protocol: docs/firmware/protocol.md (version 2). Main entry points:

* :class:`stage1iv.client.Instrument` — command/response client, events,
  keep-alive, sweeps with gap recovery;
* :mod:`stage1iv.sweeps` — exact-grid sweep builders;
* :mod:`stage1iv.calibration` — fits and the guided calibration workflow;
* :mod:`stage1iv.writers` — CSV + JSON sidecar output;
* :mod:`stage1iv.discovery` — find instruments by USB VID/PID/serial;
* :mod:`stage1iv.sim` — run the firmware simulator for tests.

Accuracy figures are provisional until the bring-up measurements
(docs/firmware/verification.md) have been made.
"""

from .client import Instrument, SweepResult
from .errors import (BringupBuildRefused, DeviceError, ProtocolError, SessionLost,
                     Stage1IVError, SweepFailed)
from .protocol import PROTOCOL_VERSION
from .records import Flag, PTRecord

__version__ = "0.1.0"

__all__ = [
    "Instrument", "SweepResult", "PTRecord", "Flag", "Stage1IVError", "DeviceError",
    "ProtocolError", "SessionLost", "SweepFailed", "BringupBuildRefused",
    "PROTOCOL_VERSION", "__version__",
]
