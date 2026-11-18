<template>
  <view class="company-inventory-page">
    <view class="toolbar">
      <view class="filters">
        <input v-model="search" class="input search" placeholder="搜索仓库 / 货主 / 商品 / 货主商品编码 / 仓库SKU编码"
          :disabled="loading" confirm-type="search" @confirm="onSearch" />
        <InventoryNameSelect list-id="inventory-warehouse-options" :model-value="warehouseChoice" :additional-options="warehouseModes" class="warehouse-filter" entity-name="仓库" :load-page="api.companyInventoryWarehouses" :disabled="loading" @update:model-value="selectWarehouse" />
        <InventoryNameSelect ref="ownerSelect" preload @options-state="ownerOptions = $event" list-id="inventory-owner-options" :model-value="ownerId" class="owner-filter" :load-page="api.owners" :disabled="loading" @update:model-value="selectOwner" />
        <button class="query-btn" size="mini" :disabled="loading" @click="onSearch">{{ loading ? '查询中…' : '查询' }}</button>
      </view>
    </view>

    <view v-if="error" class="error" role="alert">{{ error }}<button size="mini" :disabled="loading" @click="load(lastAttempt)">重试</button></view>
    <view ref="tableScroll" class="table-scroll" :aria-busy="loading">
      <view :class="['inventory-table', { 'all-mode': mode === 'all' }]">
        <view class="table-header">
          <view v-for="column in columns" :key="column.key" :class="['inventory-cell', column.class]">{{ column.title }}</view>
        </view>
        <view v-for="(item, index) in rows" :key="`${item.warehouse_id || ''}_${item.owner_id}_${item.product_id}_${item.base_unit}_${index}`"
          :class="['table-row', { odd: index % 2 === 1 }]">
          <view v-for="column in columns" :key="column.key" :class="['inventory-cell', column.class]">
            {{ display(item, column) }}
          </view>
        </view>
      </view>
      <view v-if="!rows.length" class="state-wrap">{{ loading ? '加载中…' : error ? '加载失败，请重试' : '暂无数据' }}</view>
    </view>

    <view class="owner-shortcuts" aria-label="货主快捷筛选">
      <view class="owner-shortcuts-heading">货主快捷筛选</view>
      <view class="owner-shortcut-list">
        <button class="owner-clear" :class="{ selected: ownerId === '' }" size="mini"
          :disabled="loading" :aria-pressed="ownerId === ''" @click="selectOwner('')">全部货主</button>
        <button v-for="owner in shortcutOwners" :key="owner.id" class="owner-shortcut"
          :class="{ selected: String(ownerId) === String(owner.id) }" size="mini" :disabled="loading"
          :aria-pressed="String(ownerId) === String(owner.id)" @click="selectOwner(owner.id)">{{ owner.label }}</button>
      </view>
      <view v-if="ownerOptions.loading" class="owner-shortcuts-state">加载货主中…</view>
      <view v-else-if="ownerOptions.error" class="owner-shortcuts-state">{{ ownerOptions.error }}<button size="mini" @click="ownerSelect.reload()">重试</button></view>
      <view v-else-if="!shortcutOwners.length" class="owner-shortcuts-state">暂无可选货主</view>
    </view>
    <view class="pager">
      <view class="pager-info">共 {{ total }} 条，第 {{ page }} / {{ totalPages }} 页</view>
      <view class="pager-size">
        <text>每页</text>
        <picker :disabled="loading" mode="selector" :range="pageSizeOptions" :value="pageSizeOptions.indexOf(pageSize)" @change="onPageSizeChange">
          <view class="page-size-box">{{ pageSize }} 条</view>
        </picker>
      </view>
      <view class="pager-actions">
        <button size="mini" :disabled="loading || page <= 1" @click="load({ ...applied, page: page - 1 })">上一页</button>
        <button size="mini" :disabled="loading || page >= totalPages" @click="load({ ...applied, page: page + 1 })">下一页</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { formatQuantity } from '../../../frontend/shared/quantity.js'
