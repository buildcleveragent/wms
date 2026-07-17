<template>
  <view class="page assisted-page">
    <view v-if="checkingPermission" class="state-card">正在读取代办出库权限…</view>

    <template v-else-if="authorized">
      <view class="history-navigation">
        <button class="history-button" @click="openAssistedHistory">历史出库单</button>
        <button class="history-button" @click="openAssistedStats">出库统计</button>
      </view>
      <view class="section">
        <view class="section-title">1. 选择货主</view>
        <view v-if="draft.owner" class="selected-row">
          <view>
            <view class="selected-name">{{ draft.owner.name }}</view>
            <view class="hint">{{ draft.owner.code || `ID ${draft.owner.id}` }}</view>
          </view>
          <button class="small-button" @click="clearOwner">更换</button>
        </view>
        <template v-else>
          <view class="search-row">
            <input
              v-model="ownerSearch"
              class="input search-input"
              placeholder="货主名称或编码"
              @confirm="loadOwners"
            />
            <button class="small-button" :disabled="loadingOwners" @click="loadOwners">
              {{ loadingOwners ? '加载中' : '搜索' }}
            </button>
          </view>
          <view
            v-for="owner in owners"
            :key="owner.id"
            class="choice-row"
            @click="chooseOwner(owner)"
          >
            <view>
              <view class="selected-name">{{ owner.name }}</view>
              <view class="hint">{{ owner.code || `ID ${owner.id}` }}</view>
            </view>
            <text class="choose-text">选择</text>
          </view>
          <view v-if="!loadingOwners && !owners.length" class="empty">没有可代办的货主</view>
        </template>
      </view>

      <view v-if="draft.owner" class="section">
        <view class="section-title">2. 选择客户</view>
        <view v-if="draft.customer" class="selected-row">
          <view>
            <view class="selected-name">{{ draft.customer.name }}</view>
            <view class="hint">{{ draft.customer.code || `ID ${draft.customer.id}` }}</view>
          </view>
          <button class="small-button" @click="clearCustomer">更换</button>
        </view>
        <template v-else>
          <view class="search-row">
            <input
              v-model="customerSearch"
              class="input search-input"
              placeholder="客户名称或编码"
              @confirm="loadCustomers"
            />
            <button class="small-button" :disabled="loadingCustomers" @click="loadCustomers">
              {{ loadingCustomers ? '加载中' : '搜索' }}
            </button>
          </view>
          <view
            v-for="customer in customers"
            :key="customer.id"
            class="choice-row"
            @click="chooseCustomer(customer)"
          >
            <view>
              <view class="selected-name">{{ customer.name }}</view>
              <view class="hint">{{ customer.code || `ID ${customer.id}` }}</view>
            </view>
            <text class="choose-text">选择</text>
          </view>
          <view v-if="!loadingCustomers && !customers.length" class="empty">没有匹配客户</view>
        </template>
      </view>

      <view v-if="draft.customer" class="summary-card">
        <view>
          <view class="selected-name">当前代办出库单</view>
          <view class="hint">已选择 {{ draft.itemCount }} 种商品，共 {{ formatQty(draft.totalQty) }}</view>
        </view>
        <button class="primary-button" @click="continueToProducts">
          继续选品
        </button>
      </view>
    </template>

    <view v-else class="state-card">
      <view>当前账号无代办出库权限。</view>
      <button class="retry-button" @click="checkPermission">重新读取权限</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { useAssistedOutbound } from '@/store/assistedOutbound'
import { api } from '@/utils/request'

const auth = useAuth()
const draft = useAssistedOutbound()
const checkingPermission = ref(true)
const authorized = ref(false)
const ownerSearch = ref('')
const customerSearch = ref('')
const owners = ref([])
const customers = ref([])
const loadingOwners = ref(false)
const loadingCustomers = ref(false)
const navigatingToProducts = ref(false)
let customerRequestSequence = 0

function normalizeList(result) {
  if (Array.isArray(result)) return result
  return Array.isArray(result?.results) ? result.results : []
}

function formatQty(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? String(Number(number.toFixed(3))) : '0'
}

