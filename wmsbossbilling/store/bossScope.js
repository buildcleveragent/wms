import { defineStore } from 'pinia'
import { api } from '@/utils/request'
import { formatDate } from '@/utils/billing'

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
    maxRangeDays: 367,
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
    }),
    fingerprint: (state) => [
      state.warehouse || 'ALL', state.owner || 'ALL', state.date_from, state.date_to,
    ].join(':'),
  },
  actions: {
    restore(user) {
      const userId = String(user?.id || user?.pk || user?.username || '')
      if (this.userId === userId) return
      this.userId = userId
      this.loaded = false
      this.warehouseOptions = []
      this.ownerOptions = []
      Object.assign(this, monthRange(), readSaved(userId))
    },
    persist() {
      try {
        uni.setStorageSync(storageKey(this.userId), JSON.stringify({
          warehouse: this.warehouse,
          owner: this.owner,
          date_from: this.date_from,
          date_to: this.date_to,
        }))
      } catch (error) {}
    },
    async loadContext(user, force = false) {
      this.restore(user)
      if (this.loaded && !force) return
      const context = await api.bossContext({ warehouse: this.warehouse || undefined })
      this.warehouseOptions = context?.warehouse_options || []
      this.ownerOptions = context?.owner_options || []
      this.maxRangeDays = Number(context?.limits?.max_range_days || 367)
      const allowedWarehouses = new Set(this.warehouseOptions.map((row) => String(row.id)))
      if (this.warehouse && !allowedWarehouses.has(String(this.warehouse))) this.warehouse = ''
      const allowedOwners = new Set(this.ownerOptions.map((row) => String(row.id)))
      if (this.owner && !allowedOwners.has(String(this.owner))) this.owner = ''
      this.loaded = true
      this.persist()
    },
    async selectWarehouse(value) {
      const previousOwner = this.owner
      this.warehouse = value ? String(value) : ''
      this.loaded = false
      await this.loadContext({ id: this.userId }, true)
      this.persist()
      return !!previousOwner && !this.owner
    },
    selectOwner(value) {
      this.owner = value ? String(value) : ''
      this.persist()
    },
    setDates(dateFrom, dateTo) {
      const from = new Date(`${dateFrom}T00:00:00`)
      const to = new Date(`${dateTo}T00:00:00`)
      const today = new Date(`${formatDate(new Date())}T00:00:00`)
      const days = Math.floor((to - from) / 86400000) + 1
      if (!Number.isFinite(days) || days < 1 || days > this.maxRangeDays || to > today) {
        return false
      }
      this.date_from = dateFrom
      this.date_to = dateTo
      this.persist()
      return true
    },
    clear() {
      try { uni.removeStorageSync(storageKey(this.userId)) } catch (error) {}
      this.$reset()
    },
  },
})
