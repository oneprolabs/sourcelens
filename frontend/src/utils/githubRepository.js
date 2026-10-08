export function normalizeGitHubRepositoryAddress(value) {
  const address = String(value || '').trim()
  const match = address.match(
    /^https:\/\/github\.com\/([^/?#]+\/[^/?#]+?)\/?$/i
  )
  return match ? match[1].replace(/\.git$/, '') : address
}
