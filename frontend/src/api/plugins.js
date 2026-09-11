import api from '@/api'

function unwrap(response) {
  return response?.data?.result ?? response?.data ?? null
}

export async function invokePluginRpc(pluginKey, method, params = {}) {
  const response = await api.post(
    `/lens/plugin-runtime/${pluginKey}/rpc/`,
    { method, params }
  )
  return unwrap(response)
}

export function startFeishuSelfRegister() {
  return invokePluginRpc('feishu', 'self_register.begin')
}

export function pollFeishuSelfRegister(deviceCode) {
  return invokePluginRpc('feishu', 'self_register.poll', {
    device_code: deviceCode
  })
}
