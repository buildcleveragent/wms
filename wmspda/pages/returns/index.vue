<template>
  <view class="return-page">
    <view class="page-heading"><text class="title">退货出库</text><text class="muted">退给供应商或货主 · 确认后立即扣库存</text></view>
    <view v-if="!authorized" class="card">{{ loading ? '正在核验权限…' : '当前账号没有退货出库权限，或未绑定单个仓库。' }}</view>
    <template v-else>
      <view v-if="pending" class="notice">
        <text>上次操作结果待确认。请恢复原请求，避免重复退货。</text>
        <button :disabled="busy" @click="retryPending">继续确认结果</button>
      </view>
      <view v-if="error" class="error" role="alert">{{ error }}</view>
      <view class="tabs">
        <button v-for="item in tabs" :key="item.key" :class="{ active: tab === item.key }" :disabled="busy" @click="switchTab(item.key)">{{ item.label }}</button>
      </view>
      <template v-if="tab === 'edit'">
        <view v-if="order" class="card">
          <text class="section-title">{{ order.return_no }} · {{ statusText(order.status) }}</text>
          <text class="muted">{{ order.owner_name }} → {{ order.recipient_name }} · {{ order.operator_name }}</text>
          <text v-if="order.reversal_reason" class="muted">冲销原因：{{ order.reversal_reason }}</text>
          <button :disabled="busy || !!pending" @click="newDraft">新建另一张退货单</button>
        </view>
        <view v-if="editable" class="card">
          <text class="section-title">1. 搜索或扫码选货</text>
          <text v-if="ownerId" class="muted">本单货主：{{ ownerName }}。更换货主请先移除已选商品。</text>
          <view class="search-row"><input v-model="search" placeholder="商品名称、编码或条码" confirm-type="search" :disabled="busy || !!pending" @confirm="searchProducts(true)" /><button :disabled="busy || !!pending" @click="searchProducts(true)">搜索</button></view>
          <view v-for="product in products" :key="product.product_id" class="product-row" @click="chooseProduct(product)">
            <text class="strong product-name" :title="product.product_name + (product.spec ? ' ' + product.spec : '')">{{ product.product_name }} {{ product.spec }}</text>
            <text class="owner-label" :title="'货主：' + product.owner_name">货主：{{ product.owner_name }}</text>
            <text class="muted product-stock">{{ product.product_code }} · 可用 {{ product.available_qty }} / 破损 {{ product.damaged_qty }} {{ product.base_unit }}</text>
          </view>
          <text v-if="searched && !products.length" class="muted">没有匹配的可退库存</text>
          <button v-if="productNext" :disabled="busy" @click="searchProducts(false)">更多商品</button>
        </view>
        <view v-if="stockOptions.length && editable" class="card">
          <text class="section-title">2. 选择实际退回的库位与批次</text>
          <view v-for="stock in stockOptions" :key="stock.id" class="stock-row">
            <text class="strong">{{ stock.product_name }} · {{ stock.location_name }}</text>
            <text class="muted">货主 {{ stock.owner_name }} · 批次 {{ stock.batch_no || '无' }} · 效期 {{ stock.expiry_date || '无' }} {{ stock.serial_no ? '· 序列号 ' + stock.serial_no : '' }} {{ stock.container_no ? '· 容器 ' + stock.container_no : '' }}</text>
            <text class="muted">可用 {{ stock.available_qty }} / 破损 {{ stock.damaged_qty }} {{ stock.base_unit }} · 已分配 {{ stock.allocated_qty }} / 锁定 {{ stock.locked_qty }}</text>
            <text class="muted">{{ stock.supplier_name ? '原供应商：' + stock.supplier_name : '无可靠供应商来源，仅可退货主' }}</text>
            <button :disabled="busy || !!pending || lines.some(line => line.inventory_id === stock.id)" @click="addStock(stock)">选择该库存</button>
          </view>
          <button v-if="stockNext" :disabled="busy" @click="loadStock(false)">更多库存</button>
        </view>
        <view v-if="lines.length" class="card">
          <text class="section-title">{{ editable ? '3. 填写数量和退货对象' : '退货明细' }}</text>
          <view v-for="line in lines" :key="line.inventory_id" class="return-line">
            <text class="strong">{{ line.product_name }} {{ line.spec }} · {{ line.base_unit }}</text>
            <text class="muted">{{ ownerName }} · {{ line.location_name }} · 批次 {{ line.batch_no || '无' }} {{ line.serial_no || '' }} {{ line.container_no ? '· 容器 ' + line.container_no : '' }}</text>
            <text v-if="editable && line.stock_available != null" class="muted">可用 {{ line.stock_available }} / 已登记破损 {{ line.stock_damaged }}</text>
            <view class="quantity-grid">
              <label>正常退货<input v-model="line.available_qty" type="digit" :disabled="!editable || busy || !!pending" /></label>
              <label>已登记破损<input v-model="line.damaged_qty" type="digit" :disabled="!editable || busy || !!pending" /></label>
              <label>现场发现破损<input v-model="line.new_damage_qty" type="digit" :disabled="!editable || busy || !!pending" /></label>
            </view>
            <button v-if="editable" :disabled="busy || !!pending" class="text-button" @click="removeLine(line)">移除</button>
          </view>
          <text class="hint">现场发现破损从可用库存扣除；冲销后恢复为破损库存。已分配、锁定库存不可直接退货。</text>
          <view class="recipient-options">
            <button :class="{ active: recipient === 'OWNER' }" :disabled="!editable || busy || !!pending" @click="recipient = 'OWNER'">退给货主：{{ ownerName }}</button>
            <button v-if="supplier" :class="{ active: recipient === 'SUPPLIER' }" :disabled="!editable || busy || !!pending" @click="recipient = 'SUPPLIER'">退给供应商：{{ supplier.name }}</button>
          </view>
          <text v-if="!supplier && editable" class="hint">所选批次没有共同、可靠的供应商来源。本单可退货主；退不同供应商请分别建单。</text>
          <textarea v-model="reason" maxlength="200" placeholder="填写退货原因（必填）" :disabled="!editable || busy || !!pending" />
          <view v-if="editable" class="actions"><button :disabled="busy || !!pending" @click="saveDraft">保存草稿</button><button class="primary" :disabled="busy || !!pending" @click="confirmReturn">确认退货</button></view>
        </view>
        <view v-if="order?.can_edit" class="card"><button class="danger" :disabled="busy || !!pending" @click="deleteDraft">删除草稿</button></view>
        <view v-if="order?.can_reverse" class="card">
          <text class="section-title">冲销本人退货单</text><text class="hint">请确认货物仍在仓库或已实际退回。冲销会整单恢复库存，保留原退货记录。</text>
          <textarea v-model="reverseReason" maxlength="200" placeholder="填写冲销原因（必填）" :disabled="busy || !!pending" />
          <button class="danger" :disabled="busy || !!pending || !reverseReason.trim()" @click="reverseReturn">整单冲销</button>
        </view>
      </template>
      <template v-else>
        <view class="card filters">
          <view class="actions"><button @click="setPeriod('today')">今日</button><button @click="setPeriod('month')">本月</button></view>
          <label>开始日期<input v-model="filters.start_date" placeholder="YYYY-MM-DD" /></label><label>结束日期<input v-model="filters.end_date" placeholder="YYYY-MM-DD" /></label>
          <picker :range="ownerChoices" range-key="label" @change="filters.owner_id = ownerChoices[$event.detail.value].id"><view class="picker-field">货主：{{ choiceLabel(ownerChoices, filters.owner_id) }}</view></picker>
          <picker :range="recipientChoices" range-key="label" @change="filters.recipient_type = recipientChoices[$event.detail.value].id"><view class="picker-field">对象：{{ choiceLabel(recipientChoices, filters.recipient_type) }}</view></picker>
          <picker :range="supplierChoices" range-key="label" @change="filters.supplier_id = supplierChoices[$event.detail.value].id"><view class="picker-field">供应商：{{ choiceLabel(supplierChoices, filters.supplier_id) }}</view></picker>
          <picker :range="operatorChoices" range-key="label" @change="filters.operator_id = operatorChoices[$event.detail.value].id"><view class="picker-field">操作员：{{ choiceLabel(operatorChoices, filters.operator_id) }}</view></picker>
          <picker :range="productChoices" range-key="label" @change="filters.product_id = productChoices[$event.detail.value].id"><view class="picker-field">商品：{{ choiceLabel(productChoices, filters.product_id) }}</view></picker>
          <view class="actions"><button :disabled="busy" @click="loadTab">查询</button><button :disabled="busy" @click="exportRows">导出 Excel</button><button @click="clearFilters">清除筛选</button></view>
        </view>
        <template v-if="tab === 'history'">
          <view v-for="row in history" :key="row.id" class="card clickable history-row" @click="openOrder(row.id)">
            <view class="section-title history-identity"><text class="history-number" :title="row.return_no">{{ row.return_no }}</text><text class="history-status"> · {{ statusText(row.status) }}</text></view>
            <text class="history-recipient" :title="row.owner_name + ' → ' + row.recipient_name">{{ row.owner_name }} → {{ row.recipient_name }}</text>
            <text class="muted history-meta" :title="row.operator_name + ' · ' + (row.confirmed_at || row.created_at)">{{ row.operator_name }} · {{ row.confirmed_at || row.created_at }}</text>
            <view class="muted history-summary"><text class="history-reason" :title="row.reason">{{ row.reason }}</text><text class="history-count"> · {{ row.lines.length }} 条明细</text></view>
          </view>
          <view v-if="!history.length" class="card muted">暂无退货记录</view><button v-if="historyNext" :disabled="busy" @click="loadHistory(false)">更多记录</button>
        </template>
        <template v-else>
          <view class="card"><text class="section-title">退货 {{ stats.confirmed_count || 0 }} 单 · 冲销 {{ stats.reversed_count || 0 }} 单</text><text class="hint">按实际操作日期统计；净退货 = 退货 − 冲销。不同基本单位分别汇总。</text></view>
          <view v-for="group in statGroups" :key="group.key" class="card">
            <text class="section-title">{{ group.label }}</text>
            <view v-for="row in stats[group.key] || []" :key="String(row.id) + row.base_unit" class="stat-row" @click="drillDown(group.key, row)">
              <text class="strong">{{ row.label }} · {{ row.base_unit }}</text><text>正常 {{ row.normal_qty }} / 破损 {{ row.damaged_qty }}（现场 {{ row.new_damage_qty }}）</text><text class="muted">退货 {{ row.confirmed_count }} 单 / 冲销 {{ row.reversed_count }} 单 · 冲销数量 {{ row.reverse_qty }} · 净退货 {{ row.net_qty }}</text>
            </view><text v-if="!stats[group.key]?.length" class="muted">暂无数据</text>
          </view>
        </template>
      </template>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { createIdempotencyUuid, downloadReturnOrders } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
