export function normalizeCustomerCode(value) {
  return String(value || '').trim().toUpperCase()
}

export function normalizeSelectedCustomer(customer) {
  const id = customer?.id
  const code = normalizeCustomerCode(customer?.code)
  if (!id || !code) return null

  return {
    id,
    code,
    name: String(customer?.name || '').trim(),
  }
}

export function isCashCustomer(customer) {
  return normalizeCustomerCode(customer?.code) === 'CASH'
}