import { ref, onMounted, onBeforeUnmount, computed, nextTick } from 'vue'
import { api } from '@/utils/request'
import InventoryNameSelect from '@/components/InventoryNameSelect.vue'

const ownerSelect = ref(null), ownerOptions = ref({ items: [], loading: true, error: '' })
const shortcutOwners = computed(() => ownerOptions.value.items.slice(0, 20))
const warehouseModes = [{ id: '__all__', name: '全仓汇总' }]
const mode = ref('warehouse'), search = ref(''), warehouseId = ref(''), ownerId = ref('')
const warehouseChoice = computed(() => mode.value === 'all' ? '__all__' : warehouseId.value)
const rows = ref([]), total = ref(0), page = ref(1)
const pageSize = ref(100)
const pageSizeOptions = [10, 20, 50, 100, 500, 1000]
const loading = ref(false), error = ref(''), tableScroll = ref(null)
const applied = ref(null), lastAttempt = ref(null)
let disposed = false
const totalPages = computed(() => {
  const size = Number(pageSize.value || 100)
  return Math.max(1, Math.ceil(Number(total.value || 0) / size))
})
const columns = computed(() => [
  ...(mode.value === 'warehouse' ? [{ key: 'warehouse_name', title: '仓库' }] : []),
  { key: 'owner_name', title: '货主' },
  { key: 'product_code', title: '编码' }, { key: 'product_sku', title: '仓库SKU编码' },
  { key: 'product_display_name', title: '商品名', class: 'strong' },
  { key: 'product_spec', title: '规格' }, { key: 'base_unit_name', title: '单位' },
  ...['onhand', 'available', 'allocated', 'locked', 'damaged'].map((name, index) => ({
    key: `${name}_qty`, title: ['现有', '可用', '分配', '锁定', '残次'][index], numeric: true,
    class: name === 'available' ? 'number primary' : 'number',
  })),
])
function display(item, column) {
  if (column.key === 'base_unit_name') return item.base_unit_name || '单位未设置'
  if (column.key === 'product_display_name') return item.product_display_name || item.product_name || '-'
  const value = item[column.key]
  if (!column.numeric) return value || '-'
  if (value === null || value === undefined || value === '') return '-'
  return formatQuantity(value)
}
function currentQuery() {
  return { mode: mode.value, search: search.value.trim(), owner_id: ownerId.value,
    warehouse_id: mode.value === 'warehouse' ? warehouseId.value : '', page: 1, page_size: pageSize.value }
}
async function load(params = currentQuery()) {
  if (loading.value || disposed) return
  const query = { ...params }
  lastAttempt.value = query
  loading.value = true; error.value = ''
  try {
    const response = await api.companyInventorySummary(query)
    if (disposed) return
    rows.value = Array.isArray(response?.results) ? response.results : []
    total.value = Number(response?.count || 0)
    mode.value = query.mode; warehouseId.value = query.warehouse_id
    page.value = query.page; pageSize.value = query.page_size
    applied.value = query
    await nextTick()
    const element = tableScroll.value?.$el || tableScroll.value
    if (element) element.scrollTop = 0
  } catch (e) {
    if (!disposed) error.value = e?.message || '库存加载失败，请重试'
  } finally { if (!disposed) loading.value = false }
}
function onSearch() { if (!loading.value) load(currentQuery()) }
function selectOwner(value) {
  if (loading.value) return
  ownerId.value = value
  onSearch()
}
function selectWarehouse(value) {
  if (loading.value) return
  const all = value === '__all__'
  load({ ...currentQuery(), mode: all ? 'all' : 'warehouse', warehouse_id: all ? '' : value })
}
function onPageSizeChange(e) {
  if (loading.value) return
  const idx = Number(e.detail.value)
  const size = pageSizeOptions[idx] || 100
  load({ ...(applied.value || currentQuery()), page: 1, page_size: size })
}
onMounted(() => load())
onBeforeUnmount(() => { disposed = true })
</script>

