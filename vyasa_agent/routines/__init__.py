# SPDX-License-Identifier: Apache-2.0
"""Routines — cron-scheduled and webhook-triggered tasks per employee.

The routines layer discovers ``plans/<employee_id>/*.yaml`` at boot, parses
each file into a :class:`Routine`, and registers either a cron wake-loop or a
webhook callback for it.  At fire time the routine's ``prompt`` is dispatched
to its owning employee via :meth:`FleetManager.dispatch`; the resulting
:class:`TurnResult` is delivered to ``deliver_to`` (telegram, graph node,
slack channel, or email) and the fire is audited with a ``routine_fired``
node written to Graphify.
"""

from .runner import RoutineRunner
from .types import DeliveryTarget, Routine, RoutineFire

__all__ = ["DeliveryTarget", "Routine", "RoutineFire", "RoutineRunner"]