function handleApiError(error) {
  if (Number(error?.statusCode || error?.code) === 403) {
    auth.invalidateAssistedCapability()
    authorized.value = false
  }
  console.error(error)
}

async function loadOwners() {
  loadingOwners.value = true
  try {
    owners.value = normalizeList(await api.assistedOwners(ownerSearch.value, 1))
  } catch (error) {
    handleApiError(error)
  } finally {
    loadingOwners.value = false
  }
}

async function loadCustomers() {
  const ownerId = Number(draft.owner?.id)
  if (!ownerId) return
  const requestSequence = ++customerRequestSequence
  loadingCustomers.value = true
  try {
    const result = await api.assistedCustomers(ownerId, customerSearch.value, 1)
    if (requestSequence === customerRequestSequence && Number(draft.owner?.id) === ownerId) {
      customers.value = normalizeList(result)
    }
  } catch (error) {
    handleApiError(error)
  } finally {
    if (requestSequence === customerRequestSequence) loadingCustomers.value = false
  }
}

function chooseOwner(owner) {
  customerRequestSequence += 1
  draft.setOwner(owner)
  customers.value = []
  customerSearch.value = ''
  loadCustomers()
}

function clearOwner() {
  customerRequestSequence += 1
  draft.setOwner(null)
  customers.value = []
  customerSearch.value = ''
}

function chooseCustomer(customer) {
  draft.setCustomer(customer)
  continueToProducts()
}

function clearCustomer() {
  draft.setCustomer(null)
}

function openAssistedHistory() {
  uni.navigateTo({ url: '/pages/outbound/assisted_history' })
}

function openAssistedStats() {
  uni.navigateTo({ url: '/pages/outbound/assisted_stats' })
}

function continueToProducts() {
  if (!draft.owner?.id || !draft.customer?.id || navigatingToProducts.value) return
  navigatingToProducts.value = true
  uni.navigateTo({
    url: '/pages/outbound/assisted_products',
    fail: () => {
      navigatingToProducts.value = false
      uni.showToast({ title: '无法打开选品页面，请重试', icon: 'none' })
    },
  })
}

async function checkPermission() {
  checkingPermission.value = true
  authorized.value = false
  try {
    await auth.loadProfile({ force: true })
    authorized.value = auth.canProcessAssistedOutbound
    if (!authorized.value) return
    await loadOwners()
    if (draft.owner?.id && !draft.customer?.id) await loadCustomers()
  } catch (error) {
    console.warn('无法读取代办权限', error)
  } finally {
    checkingPermission.value = false
  }
}

onLoad(checkPermission)
onShow(() => {
  navigatingToProducts.value = false
})
</script>

<style scoped>
.assisted-page { box-sizing: border-box; min-height: 100vh; padding-bottom: 40rpx; background: #f6f7fb; }
.history-navigation { display: flex; gap: 14rpx; margin: 18rpx 0; }
.history-button { flex: 1; margin: 0; color: #1677ff; background: #eef5ff; font-size: 25rpx; }
.state-card, .summary-card { margin: 18rpx 0; padding: 22rpx; border-radius: 16rpx; background: #fff; }
.search-row, .selected-row, .choice-row, .summary-card { display: flex; align-items: center; gap: 12rpx; }
.search-input { min-width: 0; margin: 0; }
.selected-row, .choice-row { justify-content: space-between; padding: 18rpx 0; border-bottom: 1rpx solid #edf0f4; }
.choice-row:active { opacity: .7; }
.selected-name { font-size: 28rpx; font-weight: 600; }
.hint { margin-top: 6rpx; color: #697386; font-size: 23rpx; }
.choose-text { color: #1677ff; }
.small-button, .retry-button { flex: none; margin: 0; padding: 0 22rpx; height: 64rpx; line-height: 64rpx; font-size: 25rpx; }
.small-button { color: #1677ff; background: #eef5ff; }
.summary-card { justify-content: space-between; }
.primary-button { flex: none; margin: 0; color: #fff; background: #1677ff; font-size: 26rpx; }
</style>
