"""Laptop-side half of the MEIT belt: AI result in, vibration out.

Reading order, if you are new to this package:

* :mod:`laptop.haptic` — the mapping that matters. An AI result (direction,
  danger class, confidence) becomes a motor command. Pure functions, no I/O.
* :mod:`laptop.protocol` — how that command is put on the wire (BLE CMD v2/v3).
* :mod:`laptop.belt_client` — scanning, connecting, reconnecting, sequencing.
* :mod:`laptop.ios_motor_bridge` — the primary runtime, driven by
  ``meit-ios``'s ``/auto/status``.
* :mod:`laptop.ai_motor_bridge` / :mod:`laptop.ai_runner` — the standalone and
  bench runtime, which loads ``meit-ai`` directly.

Command-line tools: ``haptic_preview`` (offline), ``send_motor_test`` (belt only),
``ios_motor_bridge`` and ``ai_motor_bridge``.

See ``docs/HAPTIC_DESIGN.md`` for why the vibration patterns are what they are.
"""
