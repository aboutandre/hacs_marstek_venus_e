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
# Budget invariant (audit F-09): the WORST-CASE request chain must finish inside
# the brain's setpoint window, or healthy batteries idle past cd_time while one
# sick battery stalls the tick:
#   ATTEMPTS × TIMEOUT + (ATTEMPTS−1) × MIN_GAP  =  3×2.0 + 2×0.2  =  6.4 s
#   6.4 s  <  MANAGER_RESEND_S (7 s)  <  MANAGER_CD_TIME_S (10 s)   ✓
# (The device replies in ~1 s; 2 s still covers a slow reply. The previous 4 s
# violated this: 3×4+0.4 = 12.4 s > cd_time.)
REQUEST_TIMEOUT_S: Final[float] = 2.0      # per-attempt reply timeout
REQUEST_MAX_ATTEMPTS: Final[int] = 3       # retries on packet loss (UDP is lossy on the shared radio)
REQUEST_MIN_GAP_S: Final[float] = 0.2      # min spacing between two requests to the SAME battery

# --- Polling cadence (per battery) -------------------------------------------
# The control loop (Wattsmith) drives off the fast grid sensor, not these — so
# these can be relaxed. Steady state = ONE request/cycle (ES.GetStatus).
SCAN_INTERVAL_S: Final[int] = 60           # ES.GetStatus (SOC + power) — the per-cycle poll
MODE_POLL_INTERVAL_S: Final[int] = 300     # ES.GetMode (operating-mode sensor) — was every cycle
BATTERY_INFO_INTERVAL_S: Final[int] = 3600  # Bat.GetStatus (temperature / rated capacity)

# --- Riding out short device silences (hel-138) -------------------------------
# EMS firmware V150 closes its Local API for ~5-30 s on a fixed ~34 min timer
# (port refused or no reply). Without a grace window ONE failed ES.GetStatus made
# every entity unavailable for a whole SCAN_INTERVAL_S, which dropped the battery
# out of a "Fleet SOC" average and out of the brain's dispatch set for that minute.
# Instead: keep serving the last good reading and re-poll quickly.
#   POLL_RETRY_S  ≪  SCAN_INTERVAL_S   (re-poll soon after a failed poll)
#   POLL_GRACE_S  ≫  the longest silence seen (~30 s), but bounded: a battery that
#   is really gone must still go unavailable so the brain excludes it (≤ ~2 min).
POLL_RETRY_S: Final[int] = 10              # re-poll this soon after a failed ES.GetStatus
POLL_GRACE_S: Final[int] = 120             # serve the last good reading this long, then unavailable