<style scoped>
.company-inventory-page {
  height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px));
  height: calc(100dvh - var(--window-top, 0px) - var(--window-bottom, 0px));
  display: flex; flex-direction: column; overflow: hidden; box-sizing: border-box; background: #fff;
  color: #334155; font-size: 13px;
}
.toolbar { flex-shrink: 0; padding: 12px; border-bottom: 1px solid #e2e8f0; background: white; position: relative; z-index: 5; }
.query-btn { background: #1677ff; color: white; }
.filters { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.input { box-sizing: border-box; min-width: 0; height: 36px; padding: 0 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; }
.search { flex: 1; }
.warehouse-filter { flex: 0 0 180px; }
.owner-filter { flex: 0 0 220px; }
.query-btn { margin: 0; border-radius: 4px; font-size: 13px; }
.table-scroll { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; position: relative; }
.inventory-table { min-width: 1200px; }
.inventory-table.all-mode { min-width: 1100px; }
.table-header, .table-row { display: grid; grid-template-columns: repeat(2,minmax(100px,1.1fr)) repeat(2,minmax(100px,1fr)) minmax(180px,1.8fr) minmax(100px,1fr) minmax(90px,.8fr) repeat(5,minmax(80px,.8fr)); align-items: center; min-height: 40px; }
.all-mode .table-header, .all-mode .table-row { grid-template-columns: minmax(100px,1.1fr) repeat(2,minmax(100px,1fr)) minmax(180px,1.8fr) minmax(100px,1fr) minmax(90px,.8fr) repeat(5,minmax(80px,.8fr)); }
.table-header { position: sticky; top: 0; z-index: 2; background: #f1f5f9; font-weight: 600; color: #475569; }
.table-row { border-bottom: 1px solid #edf0f4; }
.table-row.odd { background: #f8fafc; }
.inventory-cell { min-width: 0; padding: 8px; overflow-wrap: anywhere; box-sizing: border-box; line-height: 1.5; }
.number { text-align: right; font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; }
.primary { color: #1677ff; font-weight: 600; }
.state-wrap { padding: 40px 12px; text-align: center; color: #64748b; position: sticky; left: 0; }
.error { flex-shrink: 0; padding: 8px 12px; background: #fff1f0; color: #b42318; }
.error button { margin-left: 10px; }
.owner-shortcuts { flex-shrink: 0; padding: 8px 12px; border-top: 1px solid #e2e8f0; background: #fff; max-height: min(180px, 25vh); overflow-y: auto; }
.owner-shortcuts-heading { margin-bottom: 6px; color: #64748b; font-size: 12px; }
.owner-shortcut-list { display: flex; flex-wrap: wrap; gap: 6px; }
.owner-shortcut-list button { margin: 0; max-width: 100%; padding: 5px 10px; font-size: 12px; line-height: 1.5; text-align: left; white-space: normal; overflow-wrap: anywhere; border: 1px solid #e2e8f0; border-radius: 4px; color: #475569; background: #f8fafc; }
.owner-shortcut-list button::after { border: 0; }
.owner-shortcut-list button.selected { color: #1677ff; border-color: #91caff; background: #eff6ff; }
.owner-shortcut-list button[disabled] { opacity: .5; }
.owner-shortcuts-state { margin-top: 6px; color: #64748b; }
.owner-shortcuts-state button { margin-left: 8px; }
.pager { flex-shrink: 0; display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom, 0px)); background: white; border-top: 1px solid #e2e8f0; }
.pager-size, .pager-actions { display: flex; align-items: center; gap: 8px; }
.page-size-box { border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 12px; }
.pager-actions { margin-left: auto; }
.pager-actions button { margin: 0; font-size: 13px; }
@media (max-width: 600px) {
  .search { flex: 1 0 100%; }
  .warehouse-filter, .owner-filter { flex: 1 1 calc(50% - 8px); }
  .query-btn { margin-left: auto; }
  .pager { gap: 8px; }
  .pager-actions { flex-basis: 100%; }
  .pager-actions button { flex: 1; }
}
</style>
