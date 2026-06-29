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

export const paymentService = {
  prepay: (orderId) => api.prepaySaleMiniWechat({ order_id: orderId }),
  refund: (orderId, reason = '') => api.refundSaleMiniWechat({ order_id: orderId, reason }),
  requestPayment,
}
