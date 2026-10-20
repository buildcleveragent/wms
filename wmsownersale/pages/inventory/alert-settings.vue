<template>
  <view class="settings-page">
    <view class="heading"><text class="title">{{ productId ? '商品预警阈值' : '货主预警设置' }}</text><button size="mini" :disabled="loading || saving || denied" @click="reload">重新加载</button></view>
    <view v-if="error" class="error"><text>{{ error }}</text><button v-if="!denied" class="retry" size="mini" @click="retry">重试</button></view>
    <text v-if="loading && !data" class="muted">正在加载配置…</text>
    <template v-if="data">
      <text v-if="!data.can_edit" class="notice">仅货主管理员可修改，当前为只读。</text>
      <view v-if="productId" class="card">
        <text class="product-name">{{ data.product.name }}</text><text>编码：{{ data.product.code }}</text>
        <text>规格：{{ data.product.spec || '未记录' }} · 单位：{{ data.product.unit_label }}</text>
        <text v-if="data.product.is_active === false" class="muted">商品已停用：低库存提醒暂停，现存库存仍参与效期和未动销检查。</text>
        <text class="muted">低库存按本货主全仓可用数量判断；留空表示不启用该商品低库存提醒。</text>
        <label>最低库存（{{ data.product.unit_label }}）</label><input class="minimum" v-model="form.min_stock" type="digit" :disabled="readonly" placeholder="未设置" />
        <text class="muted">当前低库存提醒：{{ data.effective.low.enabled ? '已启用' : '未启用' }}{{ data.defaults.low_enabled ? '' : '（货主已关闭此类提醒）' }}</text>
        <view v-for="key in ['expiry', 'slow']" :key="key" class="field">
          <text class="field-label">{{ labels[key] }}</text>
          <picker :disabled="readonly" :range="modes" range-key="label" :value="modeIndex(form[key + '_mode'])" @change="form[key + '_mode'] = modes[Number($event.detail.value)].value"><view class="picker">{{ modes[modeIndex(form[key + '_mode'])].label }} ▾</view></picker>
          <input v-if="form[key + '_mode'] === 'custom'" :class="key + '-days'" v-model="form[key + '_days']" type="number" :disabled="readonly" placeholder="天数，1～3650" />
          <text class="muted">当前生效：{{ data.effective[key].enabled ? `${data.effective[key].days} 天` : '已关闭' }} · {{ data.effective[key].source }}；货主默认 {{ data.defaults[key + '_days'] }} 天{{ data.defaults[key + '_enabled'] ? '' : '（货主已关闭此类提醒）' }}</text>
        </view>
      </view>
      <view v-else class="card">
        <text class="muted">本货主统一默认值，特殊商品可单独覆盖。关闭某类提醒后，商品覆盖值也暂停生效。</text>
        <view v-for="key in ['low','expiry','slow']" :key="key" class="field">
          <view class="toggle"><text class="field-label">{{ labels[key] }}</text><switch :checked="form[key + '_enabled']" :disabled="readonly" @change="form[key + '_enabled'] = $event.detail.value" /></view>
          <template v-if="key !== 'low'"><label>默认天数</label><input :class="key + '-days'" v-model="form[key + '_days']" type="number" :disabled="readonly" placeholder="1～3650" /></template>
          <text v-else class="muted">最低库存数量按商品和基本单位分别设置。</text>
        </view>
      </view>
      <view v-if="saveError" class="error">{{ saveError }}</view>
      <text v-if="saved" class="success">已保存，返回预警页后将更新结果。</text>
      <button v-if="data.can_edit && canManage" class="save" :disabled="readonly || conflict" @click="save">{{ saving ? '保存中…' : '保存设置' }}</button>
      <view v-if="!productId" class="card product-settings">
        <text class="field-label">商品阈值</text><text class="muted">包含尚无库存记录的启用商品，以及仍有库存的停用商品。</text>
        <view class="search-row"><input v-model="search" placeholder="商品名称 / 编码 / SKU" @confirm="findProducts" /><button class="product-search" size="mini" @click="findProducts">搜索</button></view>
        <text v-if="productsError" class="error">{{ productsError }}</text>
        <button v-if="productsError" size="mini" @click="findProducts">重试商品查询</button>
        <button v-for="product in products?.results || []" :key="product.id" class="product-row" @click="openProduct(product.id)"><text>{{ product.name }}</text><text class="muted">{{ product.code }} · 单位：{{ product.unit_label }} ›</text></button>
        <text v-if="products && !products.results.length" class="muted">暂无符合条件的商品</text>
        <button v-if="products?.next_page" class="more-products" size="mini" :disabled="productsLoading" @click="moreProducts(products.next_page)">加载更多商品</button>
      </view>
    </template>
  </view>