import { commonSupplier, pendingStorageKey, returnLineInput, returnRequest, returnSession } from '@/utils/pdaReturns'

const auth = useAuth()
const authorized = computed(() => auth.profileLoaded && auth.profile?.capabilities?.can_process_pda_return === true)
const loading = ref(false), busy = ref(false), error = ref(''), tab = ref('edit'), session = ref(''), pending = ref(null)
const order = ref(null), lines = ref([]), ownerId = ref(null), ownerName = ref(''), reason = ref(''), recipient = ref('OWNER'), reverseReason = ref('')
const search = ref(''), searched = ref(false), products = ref([]), productPage = ref(1), productNext = ref(false)
const stockOptions = ref([]), stockProduct = ref(null), stockPage = ref(1), stockNext = ref(false)
const history = ref([]), historyPage = ref(1), historyNext = ref(false), stats = ref({}), options = ref({})
const tabs = [{ key: 'edit', label: '新建 / 详情' }, { key: 'history', label: '退货记录' }, { key: 'stats', label: '退货统计' }]
const statGroups = [{ key: 'units', label: '按单位汇总' }, { key: 'daily', label: '每日汇总' }, { key: 'owners', label: '货主汇总' }, { key: 'recipients', label: '接收方汇总' }, { key: 'products', label: '商品汇总' }, { key: 'operators', label: '操作员汇总' }]
const recipientChoices = [{ id: '', label: '全部' }, { id: 'OWNER', label: '退货主' }, { id: 'SUPPLIER', label: '退供应商' }]
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
const filters = ref({ start_date: today(), end_date: today() })
const supplier = computed(() => commonSupplier(lines.value))
const editable = computed(() => !order.value || order.value.can_edit)
const choices = (rows, id, label) => [{ id: '', label: '全部' }, ...(rows || []).filter(row => row[id]).map(row => ({ id: row[id], label: row[label] }))]
const ownerChoices = computed(() => choices(options.value.owners, 'owner_id', 'owner__name'))
const supplierChoices = computed(() => choices(options.value.suppliers, 'supplier_id', 'recipient_name'))
const operatorChoices = computed(() => choices(options.value.operators, 'operator_id', 'operator__username'))
const productChoices = computed(() => choices(options.value.products, 'lines__product_id', 'lines__product_name'))
const choiceLabel = (rows, id) => rows.find(row => row.id === id)?.label || '全部'
const statusText = status => ({ DRAFT: '草稿', COMPLETED: '已完成', REVERSED: '已冲销' }[status] || status)
const query = () => Object.fromEntries(Object.entries(filters.value).filter(([, value]) => value !== '' && value != null))
const sameSession = value => value && value === returnSession(auth.user) && value === session.value
let sequence = 0
let initialTab = ''
onLoad(params => { initialTab = params?.tab === 'stats' ? 'stats' : '' })

