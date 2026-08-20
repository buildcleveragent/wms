export function mergeUniqueById(current = [], incoming = [], { replace = true } = {}) {
  const merged = []
  const positions = new Map()
  for (const item of [...current, ...incoming]) {
    const value = item?.id
    if (value === undefined || value === null) {
      merged.push(item)
      continue
    }
    const key = String(value)
    if (!positions.has(key)) {
      positions.set(key, merged.length)
      merged.push(item)
    } else if (replace) {
      merged[positions.get(key)] = item
    }
  }
  return merged
}
