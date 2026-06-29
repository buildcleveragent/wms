import { api } from '../utils/request'

export const benefitService = {
  coupons: (params = {}) => api.saleMiniCoupons(params),
  points: (params = {}) => api.saleMiniPoints(params),
}
