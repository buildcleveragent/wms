import { defineStore } from 'pinia'

const PRINT_PREFERENCE_PREFIX = 'assisted_outbound_print_after_create_v1'

function emptyForm() {
  return {
    src_bill_no: '',
    delivery_method: '',
    etd: '',
    contact: '',
    contact_phone: '',
    ship_to: '',
    remark: '',
    assistance_reason: '',
    print_after_create: false,
  }
}

function optionalPrice(value) {
  if (value === '' || value === null || value === undefined) return ''
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? String(value) : ''
}

function quantity(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0
    ? Number(number.toFixed(3))
    : 0
}

function normalizeUnit(product, unit) {
  const multiplier = Number(unit?.multiplier || 1)
  return {
    key: unit?.key ?? unit?.package_id ?? 'BASE',
    kind: unit?.kind || (unit?.package_id == null ? 'base' : 'package'),
    package_id: unit?.package_id ?? null,
    label: unit?.label || product.base_unit_name || product.base_unit || '基本单位',
    multiplier: Number.isFinite(multiplier) && multiplier > 0 ? multiplier : 1,
    barcode: unit?.barcode || '',
    is_base: unit?.is_base === true || unit?.package_id == null,
  }
}

function normalizeUnitOptions(product) {
  const rawOptions = Array.isArray(product?.unitOptions) && product.unitOptions.length
    ? product.unitOptions
    : (Array.isArray(product?.unit_options) && product.unit_options.length
      ? product.unit_options
      : [
        {
          key: 'BASE',
          kind: 'base',
          package_id: null,
          label: product?.base_unit_name || product?.base_unit || '基本单位',
          multiplier: 1,
          barcode: '',
          is_base: true,
        },
      ])
  return rawOptions.map((unit) => normalizeUnit(product, unit))
}

function maxPackageQuantity(availableQty, multiplier) {
  const available = Number(availableQty || 0)
  const unitMultiplier = Number(multiplier || 0)
  if (!Number.isFinite(available) || available <= 0) return 0
  if (!Number.isFinite(unitMultiplier) || unitMultiplier <= 0) return 0
  return Math.floor((available / unitMultiplier) * 1000) / 1000
}

function printPreferenceKey(userId, warehouseId) {
  const normalizedUserId = Number(userId)
  const normalizedWarehouseId = Number(warehouseId)
  if (!normalizedUserId || !normalizedWarehouseId) return ''
  return `${PRINT_PREFERENCE_PREFIX}:${normalizedUserId}:${normalizedWarehouseId}`
}

