import { isRunAccessDenied } from '../../utils/runAccess.js'

export async function resolveRunStatus(getRun, runUuid) {
  try {
    return { resolved: true, run: await getRun(runUuid) }
  } catch (error) {
    if (isRunAccessDenied(error)) {
      return { resolved: false, run: null, accessDenied: true }
    }
    return { resolved: false, run: null }
  }
}

export function shouldShowRetryHint({
  isRunActive,
  messages,
  runStatusResolving
}) {
  if (runStatusResolving || isRunActive) return false
  const last = messages[messages.length - 1]
  if (last?.thinking) return false
  return !!last && last.role === 'assistant' && !(last.content || '').trim()
}
