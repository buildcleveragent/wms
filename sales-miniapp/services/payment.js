import { api } from '../utils/request'
import { createPaymentService } from './payment-core.mjs'

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

export const paymentService = createPaymentService({ api, requestPayment })
