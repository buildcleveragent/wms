import { defineStore } from 'pinia'

export function refreshUserId(user) { return String(user?.id || user?.pk || user?.username || '') }
export function validMinutes(value) { return Number.isInteger(value) && value >= 1 && value <= 1440 }
function initial(user) {
  if (user === undefined) {
    try { user = JSON.parse(uni.getStorageSync('user') || 'null') } catch { user = null }
  }
  const userId = refreshUserId(user)
  let saved
  try { saved = JSON.parse(uni.getStorageSync(`boss_refresh:${userId}`) || 'null') } catch {}
  return {
    userId,
    automatic: !!userId && saved?.automatic === true,
    minutes: validMinutes(saved?.minutes) ? saved.minutes : 5,
  }
}
export const useRefreshPreferences = defineStore('bossRefreshPreferences', {
  state: () => initial(),
  actions: {
    restore(user) {
      if (refreshUserId(user) !== this.userId) Object.assign(this, initial(user))
    },
    save(automatic, minutes) {
      if (!this.userId) throw new Error('请先登录')
      if (typeof automatic !== 'boolean' || !validMinutes(minutes)) throw new Error('请输入 1～1440 的整数分钟')
      // Persist first: storage failures must not appear as successfully saved preferences.
      uni.setStorageSync(`boss_refresh:${this.userId}`, JSON.stringify({ automatic, minutes }))
      this.automatic = automatic
      this.minutes = minutes
    },
  },
})