async function run(work) {
  if (busy.value) return
  const current = session.value
  busy.value = true; error.value = ''
  try { return await work(current) }
  catch (e) { if (sameSession(current)) error.value = e.message || '操作失败，请重试' }
  finally { busy.value = false }
}
function resetEditor() {
  order.value = null; lines.value = []; ownerId.value = null; ownerName.value = ''; reason.value = ''; recipient.value = 'OWNER'; reverseReason.value = ''; stockOptions.value = []; products.value = []; search.value = ''; searched.value = false
}
async function ask(title, content) {
  return new Promise(resolve => uni.showModal({ title, content, confirmText: '确定', cancelText: '取消', success: result => resolve(result.confirm), fail: () => resolve(false) }))
}
async function newDraft() {
  if (busy.value || pending.value) return
  if (editable.value && lines.value.length && !(await ask('新建退货单', '未保存的修改将丢弃，是否继续？'))) return
  resetEditor()
}
function loadOrder(row) {
  order.value = row; ownerId.value = row.owner_id; ownerName.value = row.owner_name; reason.value = row.reason; recipient.value = row.recipient_type; reverseReason.value = ''
  lines.value = row.lines.map(line => ({ ...line, ...line.dimensions }))
  stockOptions.value = []; products.value = []; tab.value = 'edit'
}
async function mutate(path, method, data, current) {
  if (pending.value) throw new Error('请先确认上次操作的结果')
  const entry = { path, method, data }
  uni.setStorageSync(pendingStorageKey(current), entry)
  pending.value = entry
  return sendPending(entry, current)
}
async function sendPending(entry, current) {
  try {
    const result = await returnRequest(entry.path, entry.method, entry.data)
    if (!sameSession(current)) throw new Error('账号或仓库已切换')
    uni.removeStorageSync(pendingStorageKey(current)); pending.value = null
    if (result?.id) loadOrder(result)
    else if (entry.method === 'DELETE') resetEditor()
    return result
  } catch (e) {
    if (sameSession(current) && [400, 409, 422].includes(e.statusCode)) {
      uni.removeStorageSync(pendingStorageKey(current)); pending.value = null
      if (e.statusCode === 409 && /^\d+\//.test(entry.path)) {
        const id = entry.path.split('/')[0]
        try { const fresh = await returnRequest(`${id}/`); if (sameSession(current)) loadOrder(fresh) } catch {}
      }
    }
    throw e
  }
}
const retryPending = () => run(current => sendPending(pending.value, current))
function draftData() {
  if (!lines.value.length || !reason.value.trim()) throw new Error('请选择库存并填写退货原因')
  if (recipient.value === 'SUPPLIER' && !supplier.value) throw new Error('所选库存没有共同的原供应商')
  return { owner_id: ownerId.value, recipient_type: recipient.value, supplier_id: recipient.value === 'SUPPLIER' ? supplier.value.id : null, reason: reason.value.trim(), lines: lines.value.map(returnLineInput), ...(order.value ? { version: order.value.version } : { request_id: createIdempotencyUuid() }) }
}
async function save(current) { return mutate(order.value ? `${order.value.id}/` : '', order.value ? 'PATCH' : 'POST', draftData(), current) }
const saveDraft = () => run(current => save(current))
const confirmReturn = () => run(async current => {
  const row = await save(current)
  if (!sameSession(current)) return
  const summary = row.lines.map(line => `${line.product_name}：正常 ${line.available_qty}，已登记破损 ${line.damaged_qty}，现场破损 ${line.new_damage_qty} ${line.base_unit}`).join('\n')
  if (!(await ask('确认退货并扣库存', `${row.owner_name} → ${row.recipient_name}\n${summary}`)) || !sameSession(current)) return
  await mutate(`${row.id}/confirm/`, 'POST', { version: row.version, request_id: createIdempotencyUuid() }, current)
})
const reverseReturn = () => run(async current => {
  if (!reverseReason.value.trim()) throw new Error('请填写冲销原因')
  if (!(await ask('确认整单冲销', '货物应仍在仓库或已退回。恢复原库存，现场发现破损仍计为破损。')) || !sameSession(current)) return
  await mutate(`${order.value.id}/reverse/`, 'POST', { version: order.value.version, request_id: createIdempotencyUuid(), reason: reverseReason.value.trim() }, current)
})
const deleteDraft = () => run(async current => {
  if (!(await ask('删除草稿', '确认删除此退货草稿？')) || !sameSession(current)) return
  await mutate(`${order.value.id}/`, 'DELETE', { version: order.value.version }, current)
})
function removeLine(line) {
  lines.value = lines.value.filter(row => row !== line)
  if (!lines.value.length) { ownerId.value = null; ownerName.value = ''; stockOptions.value = []; products.value = []; searched.value = false }
  if (!supplier.value) recipient.value = 'OWNER'
}
function addStock(stock) {
  if (busy.value || pending.value) return
  if (ownerId.value && ownerId.value !== stock.owner_id) { error.value = '不同货主请分别建单'; return }
  ownerId.value = stock.owner_id; ownerName.value = stock.owner_name
  lines.value.push({ ...stock, inventory_id: stock.id, stock_available: stock.available_qty, stock_damaged: stock.damaged_qty, available_qty: '0', damaged_qty: '0', new_damage_qty: '0' })
  if (!supplier.value) recipient.value = 'OWNER'
}
async function searchProducts(reset = true) {
  return run(async current => {
    const page = reset ? 1 : productPage.value + 1
    const result = await returnRequest('products/', 'GET', { search: search.value, ...(ownerId.value ? { owner_id: ownerId.value } : {}), page })
    if (!sameSession(current)) return
    products.value = reset ? result.results : [...products.value, ...result.results]; productPage.value = page; productNext.value = !!result.next; searched.value = true
  })
}
async function chooseProduct(product) {
  if (busy.value || pending.value) return
  if (ownerId.value && ownerId.value !== product.owner_id) { error.value = '不同货主请分别建单'; return }
  stockProduct.value = product; await loadStock(true)
}
async function loadStock(reset = true) {
  return run(async current => {
    const page = reset ? 1 : stockPage.value + 1
    const result = await returnRequest('stock-options/', 'GET', { product_id: stockProduct.value.product_id, page })
    if (!sameSession(current)) return
    stockOptions.value = reset ? result.results : [...stockOptions.value, ...result.results]; stockPage.value = page; stockNext.value = !!result.next
  })
}
async function openOrder(id) {
  if (pending.value) { error.value = '请先确认上次操作的结果'; return }
  if (editable.value && lines.value.length && !(await ask('打开退货记录', '未保存的修改将丢弃，是否继续？'))) return
  return run(async current => { const row = await returnRequest(`${id}/`); if (sameSession(current)) loadOrder(row) })
}
async function loadHistory(reset = true) {
  return run(async current => {
    const page = reset ? 1 : historyPage.value + 1
    const result = await returnRequest('', 'GET', { ...query(), page })
    if (!sameSession(current)) return
    history.value = reset ? result.results : [...history.value, ...result.results]; historyPage.value = page; historyNext.value = !!result.next
  })
}
async function loadTab() {
  if (tab.value === 'history') return loadHistory()
  if (tab.value === 'stats') return run(async current => { const result = await returnRequest('stats/', 'GET', query()); if (sameSession(current)) stats.value = result })
}
async function switchTab(value) {
  tab.value = value
  if (value !== 'edit') {
    await run(async current => { const result = await returnRequest('options/'); if (sameSession(current)) options.value = result })
    await loadTab()
  }
}
function setPeriod(mode) { filters.value.start_date = mode === 'month' ? `${today().slice(0, 7)}-01` : today(); filters.value.end_date = today(); loadTab() }
function clearFilters() { filters.value = { start_date: today(), end_date: today() }; loadTab() }
function drillDown(group, row) {
  if (group === 'daily') { filters.value.start_date = row.id; filters.value.end_date = row.id }
  if (group === 'owners') filters.value.owner_id = row.id
  if (group === 'products') filters.value.product_id = row.id
  if (group === 'operators') filters.value.operator_id = row.id
  if (group === 'recipients') { const [kind, id] = String(row.id).split(':'); filters.value.recipient_type = kind; if (kind === 'SUPPLIER') filters.value.supplier_id = Number(id); else filters.value.owner_id = Number(id) }
  tab.value = 'history'; loadHistory()
}
const exportRows = () => run(async current => {
  const result = await downloadReturnOrders(query())
  if (!sameSession(current)) return
  if (result?.tempFilePath) uni.openDocument({ filePath: result.tempFilePath, fileType: 'xlsx', showMenu: true })
})
useBarcodeScanner({ onScan: code => { if (tab.value === 'edit' && editable.value && !busy.value && !pending.value) { search.value = code; searchProducts() } } })
onShow(async () => {
  const currentSequence = ++sequence
  loading.value = true
  try {
    await auth.loadProfile({ force: true })
    if (currentSequence !== sequence) return
    const nextSession = returnSession(auth.user)
    if (session.value !== nextSession) { resetEditor(); history.value = []; stats.value = {}; options.value = {}; tab.value = 'edit'; filters.value = { start_date: today(), end_date: today() } }
    session.value = nextSession
    pending.value = nextSession ? uni.getStorageSync(pendingStorageKey(nextSession)) || null : null
    if (initialTab && authorized.value) { const target = initialTab; initialTab = ''; await switchTab(target) }
  } catch (e) { error.value = e.message || '权限核验失败' }
  finally { if (currentSequence === sequence) loading.value = false }
})
</script>

