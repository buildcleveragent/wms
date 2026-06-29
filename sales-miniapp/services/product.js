import { api } from '../utils/request'

export const productService = {
  home: (params) => api.saleMiniHome(params),
  merchants: () => api.saleMiniMerchants(),
  categories: (params) => api.saleMiniCategories(params),
  brands: (params) => api.saleMiniBrands(params),
  list: (params) => api.saleMiniProducts(params),
  detail: (id, params) => api.saleMiniProduct(id, params),
}
