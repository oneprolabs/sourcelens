/** Keep access checks isolated by connection and URL, including late replies. */
export function createFeishuResourceValidation(onChange, delay = 350) {
  let connection = ''
  let urls = []
  const entries = new Map()

  function publish(duplicateMessage = 'Duplicate resource address.') {
    const seen = new Set()
    const resources = urls.map((url) => {
      const duplicate = url && seen.has(url)
      seen.add(url)
      return {
        url,
        status: duplicate ? 'failed' : entries.get(url)?.status || '',
        message: duplicate
          ? duplicateMessage
          : entries.get(url)?.message || ''
      }
    })
    onChange({
      status:
        resources.length && resources.every((item) => item.status === 'success')
          ? 'success'
          : 'pending',
      details: { connection_uuid: connection, resources }
    })
  }

  function reset() {
    for (const entry of entries.values()) clearTimeout(entry.timer)
    entries.clear()
    connection = ''
    urls = []
  }

  function update(nextConnection, values, validate, duplicateMessage) {
    if (connection !== nextConnection) reset()
    connection = nextConnection
    urls = (values || []).map((value) => String(value || '').trim())
    for (const [url, entry] of entries) {
      if (!urls.includes(url)) {
        clearTimeout(entry.timer)
        entries.delete(url)
      }
    }
    for (const url of urls) {
      if (!connection || !url || entries.has(url)) continue
      const entry = { status: 'checking', message: '', timer: null }
      entries.set(url, entry)
      entry.timer = setTimeout(async () => {
        try {
          entry.message = await validate(url)
          entry.status = 'success'
        } catch (error) {
          entry.message = error.message
          entry.status = 'failed'
        }
        if (entries.get(url) === entry) publish(duplicateMessage)
      }, delay)
    }
    publish(duplicateMessage)
  }

  return { update, reset }
}
