# Changelog

All notable changes to the Marstek Venus E Home Assistant Integration will be documented in this file.

## [2.5.1] - 2026-10-26

Rides out the periodic Local API silences of EMS firmware V150 (home-energy-lab hel-138).

### Fixed
- **One missed poll no longer makes the battery unavailable.** V150 closes its
  Local API for ~5-30 s on a fixed ~34 min timer. A failed `ES.GetStatus` used
  to raise straight away, so every entity went unavailable for a whole scan
  interval (60 s): a "Fleet SOC" template that counts unavailable as 0 dipped by
  a third, and an external brain dropped the battery from dispatch for that
  minute. Now the coordinator keeps the last good reading for up to
  `POLL_GRACE_S` (120 s) and re-polls every `POLL_RETRY_S` (10 s) until the
  device answers. A battery that stays silent past the grace window still goes
  unavailable. One INFO line per silence records how long it lasted.
- **"Connection refused" is retried like a timeout.** It used to fail the
  request on the first attempt. A refused attempt now waits out the rest of its
  timeout before retrying, so the worst-case request time is unchanged.

### Added
- `tests/test_poll_grace.py` — grace window, cadence restore, unavailable past
  grace, and refused-then-retry (7 tests).

## [2.5.0] - 2026-07-04

Reliability fixes from the 2026-07-04 home-energy-lab audit (F-04/F-09/F-10/F-12).

### Fixed
- **A device response is not an ack**: `set_mode` / `set_passive_mode` now check
  the Marstek's `set_result` field and raise on an explicit `false`, so the
  services report `{ok: false}` for a rejected command instead of a phantom ack.
  Only an explicit `false` is a rejection (older firmware may omit the field).
- **UDP duplicate-datagram guard**: a duplicated reply no longer raises
  `InvalidStateError` on the already-resolved response future.

### Changed
- **Request budget invariant restored**: `REQUEST_TIMEOUT_S` 4.0 → 2.0 s so the
  worst-case request chain (3 attempts + gaps = 6.4 s) fits inside an external
  brain's setpoint window (resend 7 s < cd_time 10 s). Previously 12.4 s — one
  deaf battery could stall the control tick past cd_time and briefly idle the
  healthy batteries.
- **No data refresh per passive setpoint**: `set_passive_mode` no longer calls
  `async_request_refresh()` — an external brain re-arms setpoints every few
  seconds and the per-write refresh roughly doubled the per-battery radio
  traffic (the congestion behind the June timeout storms).

### Added
- `tests/test_set_result_ack.py` — headless contract tests for the ack path,
  the no-refresh behavior and the duplicate-datagram guard (7 tests).
- `tests/test_timeout_handling.py` repaired (was broken since the settings
  refactor) and now asserts against `settings.py` instead of stale constants.

## [2.2.0] - 2026-04-12

### Added
- **LED Control**: Added a new `switch` entity to control the device panel LED (`Led.Ctrl`).
- **Assumed State**: Implemented assumed state for the LED switch since the API does not provide feedback on the current state.

### Removed
- **DOD Control**: Removed the `number` platform and `set_dod` service. API analysis confirmed that DOD is write-only and cannot be read, making it unreliable for a Home Assistant UI element.

### Changed
- **Improved Logging**: Service calls and switch actions now include the device IP address in the logs for better traceability.
- **Translations**: Updated English and French translation files for the new switch and cleaned up removed services.
- **Manifest Update**: Added `switch` to the supported platforms list.

## [2.1.0] - 2026-04-11

### Fixed
- **Power Limit Correction**: Updated battery charging/discharging power limit from 800W to 2500W to match device capabilities
- **Service Validation**: Updated all service schemas in `services.yaml` to allow power values up to 2500W
- **Documentation Update**: Corrected README.md and test files to reflect the proper 100-2500W power range

### Technical Details
- Updated `services.yaml` power selectors: `max: 2500` for all manual schedule slots
- Corrected documentation in README.md examples and constraints
- Updated test files to reflect proper power range validation
- Updated copilot instructions for accurate API documentation

## [2.0.0] - 2026-04-11

### Added
- **Differentiated Update Intervals**: Implemented separate scan intervals for different data sources:
  - **ES (Energy System)**: Updated every 30 seconds for real-time energy monitoring (ES.GetStatus, ES.GetMode, EM.GetStatus)
  - **Bat (Battery)**: Updated every 10 minutes to reduce device battery drain while keeping status available

- **Battery Sensors Available**: Re-enabled battery-related sensors with optimized update frequency:
  - `sensor.marstek_venus_e_battery_temperature` - Updates every 10 minutes
  - `sensor.marstek_venus_e_battery_rated_capacity` - Updates every 10 minutes
  - `binary_sensor.marstek_venus_e_battery_charging` - Updates every 10 minutes
  - `binary_sensor.marstek_venus_e_battery_discharging` - Updates every 10 minutes

