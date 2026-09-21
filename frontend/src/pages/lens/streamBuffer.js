export function createStreamTextBuffer({
  onFlush,
  schedule = (callback) => requestAnimationFrame(callback),
  cancel = (frame) => cancelAnimationFrame(frame),
  revealPerTick = 0,
  catchUpRatio = 0.25
}) {
  let pending = ''
  let frame = null

  function cancelScheduled() {
    if (frame !== null) {
      cancel(frame)
      frame = null
    }
  }

  // Dump everything buffered at once (stream finished, reconnect sync, ...).
  function flush() {
    cancelScheduled()
    if (!pending) return
    const chunk = pending
    pending = ''
    onFlush(chunk)
  }

  // Reveal a slice per tick so display speed is decoupled from arrival
  // speed: bursts are spread out and slow streams are shown as they arrive.
  // When far behind, widen the slice proportionally to catch up quickly.
  function tick() {
    frame = null
    if (!pending) return
    let take
    if (revealPerTick > 0) {
      const budget = Math.max(
        revealPerTick,
        Math.ceil(pending.length * catchUpRatio)
      )
      take = pending.slice(0, budget)
      pending = pending.slice(budget)
    } else {
      take = pending
      pending = ''
    }
    onFlush(take)
    if (pending) frame = schedule(tick)
  }

  return {
    push(text) {
      pending += String(text || '')
      if (pending && frame === null) {
        frame = schedule(tick)
      }
    },
    flush,
    clear() {
      cancelScheduled()
      pending = ''
    }
  }
}
