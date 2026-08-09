import { defineStore } from 'pinia'

import { normalizeSelectedCustomer } from '@/utils/customer'
import { createIdempotencyKey } from '@/utils/idempotency'
import { validateCartQuantity } from '@/utils/quantity'

function defaultOrderHeader() {
  return {
    src_bill_no: '',
    contact: '',
    contact_phone: '',
    ship_to: '',
    delivery_method: null,
    etd: null,
    remark: '业务员下单',
  }
}

function clearCustomerHeader(header) {
  Object.assign(header, {
    src_bill_no: '',
    contact: '',
    contact_phone: '',
    ship_to: '',
  })
}

export const useCart = defineStore('cart', {
  state: () => ({
    user_id: null,
    owner_id: null,
    warehouse_id: null,
    warehouse_name: '',
    idempotency_key: null,
    editing_order_id: null,
    editing_updated_at: null,
    owner_reject_reason: '',
    order_header: defaultOrderHeader(),
    customer: null,
    items: [],
  }),

  getters: {
    totalQty: state => state.items.reduce((total, item) => total + Number(item.qty || 0), 0),
    totalAmount: state => state.items.reduce(
      (total, item) => total + Number(item.qty || 0) * Number(item.price || 0),
      0,
    ),
    hasContextForUser: state => (userId, ownerId) => Boolean(
      state.warehouse_id &&
      state.user_id &&
      state.owner_id &&
      String(state.user_id) === String(userId || '') &&
      String(state.owner_id) === String(ownerId || '')
    ),
  },

  actions: {
    beginOrder({ user_id, owner_id, warehouse }) {
      this.user_id = user_id || null
      this.owner_id = owner_id || null
      this.warehouse_id = warehouse?.id || null
      this.warehouse_name = warehouse?.name || ''
      this.idempotency_key = createIdempotencyKey()
      this.editing_order_id = null
      this.editing_updated_at = null
      this.owner_reject_reason = ''
      Object.assign(this.order_header, defaultOrderHeader())
      this.customer = null
      this.items = []
    },

    beginEdit({ user_id, owner_id, context }) {
      const customer = normalizeSelectedCustomer(context?.customer)
      if (!context?.id || !context?.warehouse?.id || !customer) return false

      this.user_id = user_id || null
      this.owner_id = owner_id || null
      this.warehouse_id = context.warehouse.id
      this.warehouse_name = context.warehouse.name || ''
      this.idempotency_key = null
      this.editing_order_id = context.id
      this.editing_updated_at = context.updated_at || null
      this.owner_reject_reason = context.owner_reject_reason || ''
      this.customer = customer
      Object.assign(this.order_header, {
        src_bill_no: context.header?.src_bill_no || '',
        contact: context.header?.contact || '',
        contact_phone: context.header?.contact_phone || '',
        ship_to: context.header?.ship_to || '',
        delivery_method: context.header?.delivery_method || null,
        etd: context.header?.etd || null,
        remark: context.header?.remark || '',
      })
      this.items = (context.items || []).map(item => ({
        ...item,
        product_id: item.product_id || item.id,
        qty: Number(item.qty || 0),
        price: Number(item.price || 0),
        available: item.available == null ? null : Number(item.available),
      }))
      return true
    },

    changeWarehouseForEdit(warehouse) {
      if (!this.editing_order_id) return false
      return this.changeWarehouse(warehouse)
    },

    changeWarehouse(warehouse) {
      if (!warehouse?.id || !this.user_id || !this.owner_id) return false
      if (String(this.warehouse_id) === String(warehouse.id)) return true

      this.warehouse_id = warehouse.id
      this.warehouse_name = warehouse.name || ''
      this.customer = null
      this.items = []
      this.idempotency_key = createIdempotencyKey()
      Object.assign(this.order_header, defaultOrderHeader())
      return true
    },

    resetOrder() {
      this.user_id = null
      this.owner_id = null
      this.warehouse_id = null
      this.warehouse_name = ''
      this.idempotency_key = null
      this.editing_order_id = null
      this.editing_updated_at = null
      this.owner_reject_reason = ''
      Object.assign(this.order_header, defaultOrderHeader())
      this.customer = null
      this.items = []
    },

    ensureIdempotencyKey() {
      if (!this.idempotency_key) this.idempotency_key = createIdempotencyKey()
      return this.idempotency_key
    },

    setCustomer(record) {
      const customer = normalizeSelectedCustomer(record)
      if (!customer) return false

      const previousCustomerId = this.customer?.id
      if (previousCustomerId && String(previousCustomerId) !== String(customer.id)) {
        clearCustomerHeader(this.order_header)
        this.items = []
        this.idempotency_key = createIdempotencyKey()
      }
      this.customer = customer
      return true
    },

    addItem(product) {
      const quantity = validateCartQuantity(product?.qty, product?.available)
      if (!product?.id || !quantity.valid) return false

      const existingIndex = this.items.findIndex(item => item.product_id === product.id)
      if (existingIndex >= 0) {
        return this.setQty(
          existingIndex,
          Number(this.items[existingIndex].qty) + quantity.value,
        )
      }

      this.items.push({
        product_id: product.id,
        sku: product.sku,
        name: product.name,
        spec: product.spec,
        price: Number(product.price || 0),
        orig_price: Number(product.orig_price ?? product.price ?? 0),
        minimum_sale_price: product.minimum_sale_price == null
          ? null
          : String(product.minimum_sale_price),
        min_price: product.min_price == null ? null : Number(product.min_price),
        qty: quantity.value,
        product_image_url: product.product_image_url,
        gtin: product.gtin,
        aux_uom_name: product.aux_uom_name,
        base_unit_name: product.base_unit_name,
        aux_qty_in_base: product.aux_qty_in_base,
        product_min_price: product.product_min_price == null
          ? null
          : Number(product.product_min_price),
        max_discount: product.max_discount == null ? null : Number(product.max_discount),
        available: product.available == null ? null : Number(product.available),
        unitOptions: product.unitOptions,
        selectedUnitIndex: product.selectedUnitIndex ?? 0,
      })
      return true
    },

    setEditingUpdatedAt(value) {
      if (!this.editing_order_id) return false
      this.editing_updated_at = value || null
      return true
    },

    setQty(index, rawQuantity) {
      const item = this.items[index]
      if (!item) return false
      const quantity = validateCartQuantity(rawQuantity, item.available)
      if (!quantity.valid) return false
      item.qty = quantity.value
      return true
    },

    remove(index) {
      if (!this.items[index]) return false
      this.items.splice(index, 1)
      return true
    },

    clear() {
      this.resetOrder()
    },
  },
})
