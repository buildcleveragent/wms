import { reactive } from 'vue'
import { cartService } from '../services/cart'

function toNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function itemFromServer(line) {
  return {
    cart_id: line.cart_id,
    item_id: line.item_id,
    key: line.key || `${line.cart_id || ''}:${line.product_id}:${line.order_uom}`,
    owner: line.owner || null,
    owner_id: line.owner_id,
    config_id: line.config_id,
    product_id: line.product_id,
    code: line.product_code,
    name: line.product_name,
    spec: line.product_spec,
    image_url: line.image_url,
    order_uom: line.order_uom,
    qty: toNumber(line.qty),
    qty_in_base: line.qty_in_base,
    base_qty: line.base_qty,
    base_uom: line.base_uom,
    unit_price: line.unit_price,
    base_unit_price: line.base_unit_price,
    line_amount: line.line_amount,
    available_qty: line.available_qty,
    quote_ok: line.ok,
    quote_message: line.message,
  }
}

function groupFromServer(group) {
  const items = (group.items || group.lines || []).map(itemFromServer)
  return {
    cart_id: group.cart_id || group.id || null,
    owner: group.owner || null,
    owner_id: group.owner_id,
    ok: group.ok !== false,
    total_amount: group.total_amount || 0,
    goods_amount: group.goods_amount || 0,
    payable_amount: group.payable_amount || group.total_amount || 0,
    line_count: group.line_count || items.length,
    items,
  }
}

const cartStore = reactive({
  cartId: uni.getStorageSync('sale_mini_cart_id') || null,
  items: uni.getStorageSync('sale_mini_cart_items') || [],
  groups: uni.getStorageSync('sale_mini_cart_groups') || [],
  lastPreview: null,
  synced: false,
  get totalQty() {
    return this.items.reduce((sum, item) => sum + toNumber(item.qty), 0)
  },
  get totalAmount() {
    return this.items.reduce(
      (sum, item) => sum + toNumber(item.line_amount || toNumber(item.qty) * toNumber(item.unit_price)),
      0,
    )
  },
  persist() {
    uni.setStorageSync('sale_mini_cart_id', this.cartId || '')
    uni.setStorageSync('sale_mini_cart_items', this.items)
    uni.setStorageSync('sale_mini_cart_groups', this.groups)
  },
  applyServerCart(data) {
    const hasGroups = data.groups && data.groups.length
    const groups = hasGroups
      ? data.groups.map(groupFromServer)
      : ((data.items && data.items.length) || (data.lines && data.lines.length) ? [groupFromServer(data)] : [])
    this.groups = groups
    this.items = groups.reduce((all, group) => all.concat(group.items), [])
    this.cartId = data.id || data.cart_id || (groups.length === 1 ? groups[0].cart_id : null)
    this.lastPreview = data
    this.synced = true
    this.persist()
  },
  clearLocal() {
    this.items = []
    this.groups = []
    this.lastPreview = null
    this.synced = true
    this.persist()
  },
  async load(params = {}) {
    const data = await cartService.get(params)
    this.applyServerCart(data)
    return data
  },
  async addProduct(product, qty = 1) {
    const amount = Math.max(toNumber(qty), 0)
    if (!amount) return this.lastPreview
    const data = await cartService.add({
      config_id: product.config_id,
      product_id: product.id,
      qty: String(amount),
      order_uom: product.order_uom,
    })
    await this.load()
    return data
  },
  findIndexForItem(target) {
    return this.items.findIndex((item) => {
      if (target.item_id && item.item_id) return Number(item.item_id) === Number(target.item_id)
      return item.key === target.key
    })
  },
  async setItemQty(item, qty) {
    const index = this.findIndexForItem(item)
    return this.setQty(index, qty)
  },
  async setQty(index, qty) {
    const item = this.items[index]
    if (!item) return this.lastPreview
    const nextQty = Math.max(toNumber(qty), 0)
    const payload = item.item_id
      ? { item_id: item.item_id, owner_id: item.owner_id, qty: String(nextQty) }
      : {
          owner_id: item.owner_id,
          product_id: item.product_id,
          order_uom: item.order_uom,
          qty: String(nextQty),
        }
    const data = await cartService.update(payload)
    await this.load()
    return data
  },
  async removeItem(item) {
    const index = this.findIndexForItem(item)
    return this.remove(index)
  },
  async remove(index) {
    const item = this.items[index]
    if (!item) return this.lastPreview
    const payload = item.item_id
      ? { item_id: item.item_id, owner_id: item.owner_id }
      : { owner_id: item.owner_id, product_id: item.product_id, order_uom: item.order_uom }
    const data = await cartService.remove(payload)
    await this.load()
    return data
  },
  async clear() {
    const data = await cartService.clear()
    this.applyServerCart(data)
    return data
  },
  payload(extra = {}) {
    const ownerId = extra.owner_id
    const lines = this.items
      .filter((item) => !ownerId || Number(item.owner_id) === Number(ownerId))
      .map((item) => ({
        product_id: item.product_id,
        qty: String(item.qty),
        order_uom: item.order_uom,
      }))
    const group = ownerId
      ? this.groups.find((item) => Number(item.owner_id) === Number(ownerId))
      : null
    const cartIds = ownerId
      ? []
      : this.groups.map((item) => item.cart_id).filter(Boolean)
    return {
      ...extra,
      ...(group && group.cart_id ? { cart_id: group.cart_id } : this.cartId ? { cart_id: this.cartId } : {}),
      ...(!ownerId && cartIds.length ? { cart_ids: cartIds } : {}),
      lines,
    }
  },
  async preview(extra = {}) {
    const payload = this.payload(extra)
    if (!payload.lines.length) throw new Error('购物车为空')
    const preview = await cartService.preview(payload)
    this.items = this.items.map((item) => {
      const line = preview.lines.find((row) => row.product_id === item.product_id && row.order_uom === item.order_uom)
      if (!line) return item
      return {
        ...item,
        qty: toNumber(line.qty),
        qty_in_base: line.qty_in_base,
        base_qty: line.base_qty,
        base_uom: line.base_uom,
        unit_price: line.unit_price,
        base_unit_price: line.base_unit_price,
        line_amount: line.line_amount,
        available_qty: line.available_qty,
        quote_ok: line.ok,
        quote_message: line.message,
      }
    })
    this.groups = this.groups.map((group) => ({
      ...group,
      items: group.items.map((item) => this.items.find((row) => {
        if (item.item_id && row.item_id) return Number(item.item_id) === Number(row.item_id)
        return item.key === row.key
      }) || item),
    }))
    this.lastPreview = preview
    this.persist()
    return preview
  },
  async checkout(extra = {}) {
    const preview = await this.preview(extra)
    if (!preview.ok) {
      const failed = preview.lines.find((line) => !line.ok)
      throw new Error((failed && failed.message) || '订单校验未通过')
    }
    const order = await cartService.checkout(this.payload(extra))
    await this.load().catch(() => this.clearLocal())
    return order
  },
})

export function useCartStore() {
  return cartStore
}
