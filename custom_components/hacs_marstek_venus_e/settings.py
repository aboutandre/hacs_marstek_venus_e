"""Marstek Venus E — centralized device tuning.

Single source of truth for the device's transport + polling behaviour. Every
tunable number lives here (not buried in udp_client / coordinator). Edit one
value to retune; where a setting is also exposed in the config/options flow, the
runtime value overrides these — these are the fallback defaults.

⚠️  These dials INTERACT. Keep the documented relationships or they fight each
    other (the thing that caused the overnight UDP-timeout spam):

      REQUEST_TIMEOUT_S  ≪  the controller tick (Wattsmith ~3 s)
          A lost packet must retry fast so it never starves a setpoint write.
      MODE/BATTERY_INFO intervals  ≫  SCAN_INTERVAL_S
          Those values barely change; polling them every cycle is wasted radio.
      REQUEST_MIN_GAP_S  >  0
          The battery is single-threaded over UDP; give it breathing room
          between back-to-back requests (all requests are serialized by a
          per-battery lock in udp_client).
"""
from typing import Final

# --- Connection --------------------------------------------------------------
DEFAULT_PORT: Final[int] = 30000

# --- UDP transport (per battery; serialized by MarstekUDPClient's lock) -------
REQUEST_TIMEOUT_S: Final[float] = 4.0      # device replies in ~1 s; 4 s covers a slow reply + margin
REQUEST_MAX_ATTEMPTS: Final[int] = 3       # retries on packet loss (UDP is lossy on the shared radio)
REQUEST_MIN_GAP_S: Final[float] = 0.2      # min spacing between two requests to the SAME battery

# --- Polling cadence (per battery) -------------------------------------------
# The control loop (Wattsmith) drives off the fast grid sensor, not these — so
# these can be relaxed. Steady state = ONE request/cycle (ES.GetStatus).
SCAN_INTERVAL_S: Final[int] = 60           # ES.GetStatus (SOC + power) — the per-cycle poll
MODE_POLL_INTERVAL_S: Final[int] = 300     # ES.GetMode (operating-mode sensor) — was every cycle
BATTERY_INFO_INTERVAL_S: Final[int] = 3600  # Bat.GetStatus (temperature / rated capacity)
