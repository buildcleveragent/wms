import { defineStore } from 'pinia'

import { normalizeSelectedCustomer } from '@/utils/customer'
import { createIdempotencyKey } from '@/utils/idempotency'
import { validateCartQuantity } from '@/utils/quantity'
import { orderAmount } from '@/utils/orderAmount'

function defaultOrderHeader() {
  return {
    src_bill_no: '',
    contact: '',
    contact_phone: '',
    ship_to: '',
    delivery_method: null,
    etd: null,
    remark: '',
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
    reorder_source_id: null,
    historical_address: false,
    default_address_loaded: false,
    pending_submission: null,
    order_header: defaultOrderHeader(),
    customer: null,
    items: [],
  }),

  getters: {
    totalQty: state => state.items.reduce((total, item) => total + Number(item.qty || 0), 0),
    totalAmount: state => orderAmount(state.items),
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
      this.reorder_source_id = null
      this.historical_address = false
      this.default_address_loaded = false
      this.pending_submission = null
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
      this.reorder_source_id = null
      this.historical_address = false
      this.default_address_loaded = true
      this.pending_submission = null
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
      this.items = (context.items || []).map((item, index) => ({
        ...item,
        line_key: item.line_key || `edit-${index}`,
        product_id: item.product_id || item.id,
        qty: Number(item.qty || 0),
        price: item.is_priced === false ? null : Number(item.price || 0),
        available: item.available == null ? null : Number(item.available),
      }))
      return true
    },

    beginReorder({ user_id, owner_id, context }) {
      if (!context?.can_copy || !context?.source_order_id || !context?.customer) return false
      this.beginOrder({ user_id, owner_id, warehouse: context.warehouse })
      this.customer = normalizeSelectedCustomer(context.customer)
      this.reorder_source_id = context.source_order_id
      this.historical_address = true
      this.default_address_loaded = true
      Object.assign(this.order_header, context.header)
      this.items = context.items.map((item, index) => ({
        ...item,
        line_key: item.line_key || `copy-${index}`,
        qty: String(item.qty),
        price: item.price == null ? '' : String(item.price),
        source_line_id: item.source_line_id,
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
      this.reorder_source_id = null
      this.historical_address = false
      this.default_address_loaded = false
      this.pending_submission = null
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
      this.reorder_source_id = null
      this.historical_address = false
      this.default_address_loaded = false
      this.pending_submission = null
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
        this.reorder_source_id = null
        this.historical_address = false
        this.default_address_loaded = false
        this.pending_submission = null
      }
      this.customer = customer
      return true
    },

    addItem(product) {
      const quantity = validateCartQuantity(product?.qty, product?.available)
      if (!product?.id || !quantity.valid) return false

      const existingIndex = this.items.findIndex(item => item.product_id === product.id && !item.source_line_id)
      const existingTotal = this.items.filter(item => item.product_id === product.id)
        .reduce((sum, item) => sum + Number(item.qty || 0), 0)
      const totalQuantity = Number((existingTotal + quantity.value).toFixed(4))
      if (!validateCartQuantity(totalQuantity, product.available).valid) return false
      if (existingIndex >= 0 && !this.items[existingIndex].source_line_id) {
        return this.setQty(
          existingIndex,
          Number((Number(this.items[existingIndex].qty) + quantity.value).toFixed(4)),
        )
      }

      this.items.push({
        line_key: createIdempotencyKey(),
        product_id: product.id,
        base_uom_id: product.base_uom_id || null,
        catalog_version: product.catalog_version || '',
        sku: product.sku,
        name: product.name,
        spec: product.spec,
        is_priced: product.is_priced !== false,
        price: product.is_priced === false ? null : Number(product.price || 0),
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
      const quantity = validateCartQuantity(rawQuantity, this.remainingFor(index))
      if (!quantity.valid) return false
      item.qty = quantity.value
      return true
    },

    remainingFor(index) {
      const item = this.items[index]
      if (item?.available == null) return null
      if (this.items.some(row => row.product_id === item.product_id &&
          (row.unit_unverified || row.unit_mismatch))) return null
      const others = this.items.reduce((sum, row, i) => (
        i !== index && row.product_id === item.product_id ? sum + Number(row.qty || 0) : sum
      ), 0)
      return Math.max(0, Number((Number(item.available) - others).toFixed(4)))
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
