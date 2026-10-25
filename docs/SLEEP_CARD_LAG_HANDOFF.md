# Sleep-Insights Card — Week/Month Lag Handoff (2026-10-24)

> Handoff doc for an AI agent to fix the "week and month is very lag" problem.
> Everything verified today is in here with numbers. Read fully before editing.

## TL;DR (中文)

卡片 (Lovelace JS) 的 week/month tab 很 lag。今天已做過一次效能修復 (v19→v20,把「每次 render 重 parse 15k 列 history」改成「fetch 時算一次並快取」),隔離基準測試證實 v20 在 Chromium 裡快 ~12-21 倍、API 只花 0.07-0.37s,部署位元組驗證一致。**但 user 反應 hard reload 後問題照舊**——代表隔離測試沒抓到真實環境裡的根因。請從「診斷步驟」開始,先確認 user 端實際執行的版本與裝置,再查下方「假設清單」。目前最可能的殘餘根因是:**卡片在每一次 HA state 更新時都整個 `shadowRoot.innerHTML` 重建**(v20 仍如此),在真實 frontend 高頻 hass 更新 + 手機 CPU 上就是持續性 jank;對應修法是「相關 states 沒變就 skip render」(見假設 #2 的具體程式碼位置)。

## Symptom

- Dashboard: `http://homeassistant.local:8123/dashboard-cradlewise/sleep-insights`
- View: custom card `cradlewise-sleep-card`, Day/Week/Month tabs.
- User report: week and month tabs are "very lag" (scrolling/clicking feel stuck). Persisted after card v20 deploy + told user to hard-reload (Ctrl+Shift-R). User's exact follow-up: "same problem".
- Severity: Day tab is usable; week/month specifically named as laggy. Unknown whether user tested on desktop, phone, or both, and whether lag is constant jank or a one-time delay on tab open. **Confirm this first.**

## Where things live

| What | Path |
|---|---|
| Card source (repo, branch `feature/local-webrtc-camera`) | `~/software/ha-cradlewise/lovelace/cradlewise-sleep-card.js` |
| Deployed file on HA (Beelink) | `/config/www/community/cradlewise-sleep-card/cradlewise-sleep-card.js` |
| Resource registration | `/config/.storage/lovelace_resources`, entry `...cradlewise-sleep-card.js?v=30` |
| Dashboard config | `/config/.storage/lovelace.dashboard_cradlewise` (view `sleep-insights`) |
| HA access pattern | `ssh ha` (root@192.168.31.31); HA API token on nas2: `/volume1/homes/kai/crib-funnel/cron/ha.env` (vars `HA_URL`, `HA_TOKEN`) |
| Bench harness (may be gone, /tmp) | `/tmp/sleep_bench/{bench.html,v19.js,v20.js,run_bench.py}` (rebuildable, see "Evidence" below) |

Deploy recipe (PROVEN): `scp lovelace/cradlewise-sleep-card.js ha:/config/www/community/cradlewise-sleep-card/` → bump `?v=` in `/config/.storage/lovelace_resources` (edit while HA running, then `ha core restart` — the restart is what makes the .storage edit stick) → browser hard reload. Verify with `md5sum` on both sides + `grep -o 'cradlewise-sleep-card.js?v=[0-9]*' /config/.storage/lovelace_resources`.

## Architecture / data flow

```
Cradlewise cloud REST API  (Cognito+SigV4, read-only)
   └─ cradlewise-poller (NAS2 docker, 75 s loop; /volume1/homes/kai/... poller.py)
        └─ MQTT discovery → entities sensor.cradlewise_avo_cloud_*  (HA, Beelink)
             ├─ HA recorder → MariaDB on nas2 192.168.31.20 (purge_keep_days 365)
             └─ card reads CURRENT states from hass.states
card week/month data:
   hass.callApi('GET', 'history/period/<start>?filter_entity_id=<total_sleep>&end_time=<...>&minimal_response=true&no_attributes=true')
   → day-totals map keyed by SLEEP-DAY (8am–8am; a 3am state belongs to the prior day's key)
```

- Key entity: `sensor.cradlewise_avo_cloud_total_sleep_min` — ~236 rows/day in recorder (8d≈1,910 rows/130 KB; 62d≈15,107 rows/1 MB).
- Card versioning: header comment says `v20`; resource URL currently `?v=30` (card v19 was `?v=29`, v20 bumped to `?v=30`).
- Card internals relevant to perf:
  - `set hass` (≈line 276): throttles the history fetch to every `_FETCH_INTERVAL=300_000` ms, then **calls `_render()` on EVERY invocation**.
  - `_render()` → `_renderMain()` rebuilds the ENTIRE `shadowRoot.innerHTML` (including a multi-KB `<style>` block) and re-attaches all listeners, every render.
  - v19 bug (fixed in v20): `_buildWeekBars()` → `_byDayTotals()` re-iterated ALL raw history rows (15k × `new Date()` parse) on every render.
  - v20: `_byDayTotals(arr)` is now pure + computed once per fetch and cached (`this._history.byDay`); week fetch shrunk to 8 days; 62-day month payload is lazy (`_loadMonthHistory()`, 30-min throttle, only fires when Month tab is active).

## Evidence gathered today (all measured, don't redo)

1. **API latency is NOT the problem** (timed from nas2, adjacent to HA):
   - 8-day window: 0.07 s, 1,910 rows, 130 KB
   - 62-day window: 0.37 s, 15,107 rows, 1,030 KB
2. **Isolated render bench in real headless Chromium** (both card versions, identical mock data — 15k-row history, 300 hass updates):

   | metric | v19 (deployed as ?v=29) | v20 (current) |
   |---|---|---|
   | render day / week / month (ms, warm avg over 200) | 7.6 / 7.5 / 14.8 | 0.66 / 0.5 / 0.69 |
   | 300 simulated hass updates (ms total) | 2,314 | 190 |
   | `_byDayTotals()` single call on 15k rows | ~6.8 ms | cached (~0) |

3. **Deploy verified**: md5 of deployed file == repo file; resource says `?v=30`; `node --check` passes; served file header shows v20 (`curl http://192.168.31.31:8123/local/community/cradlewise-sleep-card/cradlewise-sleep-card.js | head -3`).
4. **Known HA API gotcha that caused an earlier bug**: `history/period/<start>` WITHOUT `end_time` returns only start+24h. Always pass `end_time`.

So: in isolation v20 is ~12-21× faster, yet the user still reports the same lag. The bench therefore does NOT reproduce the user's real bottleneck. Something in the real environment differs — that's your job.

## Diagnosis steps (do these first, in order)

1. **Confirm which JS the user's browser actually runs.** The fastest check: add the version to the card UI (e.g. tiny footer `v20 (r30)`), deploy as `?v=31`, have the user hard-reload and read the string. Do not assume the hard reload happened or that the Lovelace resource list re-fetched — switching tabs within an already-loaded dashboard keeps the OLD resource URL in memory; the page itself must reload.
2. **Confirm the device + character of the lag**: desktop or phone (HA Companion / mobile browser)? Constant jank while idle, or one-time delay on tab switch? Does the Day tab lag too? A one-time multi-second delay on opening the Month tab points at the 1 MB JSON fetch over WiFi on a phone; constant jank points at render-per-update.
3. **Measure the real update rate**: temporarily add to `set hass`: `const now=Date.now(); console.log('hass dt', now-(this._lastHassTs||0)); this._lastHassTs=now;` — have the user (or you via a same-origin test page) observe. The HA frontend calls `set hass` for every state change the dashboard subscribes to; with 1,500+ entities this can be several per second.
4. **Browser Performance profile** on the user's device (devtools → Record → click Week/Month, scroll, stop). Share the long tasks. This alone will likely settle which hypothesis below is right.

## Ranked hypotheses

1. **User's browser is still executing the old (v19) JS.** Everything server-side is verified new; if the lag feels *identical in character* to before, this is the top suspect. Verify with the visible-version-string trick (diagnosis step 1) before touching any logic.
2. **Full `shadowRoot.innerHTML` rebuild on EVERY hass update (v20 still does this).** In a real frontend `set hass` fires many times per second (all dashboard-subscribed entities); each call reparses the big `<style>`, tears down and rebuilds the whole DOM (including the canvas element on the Day tab), and re-attaches listeners. In isolation that's 0.5–0.7 ms, but in a real page the teardown/repaint churn on a phone can dominate. **Likely the robust fix regardless of #1**: in `set hass`, compute a cheap signature (the card's ~7 input states: sleep_phase, total_sleep, total_awake, soothe_count, nap_count, longest_nap, sleep_state + a `_renderEpoch` counter bumped by tab switches/history loads) and skip `_render()` when unchanged; coalesce renders with `requestAnimationFrame`. Patch points: `set hass` (≈line 276) and the places that call `this._render()` directly after state changes (tab clicks, `_monthOffset` nav, `_loadHistory`/`_loadMonthHistory` completion) — bump the epoch there so those still re-render.
3. **Day-tab canvas redraw loop**: `_drawC` runs via `requestAnimationFrame` per render and there is a `ResizeObserver` that redraws — on the Day tab only, but worth checking it doesn't fight the resize observer (observe → render → resize feedback). Week/month don't draw canvas, so only relevant if Day lags too.
4. **Month tab one-time fetch (1 MB JSON) on a phone over WiFi** — feels like a multi-second freeze on first open of the Month tab. Mitigations: split the 62-day fetch into per-month chunks, or fetch with `significant_changes_only`-style reduction… simplest: keep lazy load but render a skeleton immediately (already does — cells come from the week fallback map) and ensure the JSON.parse doesn't block on the main thread for ~200-400 ms (it may on a phone).
5. **Dashboard-level problem**: something else in `dashboard-cradlewise` (another card, a `conditional`, an update-all loop) making the whole view janky — the sleep card is then just the visible victim. Test by putting the card alone on a scratch dashboard view and comparing.

