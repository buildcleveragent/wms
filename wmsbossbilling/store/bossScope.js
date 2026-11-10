import { defineStore } from 'pinia'
import { api } from '@/utils/request'
import { validRange } from '@/utils/date-ranges'
import { formatDate } from '@/utils/billing'
import { createLatestRequest } from '@/utils/latest-request'

const contexts = new WeakMap()
const selections = new WeakMap()
function gate(map, store) {
  if (!map.has(store)) map.set(store, createLatestRequest(() => store.userId))
  return map.get(store)
}

function monthRange() {
  const now = new Date()
  return {
    date_from: formatDate(new Date(now.getFullYear(), now.getMonth(), 1)),
    date_to: formatDate(now),
  }
}

function storageKey(userId) {
  return `boss_scope:${userId || 'anonymous'}`
}

function readSaved(userId) {
  try {
    return JSON.parse(uni.getStorageSync(storageKey(userId)) || '{}')
  } catch (error) {
    return {}
  }
}

export const useBossScope = defineStore('bossScope', {
  state: () => ({
    userId: '',
    warehouse: '',
    owner: '',
    ...monthRange(),
    maxRangeDays: null,
    businessToday: '',
    contextFetchedAt: 0,
    hadSavedDates: false,
    preset: 'this_month',
    granularity: 'auto',
    warehouseOptions: [],
    ownerOptions: [],
    loaded: false,
  }),
  getters: {
    params: (state) => ({
      warehouse: state.warehouse || undefined,
      owner: state.owner || undefined,
      date_from: state.date_from,
      date_to: state.date_to,
      granularity: state.granularity,
      preset: state.preset,
    }),
    fingerprint: (state) => [
      state.warehouse || 'ALL', state.owner || 'ALL', state.date_from, state.date_to, state.granularity, state.preset,
    ].join(':'),
  },
  actions: {
    restore(user) {
      const userId = String(user?.id || user?.pk || user?.username || '')
      if (this.userId === userId) return
      gate(contexts, this).invalidate()
      gate(selections, this).invalidate()
      this.userId = userId
      this.loaded = false
      this.warehouseOptions = []
      this.ownerOptions = []
      const saved = readSaved(userId)
      this.hadSavedDates = !!(saved.date_from && saved.date_to)
      Object.assign(this, { preset: 'custom', granularity: 'auto', warehouse: '', owner: '' }, monthRange(), saved)
      // Warehouse bosses start with all owners in their warehouses, ignoring legacy owner filters.
      // Explicit selections remain in memory for navigation and live refreshes.
      this.owner = ''
    },
    persist() {
      try {
        uni.setStorageSync(storageKey(this.userId), JSON.stringify({
          warehouse: this.warehouse,
          date_from: this.date_from,
          date_to: this.date_to,
          granularity: this.granularity,
          preset: this.preset,
        }))
      } catch (error) {}
    },
    async loadContext(user, force = false, warehouse) {
      this.restore(user)
      if (this.loaded && !force && Date.now() - this.contextFetchedAt < 60000) return true
      const current = gate(contexts, this).begin()
      const requestedWarehouse = warehouse === undefined ? this.warehouse : warehouse
      let context
      try {
        context = await api.bossContext({ warehouse: requestedWarehouse || undefined })
      } catch (error) {
        if (!current()) return false
        throw error
      }
      if (!current()) return false
      this.warehouse = requestedWarehouse
      this.warehouseOptions = context?.warehouse_options || []
      this.ownerOptions = context?.owner_options || []
      this.maxRangeDays = null
      this.businessToday = context?.business_today || context?.defaults?.date_to || formatDate(new Date())
      this.contextFetchedAt = Date.now()
      if (!this.hadSavedDates && context?.defaults?.date_from) {
        this.date_from = context.defaults.date_from
        this.date_to = this.businessToday
        this.preset = 'this_month'
      }
      if (this.date_to > this.businessToday) {
        this.date_to = this.businessToday
        if (this.date_from > this.date_to) this.date_from = this.date_to
        this.preset = 'custom'
      }
      const allowedWarehouses = new Set(this.warehouseOptions.map((row) => String(row.id)))
      if (this.warehouse && !allowedWarehouses.has(String(this.warehouse))) this.warehouse = ''
      const allowedOwners = new Set(this.ownerOptions.map((row) => String(row.id)))
      if (this.owner && !allowedOwners.has(String(this.owner))) this.owner = ''
      this.loaded = true
      this.persist()
      return true
    },
    async selectWarehouse(value) {
      const current = gate(selections, this).begin()
      const previous = {
        warehouse: this.warehouse, owner: this.owner, loaded: this.loaded,
        warehouseOptions: this.warehouseOptions, ownerOptions: this.ownerOptions,
        maxRangeDays: this.maxRangeDays,
      }
      const previousOwner = this.owner
      this.loaded = false
      try {
        const applied = await this.loadContext({ id: this.userId }, true, value ? String(value) : '')
        if (!current() || !applied) return null
        return !!previousOwner && !this.owner
      } catch (error) {
        if (!current()) return null
        Object.assign(this, previous)
        throw error
      }
    },
    selectOwner(value) {
      this.owner = value ? String(value) : ''
      this.persist()
    },
    setDates(dateFrom, dateTo) {
      if (!validRange(dateFrom, dateTo, this.businessToday || formatDate(new Date()))) return false
      this.preset = 'custom'
      this.hadSavedDates = true
      this.date_from = dateFrom
      this.date_to = dateTo
      this.persist()
      return true
    },
    clear() {
      gate(contexts, this).invalidate()
      gate(selections, this).invalidate()
      try { uni.removeStorageSync(storageKey(this.userId)) } catch (error) {}
      this.$reset()
    },
  },
})
