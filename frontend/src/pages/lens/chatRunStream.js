const TERMINAL_STATUSES = new Set([
  'awaiting_user_input',
  'done',
  'failed',
  'cancelled'
])

function waitForRetry(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer)
      reject(new DOMException('Stream aborted', 'AbortError'))
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, ms)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

export async function recoverRunStream({
  runUuid,
  openStream,
  getRun,
  onEvent,
  onRun,
  isCurrent,
  signal,
  wait = waitForRetry,
  retryMs = 3000
}) {
  const assertCurrent = () => {
    if (signal.aborted || !isCurrent()) {
      throw new DOMException('Stream superseded', 'AbortError')
    }
  }

  for (;;) {
    assertCurrent()
    let reader
    try {
      const response = await openStream(signal)
      assertCurrent()
      if (!response.ok || !response.body) throw new Error('SSE failed')
      reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let terminal = false
      while (!terminal) {
        const { done, value } = await reader.read()
        assertCurrent()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = buffer.replace(/\r\n/g, '\n')
        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1 && !terminal) {
          const raw = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const data = raw
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trimStart())
            .join('\n')
          if (data) {
            const event = JSON.parse(data)
            assertCurrent()
            onEvent(event)
            terminal =
              event.type === 'done' ||
              event.type === 'awaiting_user_input' ||
              (['sync', 'error'].includes(event.type) &&
                TERMINAL_STATUSES.has(event.status))
          }
          boundary = buffer.indexOf('\n\n')
        }
      }
    } catch {
      assertCurrent()
    } finally {
      await reader?.cancel().catch(() => {})
    }

    assertCurrent()
    try {
      const run = await getRun(runUuid)
      assertCurrent()
      onRun(run)
      if (TERMINAL_STATUSES.has(run?.status)) return run
    } catch {
      assertCurrent()
    }
    await wait(retryMs, signal)
  }
}