## Hard constraints (do not break)

- **NEVER connect MQTT with clientId `29976d6e-4bbf-433c-bdf6-0ed6606e3fc5`** (the crib's thing name — it kicks the real crib off AWS/cloud). Research identity `19c3aef9-…` is held by the running HA integration — never run `pycradlewise mqtt.py` or any listener with it while HA is up (one session per clientId on the crib broker; violations cause silent kick loops).
- **The crib hardware is currently OFFLINE** (went dark ~13:00 PDT 10-24: cloud shadow frozen, LAN 192.168.14.231 unreachable, ping fails, ports filtered). Consequences for you: `camera.cradlewise_avo_crib_camera` proxy 500s (its `state:"streaming"` is a hardcoded lie), cradlewise MQTT entities unavailable. **Do not chase these as part of the lag bug**, do not power-cycle or otherwise touch the crib remotely (it's a live nursery device; physical attention is Kai's call), no audible tests.
- `sensor.cradlewise_avo_cloud_*` values are currently FROZEN (poller republishes a stale shadow ~every 75 s without flagging staleness). The card will show stale day data until the crib is back — that is expected.
- `/home/kai` is itself a git repo — never `git add -A` there. The project repo is `/home/kai/software/ha-cradlewise` (private remote `mezl/ha-cradlewise`); `certs/` is gitignored — never commit certs or tokens.
- Keep the repo private (Kai flips public/releases himself).
- Commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- HA core restart takes ~90 s and is needed for `.storage/lovelace_resources` edits to stick; JS-only card changes themselves do NOT need a restart — only the `?v=` bump does.

## Bench rebuild (if /tmp/sleep_bench is gone)

`v19.js` = `git show e1b7f21:lovelace/cradlewise-sleep-card.js`; `v20.js` = current file. `bench.html` loads `./v{19,20}.js` via query param, mounts the element, feeds a mock `hass` (6 states + `callApi` returning synthesized history: ~240 rows/day totals, day-phase arrays), then times `_render()` per tab ×200 and a 300-iteration `set hass` storm. Run headless: `python3 -c "from playwright.sync_api import sync_playwright; ..."` (python playwright is installed on itx). Key result reproduced above — v20 render ≈0.5–0.7 ms vs v19 ≈7.5–14.8 ms.

## Commits / state

- `26a9dc4` (pushed, `feature/local-webrtc-camera`) = card v20 perf fix. Previous: `e1b7f21` = card v19 (Month tab + end_time fix), `5bd0acd` = webrtc_v2.py receiver.
- Deployed = repo = `?v=30` as of 2026-10-24 ~16:30 PDT.
- Working tree has unrelated untracked files (lovelace audio/lullabies cards, docs photo) — not part of this fix; leave them.