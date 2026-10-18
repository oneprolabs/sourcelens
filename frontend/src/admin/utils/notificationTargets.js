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