</template>
<script setup>
import { computed, reactive, ref, watch, onUnmounted } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useInventoryAccess } from '@/composables/useInventoryAccess'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useVisibleInventoryRequest } from '@/composables/useVisibleInventoryRequest'
defineOptions({ inheritAttrs: false })
const auth = useAuth()
const { canManage } = useInventoryAccess()
const productId = ref(null), search = ref(''), productQuery = ref('')
const labels = {low:'低库存',expiry:'临期／已过期',slow:'长期未动销'}
const modes = [{value:'inherit',label:'继承货主默认'}, {value:'custom',label:'自定义天数'}, {value:'off',label:'关闭'}]
const identity = computed(() => `${auth.user?.id}:${auth.user?.owner_id}:${auth.roles.join(',')}`)
const saving=ref(false), saveError=ref(''), saved=ref(false), conflict=ref(false)
const {data,error,loading,denied,refresh,retry} = useVisibleInventoryRequest(async () => {
  const result = await api.inventoryAlertSettings(productId.value)
  if (!result?.version || (productId.value && !result.product)) throw new Error('配置不完整')
  return result
}, productId, identity, saving)
const productResource = useVisibleInventoryRequest(async ({page}) => {
  if (productId.value) return {results:[],next_page:null}
  const result = await api.inventoryAlertProducts({search:productQuery.value,page,page_size:20})
  if (!Array.isArray(result?.results)) throw new Error('商品数据不完整')
  return result
}, computed(() => `${productId.value}:${productQuery.value}`), identity)
const {data:products,error:productsError,loading:productsLoading,more:moreProducts} = productResource
const form = reactive({})
let active=false, alive=true, epoch=0
const readonly=computed(() => !canManage.value || !data.value?.can_edit || loading.value || saving.value || denied.value)
watch(data, value => {
  Object.keys(form).forEach(key => delete form[key])
  if (!value) return
  for (const key of ['low_enabled','expiry_enabled','slow_enabled','expiry_days','slow_days','min_stock','expiry_mode','slow_mode']) form[key] = value[key] ?? ''
  conflict.value=false
})
watch([identity,productId], () => { epoch++; saveError.value=''; saved.value=false; conflict.value=false })
watch([denied, productResource.denied], values => {
  if (values.some(Boolean)) { data.value=null; products.value=null; denied.value=true; productResource.denied.value=true; error.value='库存预警访问权限已失效' }
})
function modeIndex(mode) { return Math.max(0,modes.findIndex(m => m.value === mode)) }
function reload() { if (!saving.value) { saved.value=false; saveError.value=''; refresh() } }
function findProducts() { if (productQuery.value === search.value.trim()) productResource.refresh(); else productQuery.value=search.value.trim() }
function openProduct(id) { uni.navigateTo({url:`/pages/inventory/alert-settings?product_id=${id}`}) }
async function save() {
  if (readonly.value || conflict.value) return
  const body={version:data.value.version}
  const keys=productId.value ? ['expiry','slow'] : ['low','expiry','slow']
  for (const key of keys) {
    if (productId.value) body[key + '_mode']=form[key + '_mode']
    else body[key + '_enabled']=Boolean(form[key + '_enabled'])
    if (key === 'low') continue
    if (!productId.value || form[key + '_mode'] === 'custom') {
      const days=Number(form[key + '_days'])
      if (!Number.isInteger(days) || days < 1 || days > 3650) { saveError.value='天数必须为 1～3650 的整数'; return }
      body[key + '_days']=days
    } else body[key + '_days']=null
  }
  if (productId.value) {
    const minimum=String(form.min_stock).trim()
    if (minimum && !/^\d{1,10}(\.\d{1,2})?$/.test(minimum)) { saveError.value='最低库存须为非负数，最多两位小数'; return }
    body.min_stock=minimum || null
  }
  const tag=epoch
  saving.value=true; saveError.value=''; saved.value=false
  try {
    await api.saveInventoryAlertSettings(productId.value,body)
    if (!alive || !active || tag !== epoch) return
    saved.value=true; refresh()
  } catch (e) {
    if (!alive || !active || tag !== epoch) return
    const status=Number(e?.statusCode || e?.status)
    if ([401,403].includes(status)) { data.value=null; denied.value=true; error.value='库存预警配置权限已失效' }
    else if (status === 409) { conflict.value=true; saveError.value='配置已被修改，请重新加载后再保存' }
    else saveError.value=e?.message || '保存失败，请重试'
  } finally { if (alive) saving.value=false }
}
onLoad(options => { productId.value=options.product_id ? Number(options.product_id) : null })
onShow(() => {active=true})
function hide() {active=false;epoch++}
onHide(hide)
function dispose() {alive=false;hide()}
onUnload(dispose);onUnmounted(dispose)
</script>
<style scoped>
.settings-page{max-width:760px;margin:auto;padding:16px;box-sizing:border-box;min-height:100vh;background:#f6f7f9;color:#1f2937}.heading,.toggle,.search-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.heading{flex-wrap:wrap;margin-bottom:16px}.heading button,.search-row button{margin:0;flex-shrink:0}.title{font-size:22px;font-weight:700}.card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:12px;margin:16px 0;font-size:14px;overflow-wrap:anywhere}.product-name{font-size:18px;font-weight:700}.muted{color:#667085;font-size:13px;line-height:1.7}.notice{display:block;color:#475569;background:#eff6ff;padding:12px;border-radius:8px;font-size:14px}.field{display:flex;flex-direction:column;gap:10px;padding:12px 0;border-top:1px solid #f1f5f9}.field-label{font-size:16px;font-weight:600}input,.picker{border:1px solid #cbd5e1;border-radius:8px;padding:10px;font-size:14px;background:#fff;height:auto;min-height:22px}.search-row input{min-width:0;flex:1}.save{background:#2563eb;color:white;font-size:16px}.save[disabled]{opacity:.5}.error{display:block;color:#b42318;background:#fff7ed;border-radius:8px;padding:10px;font-size:14px;line-height:1.7}.success{display:block;color:#047857;margin:12px 0;font-size:14px}.product-row{display:flex;flex-direction:column;align-items:flex-start;text-align:left;width:100%;font-size:14px;line-height:1.7;padding:12px;background:#f8fafc;margin:0}.product-row::after{border:0}
</style>
