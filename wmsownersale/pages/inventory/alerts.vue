<template>
  <view class="alerts-page">
    <view class="heading"><text class="title">库存预警</text><button class="refresh" size="mini" :disabled="loading || denied" @click="refresh">刷新</button></view>
    <view class="toolbar"><text class="muted">本货主全仓库存 · 按商品计数</text><button size="mini" @click="settings()">{{ data?.can_edit ? '阈值设置' : '查看阈值' }}</button></view>
    <view class="categories">
      <button v-for="item in categories" :key="item.code" :class="['category', {selected: category === item.code}]" @click="category = item.code">
        <text class="count">{{ data ? (item.enabled ? item.count : '—') : '—' }}</text><text>{{ item.label }}</text><text class="small">{{ item.enabled === false ? '已关闭' : '查看商品' }}</text>
      </button>
    </view>
    <text class="muted note">各类商品可重复；未动销依据真实销售出库，不代表每个批次库龄。</text>
    <view class="filters">
      <input v-model="search" placeholder="商品名称 / 编码 / SKU" @confirm="applySearch" /><button class="search" size="mini" @click="applySearch">搜索</button>
      <picker :range="statusOptions" range-key="label" :value="statusIndex" @change="statusIndex = Number($event.detail.value)"><view class="picker">{{ statusOptions[statusIndex].label }} ▾</view></picker>
      <picker v-if="category === 'expiry'" :range="expiryOptions" range-key="label" :value="expiryIndex" @change="expiryIndex = Number($event.detail.value)"><view class="picker">{{ expiryOptions[expiryIndex].label }} ▾</view></picker>
    </view>
    <text v-if="selectedCategory?.needs_review_count" class="warning">{{ selectedCategory.needs_review_count }} 个商品存在待核对数据（含效期未记录），可切换“待核对”查看。</text>
    <view v-if="error" class="error"><text>{{ error }}</text><button v-if="!denied" class="retry" size="mini" @click="retry">重试</button></view>
    <text v-if="data" class="muted note">{{ data.count }} 个商品 · 最近更新：{{ time(data.as_of) }}</text>
    <text v-if="loading && !data" class="empty">正在加载预警…</text>
    <text v-if="data && !data.results.length" class="empty">{{ selectedCategory?.enabled === false ? '本类预警已关闭' : '暂无符合条件的商品' }}</text>
    <view v-for="row in data?.results || []" :key="row.product.id" class="alert-card">
      <text class="product-name">{{ row.product.name }}</text>
      <text v-if="row.product.is_active === false" class="muted">商品已停用，仍有现存库存</text>
      <text class="muted">编码：{{ row.product.code }} · SKU：{{ row.product.sku || '未记录' }}</text>
      <text class="muted">规格：{{ row.product.spec || '未记录' }} · 单位：{{ row.product.unit_label || row.product.base_unit }}</text>
      <text :class="row.status === 'needs_review' ? 'warning' : 'reason'">{{ row.status === 'needs_review' ? '数据待核对：' : '' }}{{ row.reason }}</text>
      <view v-for="group in row.quantities" :key="group.base_unit" class="quantities">
        <text>现有 {{ qty(group.onhand_qty) }} {{ unitLabel(row, group.base_unit) }}</text><text>可用 {{ qty(group.available_qty) }} {{ unitLabel(row, group.base_unit) }}</text>
        <text class="muted">分配 {{ qty(group.allocated_qty) }} · 锁定 {{ qty(group.locked_qty) }} · 残次 {{ qty(group.damaged_qty) }}</text>
      </view>
      <text v-if="category === 'low'">最低库存：{{ qty(row.thresholds.low.threshold) }} {{ row.product.unit_label }}（可用量≤阈值提醒）</text>
      <text v-else>阈值：{{ row.thresholds[category].days }} 天 · {{ row.thresholds[category].source }}</text>
      <template v-if="category === 'slow' && row.observed_since">
        <text>已 {{ row.inactive_days }} 天未动销</text><text class="muted">观察起点：{{ time(row.observed_since) }}</text>
        <text class="muted">最近销售出库：{{ row.last_sale_at ? time(row.last_sale_at) : '无有效销售出库记录' }}</text>
      </template>
      <text v-if="row.missing_expiry" class="warning">部分现存库存效期未记录，未推测到期时间。</text>
      <view v-for="(batch, index) in row.batches || []" :key="index" :class="['batch', { expired: batch.status === 'expired' }]">
        <text>{{ batch.status === 'expired' ? `已过期 ${-batch.remaining_days} 天` : batch.remaining_days === 0 ? '今天到期' : `${batch.remaining_days} 天后到期` }}</text>
        <text>{{ batch.warehouse_name }} · 批次：{{ batch.batch_no || '未记录' }}</text><text>到期日：{{ batch.expiry_date }} · 现有 {{ qty(batch.onhand_qty) }} {{ unitLabel(row, batch.base_unit) }}</text>
      </view>
      <view class="card-actions"><button v-if="row.summary_id" size="mini" @click="openDetail(row.summary_id)">查看库存明细</button><text v-else class="muted">尚无库存记录</text><button size="mini" @click="settings(row.product.id)">{{ data.can_edit ? '设置商品阈值' : '查看商品阈值' }}</button></view>
    </view>
    <button v-if="data?.next_page" class="load-more" :disabled="loading" @click="more(data.next_page)">加载更多</button>
  </view>
