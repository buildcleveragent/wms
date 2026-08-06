export const CHECKOUT_STATUS = Object.freeze({
  CHECKOUT_FAILED: 'checkout_failed',
  COMPLETED: 'completed',
  PRINT_FAILED: 'print_failed',
  COMMITTED_UI_FAILED: 'committed_ui_failed',
})

export async function executeCheckoutFlow({ submit, commit, print, shouldPrint = true }) {
  let response
  try {
    response = await submit()
  } catch (error) {
    return { status: CHECKOUT_STATUS.CHECKOUT_FAILED, error }
  }

  try {
    await commit(response)
  } catch (error) {
    return { status: CHECKOUT_STATUS.COMMITTED_UI_FAILED, response, error }
  }

  if (!shouldPrint) return { status: CHECKOUT_STATUS.COMPLETED, response }

  try {
    const printed = await print(response)
    if (printed === false) {
      return { status: CHECKOUT_STATUS.PRINT_FAILED, response }
    }
  } catch (error) {
    return { status: CHECKOUT_STATUS.PRINT_FAILED, response, error }
  }
  return { status: CHECKOUT_STATUS.COMPLETED, response }
}

export async function executePrintAttempt(print) {
  try {
    const printed = await print()
    return printed === false
      ? { status: CHECKOUT_STATUS.PRINT_FAILED }
      : { status: CHECKOUT_STATUS.COMPLETED }
  } catch (error) {
    return { status: CHECKOUT_STATUS.PRINT_FAILED, error }
  }
}
