const LIMIT = 100

export function parseInvitationText(text, defaults = {}) {
  return text
    .split(/[\s,;，；]+/u)
    .filter(Boolean)
    .map((email) => ({
      email: email.trim().toLowerCase(),
      role: defaults.role || 'member',
      group_ids: [...(defaults.group_ids || [])]
    }))
}

export function classifyInvitationRows(rows) {
  const seen = new Set()
  return rows.map((row, index) => {
    const valid =
      /^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(row.email) && row.email.length <= 254
    const status = !valid
      ? 'invalid'
      : seen.has(row.email)
        ? 'duplicate'
        : 'valid'
    seen.add(row.email)
    return {
      index,
      email: row.email,
      status,
      reason:
        status === 'invalid'
          ? 'invalid_email'
          : status === 'duplicate'
            ? 'duplicate'
            : ''
    }
  })
}

function csvCells(text) {
  const rows = []
  let row = [],
    cell = '',
    quoted = false,
    closed = false
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        cell += '"'
        i++
      } else if (char === '"') {
        quoted = false
        closed = true
      } else cell += char
    } else if (char === '"') {
      if (cell || closed) throw new Error('invalidCsv')
      quoted = true
    } else if (char === ',' || char === '\r' || char === '\n') {
      row.push(cell.trim())
      cell = ''
      closed = false
      if (char !== ',') {
        if (row.some(Boolean)) rows.push(row)
        row = []
        if (char === '\r' && text[i + 1] === '\n') i++
      }
    } else {
      if (closed && char.trim()) throw new Error('invalidCsv')
      if (!closed) cell += char
    }
    if (rows.length > LIMIT + 1) throw new Error('tooMany')
  }
  if (quoted) throw new Error('invalidCsv')
  row.push(cell.trim())
  if (row.some(Boolean)) rows.push(row)
  return rows
}

export function parseInvitationCsv(text, groups = [], defaults = {}) {
  const cells = csvCells(text.replace(/^\uFEFF/u, ''))
  const header = cells.shift()?.map((value) => value.toLowerCase())
  const headers = [
    ['email', 'role', 'groups'],
    ['邮箱', '角色', '用户组']
  ]
  if (
    !headers.some(
      (expected) =>
        expected.every((value, index) => value === header?.[index]) &&
        header.length === 3
    )
  ) {
    throw new Error('invalidCsv')
  }
  if (!cells.length) throw new Error('emptyCsv')
  if (cells.length > LIMIT) throw new Error('tooMany')
  const roles = {
    普通用户: 'member',
    租户管理员: 'tenant_admin',
    member: 'member',
    tenant_admin: 'tenant_admin'
  }
  return cells.map((row) => {
    if (row.length !== 3) throw new Error('invalidCsv')
    return {
      email: row[0].trim().toLowerCase(),
      role: row[1] ? roles[row[1]] || row[1] : defaults.role || 'member',
      group_ids: row[2]
        ? row[2]
            .split('|')
            .map(
              (name) =>
                groups.find((group) => group.name === name.trim())?.id || 0
            )
        : [...(defaults.group_ids || [])]
    }
  })
}

export function invitationCsvTemplate() {
  return '\uFEFFemail,role,groups\r\nmember@example.com,member,\r\nadmin@example.com,tenant_admin,\r\n'
}