<style scoped>
.return-page { max-width: 1040px; margin: auto; padding: 20rpx 24rpx 70rpx; color: #172b35; background: #f4f7f8; min-height: 100vh; box-sizing: border-box; }
.page-heading { display: flex; flex-direction: column; gap: 10rpx; padding: 18rpx 0 28rpx; }
.title { font-size: 40rpx; font-weight: 700; }.section-title { display: block; font-size: 30rpx; font-weight: 650; margin-bottom: 18rpx; }
.card { background: white; border: 1px solid #e0e7ea; border-radius: 16rpx; padding: 24rpx; margin-bottom: 20rpx; overflow-wrap: anywhere; }
.tabs, .actions, .search-row, .recipient-options { display: flex; gap: 12rpx; margin-bottom: 16rpx; flex-wrap: wrap; }
.tabs button { flex: 1; white-space: nowrap; }.active, .primary { background: #176c64 !important; color: white !important; }
button { font-size: 26rpx; margin: 0; padding: 0 20rpx; border-radius: 10rpx; }button[disabled] { opacity: .5; }
input, textarea, .picker-field { box-sizing: border-box; background: #f6f8fa; border: 1px solid #d4dfe5; border-radius: 8rpx; padding: 14rpx; min-height: 76rpx; font-size: 28rpx; }
.search-row input { flex: 1; min-width: 160px; }textarea { width: 100%; height: 130rpx; margin: 18rpx 0; }
.product-row, .stock-row, .return-line, .stat-row { border-top: 1px solid #e9eef0; padding: 22rpx 0; display: flex; flex-direction: column; gap: 10rpx; }
.product-row { cursor: pointer; }.owner-label { color: #176c64; font-weight: 600; }.strong { font-weight: 600; }.muted { display: block; color: #62747e; font-size: 25rpx; line-height: 1.6; }.hint { display: block; color: #687780; font-size: 24rpx; line-height: 1.6; padding: 10rpx 0 18rpx; }
.quantity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12rpx; }.quantity-grid label { font-size: 23rpx; line-height: 1.8; }.quantity-grid input { width: 100%; }
.danger { color: #ad3434; background: #fff2f0; }.text-button { align-self: flex-end; color: #ad3434; }.notice, .error { padding: 20rpx; border-radius: 10rpx; margin-bottom: 20rpx; line-height: 1.6; }.notice { background: #fff5dc; }.error { background: #fff0ef; color: #a32c24; }
.filters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16rpx; }.filters .actions { grid-column: 1 / -1; }.filters label { font-size: 25rpx; }.clickable, .stat-row { cursor: pointer; }
@media (min-width: 1024px) {
  .return-page { max-width: 1600px; }
  .product-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(120px, 200px) auto; align-items: center; gap: 24px; padding: 14px 0; }
  .product-row .product-name, .product-row .owner-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .product-row .product-stock { white-space: nowrap; }
  .product-row:hover { background: #f6faf9; }
  .history-row { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, .85fr) minmax(0, 1.2fr) minmax(0, 1fr); align-items: center; gap: 20px; padding: 14px 16px; }
  .history-row .section-title { margin: 0; }
  .history-identity, .history-row .history-summary { display: flex; align-items: center; gap: 4px; min-width: 0; }
  .history-number, .history-recipient, .history-meta, .history-reason { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .history-status, .history-count { flex-shrink: 0; white-space: nowrap; }
  .history-row:hover { background: #f6faf9; }
}
@media (max-width: 480px) { .filters { grid-template-columns: 1fr; }.quantity-grid { gap: 8rpx; }.card { padding: 20rpx; } }
</style>
