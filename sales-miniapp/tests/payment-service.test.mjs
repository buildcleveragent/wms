import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_CONFIRM_DELAYS,
  createPaymentService,
} from '../services/payment-core.mjs'

function serviceWith({ prepay, queries = [], requestError = null }) {
  const calls = { prepay: 0, query: 0, request: 0, sleeps: [], refund: [] }
  const api = {
    async prepaySaleMiniWechat(payload) {
      calls.prepay += 1
      calls.prepayPayload = payload
      return prepay
    },
    async querySaleMiniWechat(payload) {
      const index = calls.query
      calls.query += 1
      calls.queryPayload = payload
      const result = queries[Math.min(index, queries.length - 1)]
      if (result instanceof Error) throw result
      return result
    },
    async refundSaleMiniWechat(payload) {
      calls.refund.push(payload)
      return { queued: true }
    },
  }
  const requestPayment = async (params) => {
    calls.request += 1
    calls.requestParams = params
    if (requestError) throw requestError
    return { errMsg: 'requestPayment:ok' }
  }
  const sleep = async (delay) => {
    calls.sleeps.push(delay)
  }
  return { service: createPaymentService({ api, requestPayment, sleep }), calls }
}

test('internal zero settlement never invokes WeChat payment', async () => {
  const { service, calls } = serviceWith({
    prepay: {
      paid: true,
      settlement_channel: 'INTERNAL_ZERO',
      payment: { amount: '0.00' },
      order: { payable_amount: '0.00' },
      pay_params: null,
    },
  })

  const result = await service.payAndConfirm(8)

  assert.equal(result.confirmed, true)
  assert.equal(result.settlement_channel, 'INTERNAL_ZERO')
  assert.equal(calls.request, 0)
  assert.deepEqual(calls.prepayPayload, { order_id: 8 })
})

test('client payment success is confirmed by backend polling', async () => {
  const { service, calls } = serviceWith({
    prepay: { paid: false, pay_params: { package: 'prepay_id=test' } },
    queries: [
      { confirmed: false, retryable: true, trade_state: 'NOTPAY' },
      { confirmed: false, retryable: true, trade_state: 'USERPAYING' },
      { confirmed: true, retryable: false, trade_state: 'SUCCESS' },
    ],
  })

  const result = await service.payAndConfirm(9)

  assert.equal(result.confirmed, true)
  assert.equal(calls.request, 1)
  assert.equal(calls.query, 3)
  assert.deepEqual(calls.sleeps, DEFAULT_CONFIRM_DELAYS.slice(0, 2))
})

test('non-retryable backend result returns pending without needless waits', async () => {
  const { service, calls } = serviceWith({
    prepay: { paid: false, pay_params: { package: 'prepay_id=test' } },
    queries: [{ confirmed: false, retryable: false, trade_state: 'CLOSED' }],
  })

  const result = await service.payAndConfirm(10)

  assert.equal(result.confirmed, false)
  assert.equal(result.pending, true)
  assert.equal(calls.query, 1)
  assert.deepEqual(calls.sleeps, [])
})

test('client cancellation still accepts a backend-confirmed payment', async () => {
  const { service, calls } = serviceWith({
    prepay: { paid: false, pay_params: { package: 'prepay_id=test' } },
    queries: [{ confirmed: true, trade_state: 'SUCCESS' }],
    requestError: new Error('requestPayment:fail cancel'),
  })

  const result = await service.payAndConfirm(11)

  assert.equal(result.confirmed, true)
  assert.equal(calls.query, 1)
})

test('client cancellation retains the backend query result on the error', async () => {
  const requestError = new Error('requestPayment:fail cancel')
  const { service, calls } = serviceWith({
    prepay: { paid: false, pay_params: { package: 'prepay_id=test' } },
    queries: [{ confirmed: false, retryable: true, trade_state: 'NOTPAY' }],
    requestError,
  })

  await assert.rejects(service.payAndConfirm(12), (error) => {
    assert.equal(error, requestError)
    assert.equal(error.paymentConfirmation.trade_state, 'NOTPAY')
    return true
  })
  assert.equal(calls.query, 1)
})

test('query failure after client success is reported as confirmation pending', async () => {
  const { service } = serviceWith({
    prepay: { paid: false, pay_params: { package: 'prepay_id=test' } },
    queries: [new Error('network unavailable')],
  })

  const result = await service.payAndConfirm(13)

  assert.equal(result.confirmed, false)
  assert.equal(result.pending, true)
  assert.equal(result.payment_status, 'UNPAID')
  assert.equal(result.query_error, 'network unavailable')
})

test('missing prepay parameters fail before invoking the client payment API', async () => {
  const { service, calls } = serviceWith({ prepay: { paid: false, pay_params: null } })

  await assert.rejects(service.payAndConfirm(14), /预支付参数缺失/)
  assert.equal(calls.request, 0)
})

test('refund forwards only the authoritative order id and reason', async () => {
  const { service, calls } = serviceWith({ prepay: {} })

  await service.refund(15, '测试退款')

  assert.deepEqual(calls.refund, [{ order_id: 15, reason: '测试退款' }])
})
