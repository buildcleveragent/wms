export const DEFAULT_CONFIRM_DELAYS = Object.freeze([1000, 2000, 3000, 5000])

function defaultSleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function shouldRetryConfirmation(result) {
  if (!result || result.confirmed) return false
  if (result.retryable) return true
  return ['USERPAYING', 'NOTPAY', ''].includes(result.trade_state || '')
}

export function createPaymentService({ api, requestPayment, sleep = defaultSleep }) {
  if (!api || typeof requestPayment !== 'function') {
    throw new TypeError('payment service dependencies are incomplete')
  }

  const query = (orderId) => api.querySaleMiniWechat({ order_id: orderId })

  async function confirmPayment(orderId, delays = DEFAULT_CONFIRM_DELAYS) {
    let result = await query(orderId)
    if (result.confirmed) return result

    for (const delay of delays) {
      if (!shouldRetryConfirmation(result)) break
      await sleep(delay)
      result = await query(orderId)
      if (result.confirmed) return result
    }
    return { ...result, confirmed: false, pending: true }
  }

  async function payAndConfirm(orderId) {
    const prepay = await api.prepaySaleMiniWechat({ order_id: orderId })
    if (prepay.paid) {
      return {
        confirmed: true,
        pending: false,
        payment_status: 'PAID',
        payment: prepay.payment,
        order: prepay.order,
        settlement_channel: prepay.settlement_channel,
      }
    }
    if (!prepay.pay_params) {
      throw new Error('微信预支付参数缺失')
    }

    try {
      await requestPayment(prepay.pay_params)
    } catch (error) {
      const paymentError = error instanceof Error ? error : new Error(String(error || '支付未完成'))
      try {
        const confirmation = await query(orderId)
        if (confirmation.confirmed) return confirmation
        paymentError.paymentConfirmation = confirmation
      } catch (queryError) {
        paymentError.paymentQueryError = queryError
      }
      throw paymentError
    }

    try {
      return await confirmPayment(orderId)
    } catch (error) {
      return {
        confirmed: false,
        pending: true,
        payment_status: 'UNPAID',
        trade_state: '',
        query_error: error.message || '支付结果暂未确认',
      }
    }
  }

  return {
    prepay: (orderId) => api.prepaySaleMiniWechat({ order_id: orderId }),
    query,
    confirmPayment,
    payAndConfirm,
    refund: (orderId, reason = '') => api.refundSaleMiniWechat({ order_id: orderId, reason }),
    requestPayment,
  }
}
