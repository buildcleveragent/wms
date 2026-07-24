import { api } from '../utils/request'

function requestPayment(payParams) {
  return new Promise((resolve, reject) => {
    uni.requestPayment({
      provider: 'wxpay',
      timeStamp: payParams.timeStamp,
      nonceStr: payParams.nonceStr,
      package: payParams.package,
      signType: payParams.signType || 'RSA',
      paySign: payParams.paySign,
      success: resolve,
      fail: reject,
    })
  })
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function query(orderId) {
  return api.querySaleMiniWechat({ order_id: orderId })
}

async function confirmPayment(orderId, delays = [1000, 2000, 3000, 5000]) {
  let result = await query(orderId)
  if (result.confirmed) return result
  for (const delay of delays) {
    await sleep(delay)
    result = await query(orderId)
    if (result.confirmed) return result
    if (!result.retryable && !['USERPAYING', 'NOTPAY', ''].includes(result.trade_state || '')) {
      break
    }
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

  try {
    await requestPayment(prepay.pay_params)
  } catch (error) {
    try {
      const confirmation = await query(orderId)
      if (confirmation.confirmed) return confirmation
      error.paymentConfirmation = confirmation
    } catch (queryError) {
      error.paymentQueryError = queryError
    }
    throw error
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

export const paymentService = {
  prepay: (orderId) => api.prepaySaleMiniWechat({ order_id: orderId }),
  query,
  payAndConfirm,
  refund: (orderId, reason = '') => api.refundSaleMiniWechat({ order_id: orderId, reason }),
  requestPayment,
}