export const useAssistedOutbound = defineStore('assistedOutbound', {
  state: () => ({
    owner: null,
    customer: null,
    items: [],
    form: emptyForm(),
    lastRequestId: '',
    lastRequestSignature: '',
    submissionState: 'idle',
  }),

  getters: {
    itemCount: (state) => state.items.length,
    totalQty: (state) => state.items.reduce(
      (total, item) => total + Number(item.qty || 0),
      0,
    ),
    submissionLocked: (state) => ['confirming', 'submitting'].includes(
      state.submissionState,
    ),
  },

  actions: {
    clearRequestIdentity() {
      this.lastRequestId = ''
      this.lastRequestSignature = ''
    },

    clearOrderDetails() {
      const printAfterCreate = Boolean(this.form?.print_after_create)
      this.items = []
      this.form = emptyForm()
      this.form.print_after_create = printAfterCreate
      this.submissionState = 'idle'
      this.clearRequestIdentity()
    },

    loadPrintPreference(userId, warehouseId) {
      const key = printPreferenceKey(userId, warehouseId)
      if (!key) return false
      let checked = false
      try {
        checked = uni.getStorageSync(key) === '1'
      } catch (error) {
        console.warn('读取代办出库打印偏好失败', error)
      }
      this.form.print_after_create = checked
      return checked
    },

    setPrintAfterCreate(checked, userId, warehouseId) {
      const value = Boolean(checked)
      this.form.print_after_create = value
      const key = printPreferenceKey(userId, warehouseId)
      if (!key) return
      try {
        uni.setStorageSync(key, value ? '1' : '0')
      } catch (error) {
        console.warn('保存代办出库打印偏好失败', error)
      }
    },

    setOwner(owner) {
      const changed = Number(this.owner?.id || 0) !== Number(owner?.id || 0)
      this.owner = owner || null
      if (changed) {
        this.customer = null
        this.clearOrderDetails()
      }
    },

    setCustomer(customer) {
      const changed = Number(this.customer?.id || 0) !== Number(customer?.id || 0)
      this.customer = customer || null
      if (changed) this.clearOrderDetails()
    },

    addItem(product, baseQty, price, selectedUnit = null, selectedQty = null) {
      const productId = Number(product?.id)
      const addBaseQty = quantity(baseQty)
      const addSelectedQty = quantity(selectedQty)
      if (!productId || !addBaseQty || !addSelectedQty) {
        return { ok: false, reason: 'invalid_quantity' }
      }

      const unit = normalizeUnit(product, selectedUnit)

      const existing = this.items.find((item) => item.product_id === productId)
      if (existing) {
        if ((existing.package_id ?? null) !== unit.package_id) {
          return {
            ok: false,
            reason: 'unit_mismatch',
            existing_label: existing.unit_label,
          }
        }
        existing.qty = quantity(Number(existing.qty || 0) + addBaseQty)
        existing.package_qty = quantity(
          Number(existing.package_qty || 0) + addSelectedQty,
        )
        if (price !== undefined) existing.price = optionalPrice(price)
        this.clearRequestIdentity()
        return { ok: true, item: existing }
      }

      const item = {
        product_id: productId,
        sku: product.sku || '',
        name: product.name || product.sku || `商品 ${productId}`,
        spec: product.spec || '',
        base_unit: product.base_unit || '',
        base_unit_name: product.base_unit_name || product.base_unit || '',
        available_qty: Number(product.available_qty ?? product.available ?? 0),
        price: optionalPrice(
          price !== undefined ? price : (product.default_price ?? product.price),
        ),
        qty: addBaseQty,
        package_id: unit.package_id,
        package_qty: addSelectedQty,
        unit_label: unit.label,
        unit_multiplier: unit.multiplier,
        unit_options: normalizeUnitOptions(product),
      }
      this.items.push(item)
      this.clearRequestIdentity()
      return { ok: true, item }
    },

    selectItem(product, price, selectedUnit = null, selectedQty = null) {
      const productId = Number(product?.id)
      const packageQty = quantity(selectedQty)
      if (!productId || !packageQty) {
        return { ok: false, reason: 'invalid_quantity' }
      }
      if (this.items.some((item) => item.product_id === productId)) {
        return { ok: false, reason: 'already_selected' }
      }

      const unitOptions = normalizeUnitOptions(product)
      const requestedUnit = normalizeUnit(product, selectedUnit)
      const unit = unitOptions.find(
        (option) => (option.package_id ?? null) === (requestedUnit.package_id ?? null),
      ) || requestedUnit
      const baseQty = quantity(packageQty * unit.multiplier)
      const availableQty = Number(product.available_qty ?? product.available ?? 0)
      if (!baseQty || baseQty > availableQty) {
        return { ok: false, reason: 'insufficient_stock' }
      }

      const item = {
        product_id: productId,
        sku: product.sku || '',
        name: product.name || product.sku || `商品 ${productId}`,
        spec: product.spec || '',
        base_unit: product.base_unit || '',
        base_unit_name: product.base_unit_name || product.base_unit || '',
        available_qty: availableQty,
        price: optionalPrice(
          price !== undefined ? price : (product.default_price ?? product.price),
        ),
        qty: baseQty,
        package_id: unit.package_id,
        package_qty: packageQty,
        unit_label: unit.label,
        unit_multiplier: unit.multiplier,
        unit_options: unitOptions,
      }
      this.items.push(item)
      this.clearRequestIdentity()
      return { ok: true, item }
    },

    findItemIndex(productId) {
      return this.items.findIndex((item) => item.product_id === Number(productId))
    },

    setQty(index, qty) {
      if (!this.items[index]) return
      this.items[index].qty = qty
      this.clearRequestIdentity()
    },

    setPackageQty(index, packageQty) {
      const item = this.items[index]
      if (!item) return
      item.package_qty = packageQty
      this.clearRequestIdentity()
      if (packageQty === '' || packageQty === null || packageQty === undefined) {
        item.qty = ''
        return
      }
      const selectedQty = Number(packageQty)
      const multiplier = Number(item.unit_multiplier || 1)
      item.qty = Number.isFinite(selectedQty) && selectedQty > 0
        ? quantity(selectedQty * multiplier)
        : ''
    },

    finalizePackageQty(index) {
      const item = this.items[index]
      if (!item) return { ok: false, reason: 'missing_item' }
      const enteredQty = Number(item.package_qty)
      const multiplier = Number(item.unit_multiplier || 0)
      if (!Number.isFinite(enteredQty) || enteredQty <= 0) {
        return { ok: false, reason: 'invalid_quantity' }
      }
      if (!Number.isFinite(multiplier) || multiplier <= 0) {
        return { ok: false, reason: 'invalid_multiplier' }
      }

      const maximum = maxPackageQuantity(item.available_qty, multiplier)
      if (maximum <= 0) return { ok: false, reason: 'insufficient_stock' }
      const normalizedQty = quantity(enteredQty)
      const finalQty = normalizedQty > maximum ? maximum : normalizedQty
      item.package_qty = finalQty
      item.qty = quantity(finalQty * multiplier)
      this.clearRequestIdentity()
      return {
        ok: true,
        clamped: finalQty !== normalizedQty,
        package_qty: finalQty,
        qty: item.qty,
      }
    },

    setItemUnit(index, selectedUnit) {
      const item = this.items[index]
      if (!item) return { ok: false, reason: 'missing_item' }
      const options = normalizeUnitOptions(item)
      const requested = normalizeUnit(item, selectedUnit)
      const unit = options.find(
        (option) => (option.package_id ?? null) === (requested.package_id ?? null),
      )
      if (!unit) return { ok: false, reason: 'invalid_unit' }

      const currentPackageQty = Number(item.package_qty)
      if (!Number.isFinite(currentPackageQty) || currentPackageQty <= 0) {
        return { ok: false, reason: 'invalid_quantity' }
      }
      const maximum = maxPackageQuantity(item.available_qty, unit.multiplier)
      if (maximum <= 0) return { ok: false, reason: 'insufficient_stock' }
      const normalizedQty = quantity(currentPackageQty)
      const finalQty = normalizedQty > maximum ? maximum : normalizedQty

      item.unit_options = options
      item.package_id = unit.package_id
      item.unit_label = unit.label
      item.unit_multiplier = unit.multiplier
      item.package_qty = finalQty
      item.qty = quantity(finalQty * unit.multiplier)
      this.clearRequestIdentity()
      return {
        ok: true,
        clamped: finalQty !== normalizedQty,
        package_qty: finalQty,
        qty: item.qty,
      }
    },

    setPrice(index, price) {
      if (!this.items[index]) return
      this.items[index].price = price
      this.clearRequestIdentity()
    },

    finalizePrice(index) {
      const item = this.items[index]
      if (!item) return { ok: false, reason: 'missing_item' }
      if (item.price === '' || item.price === null || item.price === undefined) {
        item.price = ''
        return { ok: true }
      }
      const price = Number(item.price)
      if (!Number.isFinite(price) || price < 0) {
        item.price = ''
        this.clearRequestIdentity()
        return { ok: false, reason: 'invalid_price' }
      }
      item.price = String(item.price)
      this.clearRequestIdentity()
      return { ok: true }
    },

    remove(index) {
      this.items.splice(index, 1)
      this.clearRequestIdentity()
    },

    removeByProductId(productId) {
      const index = this.findItemIndex(productId)
      if (index < 0) return false
      this.remove(index)
      return true
    },

    resetAll() {
      this.owner = null
      this.customer = null
      this.clearOrderDetails()
    },
  },
})
