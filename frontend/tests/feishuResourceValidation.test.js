import assert from 'node:assert/strict'
import test from 'node:test'
import { setTimeout as wait } from 'node:timers/promises'
import { createFeishuResourceValidation } from '../src/pages/lens/feishuResourceValidation.js'

test('adding and editing URLs preserves other results and checks only new URLs', async () => {
  let result
  const calls = []
  const validation = createFeishuResourceValidation((value) => {
    result = value
  }, 0)
  const validate = async (url) => {
    calls.push(url)
    if (url === 'bad') throw new Error('unavailable')
    return 'accessible'
  }
  validation.update('onepro', ['good'], validate)
  await wait(10)
  validation.update('onepro', ['good', 'bad'], validate)
  assert.equal(result.details.resources[0].status, 'success')
  await wait(10)
  assert.deepEqual(calls, ['good', 'bad'])
  assert.deepEqual(
    result.details.resources.map((r) => r.status),
    ['success', 'failed']
  )
  validation.update('onepro', ['good', 'better', ''], validate)
  await wait(10)
  assert.deepEqual(calls, ['good', 'bad', 'better'])
  assert.equal(result.status, 'pending')
  assert.equal(result.details.resources[2].status, '')
  validation.update('onepro', ['good', 'better'], validate)
  assert.equal(result.status, 'success')
  validation.reset()
})

test('late replies after removal, connection change or closing cannot overwrite state', async () => {
  let result
  const resolvers = []
  const validation = createFeishuResourceValidation((value) => {
    result = value
  }, 0)
  const validate = () => new Promise((resolve) => resolvers.push(resolve))
  validation.update('onepro', ['first'], validate)
  await wait(10)
  validation.update('onepro', ['second'], validate)
  await wait(10)
  resolvers[0]('old')
  await wait(0)
  assert.equal(result.details.resources[0].url, 'second')
  assert.equal(result.details.resources[0].status, 'checking')
  validation.update('other', ['second'], validate)
  await wait(10)
  resolvers[1]('old connection')
  await wait(0)
  assert.equal(result.details.resources[0].status, 'checking')
  validation.reset()
  const previous = result
  resolvers[2]('closed')
  await wait(0)
  assert.equal(result, previous)
})
