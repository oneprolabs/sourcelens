import assert from 'node:assert/strict'
import test from 'node:test'

import { recoverRunStream } from '../src/pages/lens/chatRunStream.js'

const frame = (event) => `data: ${JSON.stringify(event)}\n\n`

test('reconnects after premature EOF and reconciles a genuine partial outcome', async () => {
  const received = []
  const statuses = []
  let connections = 0
  const result = await recoverRunStream({
    runUuid: 'run-a',
    signal: new AbortController().signal,
    isCurrent: () => true,
    openStream: async () =>
      new Response(
        frame({
          type: 'sync',
          status: 'streaming',
          content: 'Invoice progress'
        }) + (connections++ ? frame({ type: 'done', outcome: 'partial' }) : '')
      ),
    getRun: async () => ({
      uuid: 'run-a',
      status: connections === 1 ? 'streaming' : 'done',
      outcome: 'partial'
    }),
    onEvent: (event) => received.push(event),
    onRun: (run) => statuses.push(run.status),
    wait: async () => {}
  })
  assert.equal(connections, 2)
  assert.deepEqual(statuses, ['streaming', 'done'])
  assert.equal(received.at(-1).outcome, 'partial')
  assert.equal(result.status, 'done')
})

test('recovers from stream and status request failures without declaring failure', async () => {
  let connections = 0
  let polls = 0
  const received = []
  const result = await recoverRunStream({
    runUuid: 'run-a',
    signal: new AbortController().signal,
    isCurrent: () => true,
    openStream: async () => {
      if (++connections === 1) throw new Error('connection reset')
      return new Response(
        frame({ type: 'sync', status: 'failed', outcome: 'blocked' })
      )
    },
    getRun: async () => {
      if (++polls === 1) throw new Error('temporary network failure')
      return { uuid: 'run-a', status: 'failed', error: 'EXECUTION_FAILED' }
    },
    onEvent: (event) => received.push(event),
    onRun: () => {},
    wait: async () => {}
  })
  assert.equal(result.status, 'failed')
  assert.equal(result.error, 'EXECUTION_FAILED')
  assert.equal(received.length, 1)
})

test('drops buffered frames from a superseded subscription, even on A-B-A navigation', async () => {
  let generation = 1
  let releaseRead
  const received = []
  const response = new Response(
    new ReadableStream({
      start(controller) {
        releaseRead = () => {
          controller.enqueue(
            new TextEncoder().encode(
              frame({ type: 'token', content: 'Old answer' })
            )
          )
          controller.close()
        }
      }
    })
  )
  const reading = recoverRunStream({
    runUuid: 'run-a',
    signal: new AbortController().signal,
    isCurrent: () => generation === 1,
    openStream: async () => response,
    getRun: async () => {
      assert.fail('stale stream must not poll')
    },
    onEvent: (event) => received.push(event),
    onRun: () => {}
  })
  await new Promise((resolve) => setImmediate(resolve))
  generation = 3
  releaseRead()
  await assert.rejects(reading, { name: 'AbortError' })
  assert.deepEqual(received, [])
})

test('aborting recovery prevents a delayed status response from changing another run', async () => {
  const controller = new AbortController()
  let resolveRun
  const applied = []
  const reading = recoverRunStream({
    runUuid: 'run-a',
    signal: controller.signal,
    isCurrent: () => true,
    openStream: async () => new Response(''),
    getRun: () =>
      new Promise((resolve) => {
        resolveRun = resolve
      }),
    onEvent: () => {},
    onRun: (run) => applied.push(run)
  })
  await new Promise((resolve) => setImmediate(resolve))
  controller.abort()
  resolveRun({ uuid: 'run-a', status: 'done' })
  await assert.rejects(reading, { name: 'AbortError' })
  assert.deepEqual(applied, [])
})

test('parses split UTF-8 frames and cancels a terminal stream that stays open', async () => {
  const bytes = new TextEncoder().encode(
    frame({ type: 'token', content: '发票' }) +
      frame({ type: 'done', status: 'done', outcome: 'completed' })
  )
  let cancelled = false
  const received = []
  const result = await recoverRunStream({
    runUuid: 'run-a',
    signal: new AbortController().signal,
    isCurrent: () => true,
    openStream: async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            for (let i = 0; i < bytes.length; i += 3)
              controller.enqueue(bytes.slice(i, i + 3))
          },
          cancel() {
            cancelled = true
          }
        })
      ),
    getRun: async () => ({ uuid: 'run-a', status: 'done' }),
    onEvent: (event) => received.push(event),
    onRun: () => {}
  })
  assert.equal(result.status, 'done')
  assert.equal(received[0].content, '发票')
  assert.equal(cancelled, true)
})
