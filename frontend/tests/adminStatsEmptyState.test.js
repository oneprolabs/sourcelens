import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { hasStatisticsData } from '../src/admin/utils/statisticsState.js'

test('statistics totals distinguish empty payloads from populated data', () => {
  assert.equal(hasStatisticsData(null), false)
  assert.equal(hasStatisticsData(undefined), false)
  assert.equal(hasStatisticsData(0), false)
  assert.equal(hasStatisticsData('0'), false)
  assert.equal(hasStatisticsData(-1), false)
  assert.equal(hasStatisticsData(1), true)
  assert.equal(hasStatisticsData('12'), true)
})

test('task statistics replaces zero charts with an actionable empty state', async () => {
  const contents = await readFile(
    new URL('../src/admin/pages/TaskManagement/Stats.vue', import.meta.url),
    'utf8'
  )

  assert.match(contents, /data-testid="task-stats-empty"/)
  assert.match(contents, /hasStatisticsData\(stats\.value\?\.total\)/)
  assert.match(contents, /name: 'TaskManagementList'/)
  assert.match(contents, /v-else-if="hasStatsData"/)
})

test('notification statistics links an empty period to channel setup', async () => {
  const contents = await readFile(
    new URL('../src/admin/pages/Notifications/Stats.vue', import.meta.url),
    'utf8'
  )

  assert.match(contents, /data-testid="notification-stats-empty"/)
  assert.match(
    contents,
    /hasStatisticsData\(statsData\.value\?\.summary\?\.total\)/
  )
  assert.match(contents, /name: 'PlatformNotificationsChannels'/)
  assert.match(contents, /v-else-if="hasStatsData"/)
})
