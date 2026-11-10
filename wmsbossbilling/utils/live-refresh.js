// Serialize manual/automatic requests; disabling polling does not disable manual reads.
export function createLiveRefresh(load, { interval = 30000, automatic = true, schedule = setTimeout, cancel = clearTimeout } = {}) {
  let active = false, disposed = false, busy = false, pending = false, timer = null, failures = 0
  function clear() { if (timer !== null) cancel(timer); timer = null }
  function queue(delay) { clear(); timer = schedule(refresh, delay) }
  async function refresh() {
    clear()
    if (!active || disposed) return
    if (busy) { pending = true; return }
    busy = true
    pending = false
    try { await load(); failures = 0 } catch { failures++ }
    finally {
      busy = false
      if (active && !disposed) {
        if (pending) queue(0)
        else if (automatic) queue(interval * 2 ** Math.min(failures, 2))
      }
    }
  }
  return {
    refresh,
    resume({ refresh: immediate = true } = {}) {
      if (disposed) return
      active = true; failures = 0
      if (immediate) return refresh()
      if (automatic && !busy) queue(interval)
    },
    configure(options) {
      automatic = options.automatic
      interval = options.interval
      failures = 0
      clear()
      if (active && automatic && !busy) queue(interval)
    },
    pause() { active = false; pending = false; clear() },
    dispose() { disposed = true; active = false; pending = false; clear() },
  }
}