</template>
<script setup>
import { formatQuantity } from '@/utils/quantity'
import { computed, ref } from 'vue'
import { onPullDownRefresh } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useVisibleInventoryRequest } from '@/composables/useVisibleInventoryRequest'
defineOptions({ inheritAttrs: false })
const auth = useAuth()
const category = ref('low'), search = ref(''), appliedSearch = ref(''), statusIndex = ref(0), expiryIndex = ref(0)
const statusOptions = [{label: '已确认预警', value: 'confirmed'}, {label: '待核对', value: 'needs_review'}]
const expiryOptions = [{label: '全部效期提醒', value: 'all'}, {label: '临期', value: 'expiring'}, {label: '已过期', value: 'expired'}]
const identity = computed(() => `${auth.user?.id}:${auth.user?.owner_id}:${auth.roles.join(',')}`)
const query = computed(() => JSON.stringify([category.value, appliedSearch.value, statusIndex.value, expiryIndex.value]))
const {data, error, loading, denied, refresh, retry, more} = useVisibleInventoryRequest(async ({page}) => {
  const result = await api.inventoryAlertItems({category: category.value, search: appliedSearch.value, status: statusOptions[statusIndex.value].value, expiry: category.value === 'expiry' ? expiryOptions[expiryIndex.value].value : 'all', page, page_size: 20})
  if (!Array.isArray(result?.results) || !Array.isArray(result?.categories)) throw new Error('数据不完整')
  return result
}, query, identity)
const categories = computed(() => data.value?.categories || [{code:'low',label:'低库存'}, {code:'expiry',label:'临期／已过期'}, {code:'slow',label:'长期未动销'}])
const selectedCategory = computed(() => categories.value.find(c => c.code === category.value))
function applySearch() { if (appliedSearch.value === search.value.trim()) refresh(); else appliedSearch.value = search.value.trim() }
function settings(id) { uni.navigateTo({url: '/pages/inventory/alert-settings' + (id ? `?product_id=${id}` : '')}) }
function openDetail(id) { uni.navigateTo({url: `/pages/inventory/detail?id=${id}`}) }
function qty(v) { return v == null ? '未设置' : formatQuantity(v) }
function unitLabel(row, code) { return code === row.product.base_unit ? row.product.unit_label || code : code }
function time(v) { return (v || '').replace('T',' ').replace(/\.\d+/, '').replace(/\+08:00$/, '（北京时间）') }
onPullDownRefresh(refresh)
</script>
<style scoped>
.alerts-page { max-width: 960px; margin: auto; padding: 16px; background: #f6f7f9; min-height: 100vh; box-sizing: border-box; color: #1f2937; }
.heading,.toolbar,.card-actions { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }.title {font-size:22px;font-weight:700}.heading button,.toolbar button,.card-actions button {margin:0}.toolbar{margin:14px 0}
.categories{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.category{margin:0;padding:12px 4px;display:flex;flex-direction:column;gap:5px;line-height:1.5;background:white;border:1px solid #e5e7eb;font-size:13px}.category.selected{border-color:#2563eb;background:#eff6ff;color:#1d4ed8}.category::after{border:0}.count{font-size:27px;font-weight:700}.small{font-size:11px;color:#667085}.muted{font-size:13px;color:#667085;line-height:1.7}.note{display:block;margin:12px 0}.filters{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.filters input{flex:1;min-width:120px;background:white;border:1px solid #d1d5db;border-radius:8px;padding:10px;height:22px;font-size:14px}.filters button{margin:0}.picker{background:white;border:1px solid #d1d5db;border-radius:8px;padding:9px;font-size:13px}
.alert-card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:14px 0;display:flex;flex-direction:column;gap:9px;font-size:14px;overflow-wrap:anywhere}.product-name{font-size:18px;font-weight:700}.quantities{display:flex;flex-wrap:wrap;gap:8px 16px;padding:10px;background:#f8fafc;border-radius:8px}.quantities .muted{width:100%}.reason,.warning{color:#92400e;line-height:1.7}.warning{display:block;font-size:13px}.batch{display:flex;flex-direction:column;gap:5px;padding:12px;background:#fff7ed;border-radius:8px;font-size:13px;line-height:1.6}.batch.expired{background:#fef2f2;color:#b42318}.card-actions{margin-top:8px}.error{color:#b42318;padding:12px;background:#fff7ed;line-height:1.6}.error button{margin-top:8px}.empty{display:block;padding:32px;text-align:center;color:#667085}.load-more{font-size:14px}
</style>