### Fixed
- **Phase Power Data (Phase A/B/C) - Critical Fix**: 
  - Fixed incorrect zero values being reported for phase power sensors
  - Implemented intelligent data source merging between ES.GetMode and EM.GetStatus
  - EM.GetStatus now takes precedence for CT power fields when ES.GetMode returns zeros
  - `sensor.marstek_venus_e_phase_b_power` now correctly reports actual power values (e.g., 463W instead of 0W)

- **Power Limit Correction**: Updated battery charging/discharging power limit from 800W to 2500W to match device capabilities
- **Service Validation**: Updated all service schemas in `services.yaml` to allow power values up to 2500W
- **Documentation Update**: Corrected README.md and test files to reflect the proper 100-2500W power range

### Technical Details
- Updated `services.yaml` power selectors: `max: 2500` for all manual schedule slots
- Corrected documentation in README.md examples and constraints
- Updated test files to reflect proper power range validation
- Updated copilot instructions for accurate API documentation

## [1.1.0] - 2026-01-15

### Added
- **API Rev 2.0 Support**: Updated integration to support all commands from Marstek Device Open API Rev 2.0 (2026-01-06)
- **ES.GetMode Data**: Now retrieves and displays all operating mode data including:
  - Current operating mode (Auto, AI, Manual, Passive, UPS)
  - Grid-tied power and off-grid power
  - Battery state of charge
  - CT (Current Transformer) status (0: Not connected, 1: Connected)
  - CT Phase A, B, C power readings [W]
  - Total CT power [W]
  - Cumulative input energy [Wh] (*0.1 scaling applied)
  - Cumulative output energy [Wh] (*0.1 scaling applied)

- **EM.GetStatus Support**: Added energy meter status queries for additional CT meter data (when available)

- **New Sensors**:
  - CT Input Energy (from ES.GetMode) - cumulative input energy measurement
  - CT Output Energy (from ES.GetMode) - cumulative output energy measurement

- **New Services**:
  - `marstek_venus_e.set_dod`: Configure battery depth of discharge (30-88%)
  - `marstek_venus_e.set_ble_adv`: Enable/disable Bluetooth advertising
  - `marstek_venus_e.set_led_ctrl`: Control the device LED (on/off)

- **Enhanced Data Retrieval**:
  - Coordinator now retrieves both ES.GetMode and EM.GetStatus in parallel with main status
  - Automatic energy value scaling (*0.1) for API compliance
  - Added fallback mechanism for optional EM.GetStatus when unavailable

### Changed
- **API Timeout**: Increased from 20s to 30s to comply with API specification requirements
- **UDP Request Spacing**: Added constant for 30-second minimum time between requests per API guidelines
- **Error Handling**: Improved error logging with distinction between fatal and non-fatal failures
- **Data Update Strategy**: Mode and meter data are now collected on every coordinator update cycle

### Fixed
- **Full ES.GetMode Data Now Available**: Previously, ES.GetMode data was being fetched but not all fields were properly utilized
- **Energy Value Scaling**: Cumulative energy values from ES.GetMode are now correctly scaled by 0.1 as per API documentation
- **Mode Data Persistence**: Mode data is now properly merged and persisted through coordinator updates

### Technical Details
- Updated DEFAULT_TIMEOUT from 20.0s to 30.0s
- Added MIN_TIME_BETWEEN_REQUESTS constant (30.0s)
- Enhanced coordinator's _async_update_data() to handle multiple optional data sources
- New const.py entries for DOD configuration (DOD_MIN, DOD_MAX, DOD_DEFAULT)
- Added proper docstrings for all new API methods

### Developer Notes
- All UDP requests now include proper debug logging of payloads and responses
- Error messages distinguish between timeout errors (debug level) and other errors (error level)
- DoD values validate range 30-88 before sending to device
- Ble.Adv uses inverted logic: enable=0 (enable), enable=1 (disable) per API spec
- Led.Ctrl: state=1 (on), state=0 (off)

## [1.0.0] - 2025-11-20

### Initial Release
- Full integration with Marstek Venus E battery storage system
- Real-time battery, solar PV, and grid monitoring
- Operating mode control (Auto, AI, Manual, Passive)
- Manual schedule configuration for charging/discharging
- Energy dashboard integration
- Multi-language support (English, French)
- UDP JSON-RPC communication with device
- Automatic device discovery
- Support for current transformer (CT) meter sensors

### Features
- Comprehensive sensor suite (battery status, PV power, grid power, energy totals)
- Binary sensors for charging/discharging status and CT meter connection
- Operating mode selection entity
- Multiple configuration options
- Service-based control for mode changes and schedule management
