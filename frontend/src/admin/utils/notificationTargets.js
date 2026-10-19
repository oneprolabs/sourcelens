/** Shared labels for notification configuration, destinations and delivery history. */
export const nativeTargetTypes = ['feishu', 'wecom', 'dingtalk']

const labelKeys = {
  feishu: 'typeFeishu',
  wecom: 'typeWecom',
  wechat: 'typeWecom',
  dingtalk: 'typeDingtalk',
  email: 'typeEmail',
  webhook: 'typeWebhook'
}

export function notificationTargetLabel(type, t) {
  return labelKeys[type]
    ? t(`notificationManagement.channels.${labelKeys[type]}`)
    : type || '–'
}

export function notificationChannelStats(rows) {
  const counts = new Map(
    [...nativeTargetTypes, 'email', 'webhook'].map((type) => [type, 0])
  )
  for (const row of Array.isArray(rows) ? rows : []) {
    const type = row.provider_type === 'wechat' ? 'wecom' : row.provider_type
    counts.set(type, (counts.get(type) || 0) + Number(row.count || 0))
  }
  const total = [...counts.values()].reduce((sum, count) => sum + count, 0)
  return [...counts].map(([provider_type, count]) => ({
    provider_type,
    count,
    percent: total > 0 ? Math.round((count / total) * 100) : 0
  }))
}
