import { orderService } from './order'
import { api } from '../utils/request'

export const cartService = {
  get: (params = {}) => api.saleMiniCart(params),
  add: (payload) => api.addSaleMiniCart(payload),
  update: (payload) => api.updateSaleMiniCart(payload),
  remove: (payload) => api.removeSaleMiniCart(payload),
  clear: (payload = {}) => api.clearSaleMiniCart(payload),
  preview: (payload) => orderService.preview(payload),
  checkout: (payload) => orderService.create(payload),
}
