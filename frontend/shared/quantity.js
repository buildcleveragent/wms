/** Remove decimal padding for display without rounding the supplied quantity. */
export function formatQuantity(value) {
  const text = value == null ? '' : String(value)
  return text.replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1')
}
