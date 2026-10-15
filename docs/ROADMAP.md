# Roadmap (obrint fork)

What this fork has shipped, what is next, and why. The goal is an Alarm.com integration that a 24/7 alarm can
depend on: state that matches Alarm.com, failures that are visible, and no surprises from the integration itself.
Update this file with every release.

## Shipped

| Release | What changed |
|---|---|
| 2026.10.14.1 | The 5-minute safety-net refresh and the reconnect refresh actually re-fetch devices (`set.pop()` emptied the target list after the first refresh). Device state is reconciled after every websocket (re)connection and OpenedClosed event, and a refresh can no longer overwrite a newer websocket event. Session repair cannot deadlock; failed logins back off; outages are not reported as bad credentials; the websocket never stops reconnecting; setup errors are retried. HA 2026.10 setup slowdown fixed. |
| 2026.10.14.2 | Optional "Clear motion after" option. A device is removed only after three refreshes in a row without it. `Sensor.subtype` fixed. |
| 2026.10.14.3 | A device Alarm.com drops is removed whole (all its entities), and one failing event subscriber can no longer break reconnects or unloading. |
| 2026.10.15.1 | A refused endpoint (406/423) no longer counts as an authentication failure, which had caused a re-login every 5 minutes. Refresh-triggered re-logins are limited to one per 10 minutes; device types the system lacks are not requested; debug dumps redact cookies, keys and passwords. **Regression:** the "no devices" skip also caught the systems controller, so setup failed until 2026.10.15.2 (about 10 minutes). |
| 2026.10.15.2 | Only controllers known to have no devices are skipped. |

## Lessons that shape the plan

- **Silent failures are the real risk.** The two worst production bugs so far (the dead reconnect on 10-14 and the login
  loop on 10-15) left entities looking normal. The websocket kept working while the safety net was broken. Health has to be
  visible as entities and alerts, not only in the log.
- **Every login signs the account's other sessions out.** Anything that logs in more than necessary is user-visible.
- **Tests that mock one layer miss cross-layer bugs.** The login loop needed the request layer's exception mapping,
  the bridge's refresh and the hub's reconnect together, and the 10-15 setup regression came from setup and refresh
  sharing `_refresh()`. Every unit test passed both times.

## Next

Ordered by value for reliability. Each item names how we know it is done.

### 1. Integration test harness (next release)

A fake Alarm.com (recorded JSON:API responses plus a scripted websocket) that runs setup, refresh, reconnect,
session expiry, 423 endpoints and device removal through the real bridge, hub and entities.

Done when: the dead reconnect, the login loop and the setup regression each have a regression test at that
level, and the harness runs in CI before every release.

### 2. Health you can see

Diagnostic entities on the system device:

- `binary_sensor` *Alarm.com connection*: websocket connected (on) or not.
- `sensor` *Last successful refresh* (timestamp) and *Last login* (timestamp, with the reason as an attribute:
  setup, session repair, reconnect).
- `sensor` *Logins (24 h)* and *Refresh failures (24 h)*.
- `sensor` *State corrections (24 h)*: how often a refresh changed a state the websocket had missed. This is the
  direct measure of how far Home Assistant drifts from Alarm.com.
- A Repairs issue when logins exceed 6 per hour or the refresh has failed for 30 minutes.

Done when: today's login loop would have shown up on the dashboard within an hour, with no log reading.

### 3. CI and one-command releases

GitHub Actions on the fork: `pytest` and `ruff` on every push; a `workflow_dispatch` release job that stamps the
version, builds `alarmdotcom.zip` and publishes the release, replacing the manual steps.

Done when: a release is one click and cannot go out with failing tests.

### 4. Reconfigure flow

Change the Alarm.com username or password from the integration's menu without deleting and re-adding it, which today
regenerates every entity ID.

### 5. Send the generic fixes upstream

Offer the fixes that are not specific to this fork as pull requests to SeloraHomes: refresh target list, session
repair and backoff, event broker isolation, removed-device cleanup, 406/423 classification, debug redaction. Smaller
diff to carry, and other users get them.

### 6. Small cleanups

- `asyncio.iscoroutinefunction` is deprecated in Python 3.14; switch to `inspect.iscoroutinefunction`.
- Apply `ruff format` to the files that drifted upstream, in a commit of its own.

## Later / ideas

- Event entities for arm, disarm and alarm events with the user who did it, from the activity feed.
- Per-zone "last tripped" timestamps that survive restarts.
- A local fallback path (NetworX serial or network module) alongside the cloud, if hardware is ever added.
