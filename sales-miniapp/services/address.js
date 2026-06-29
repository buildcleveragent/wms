import { api } from '../utils/request'

export const addressService = {
  list: (params) => api.saleMiniAddresses(params),
  create: (payload) => api.createSaleMiniAddress(payload),
  update: (id, payload) => api.updateSaleMiniAddress(id, payload),
  remove: (id) => api.deleteSaleMiniAddress(id),
}
