import { reactive } from 'vue'

const FAVORITES_KEY = 'sale_mini_favorite_products'
const RECENT_KEY = 'sale_mini_recent_products'
const MAX_RECENT = 50

function rowsFromStorage(key) {
  const rows = uni.getStorageSync(key)
  return Array.isArray(rows) ? rows : []
}

function productId(product = {}) {
  return product.id || product.product_id || ''
}

function keyFor(product = {}) {
  return [product.config_id || '', productId(product)].join(':')
}

function snapshot(product = {}) {
  const id = productId(product)
  return {
    key: keyFor(product),
    id,
    product_id: id,
    config_id: product.config_id || '',
    name: product.name || product.product_name || '',
    spec: product.spec || product.product_spec || '',
    image_url: product.image_url || '',
    price: product.price || product.unit_price || '',
    market_price: product.market_price || '',
    order_uom: product.order_uom || '',
    stock: product.stock || null,
    badges: product.badges || {},
    rules: product.rules || {},
    saved_at: Date.now(),
  }
}

const browseStore = reactive({
  favorites: rowsFromStorage(FAVORITES_KEY),
  recent: rowsFromStorage(RECENT_KEY),
  persist() {
    uni.setStorageSync(FAVORITES_KEY, this.favorites)
    uni.setStorageSync(RECENT_KEY, this.recent)
  },
  reload() {
    this.favorites = rowsFromStorage(FAVORITES_KEY)
    this.recent = rowsFromStorage(RECENT_KEY)
  },
  isFavorite(product) {
    const key = keyFor(product)
    return this.favorites.some((item) => item.key === key)
  },
  toggleFavorite(product) {
    const key = keyFor(product)
    const index = this.favorites.findIndex((item) => item.key === key)
    if (index >= 0) {
      this.favorites.splice(index, 1)
      this.persist()
      return false
    }
    this.favorites.unshift(snapshot(product))
    this.persist()
    return true
  },
  addRecent(product) {
    if (!productId(product)) return
    const item = snapshot(product)
    this.recent = [item].concat(this.recent.filter((row) => row.key !== item.key)).slice(0, MAX_RECENT)
    this.persist()
  },
  removeFavorite(product) {
    const key = keyFor(product)
    this.favorites = this.favorites.filter((item) => item.key !== key)
    this.persist()
  },
  clearRecent() {
    this.recent = []
    this.persist()
  },
})

export function useBrowseStore() {
  browseStore.reload()
  return browseStore
}
