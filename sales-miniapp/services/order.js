import { api } from '../utils/request'

export const orderService = {
  preview: (payload) => api.previewSaleMiniOrder(payload),
  create: (payload) => api.createSaleMiniOrder(payload),
  list: (params) => api.saleMiniOrders(params),
  detail: (id, params) => api.saleMiniOrder(id, params),
  cancel: (id) => api.cancelSaleMiniOrder(id),
  afterSales: (params = {}) => api.saleMiniAfterSales(params),
  afterSale: (orderId, reason = '') =>
    api.createSaleMiniAfterSale({
      order_id: orderId,
      request_type: 'REFUND',
      reason,
    }),
}
