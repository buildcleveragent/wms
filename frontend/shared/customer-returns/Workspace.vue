<template>
  <view class="returns-page">
    <view class="heading"><text class="title">{{ warehouse ? '客户退货验收' : '退货与异常' }}</text><button size="mini" :disabled="loading || busy || denied" @click="refresh">刷新</button></view>
    <text class="muted">{{ warehouse ? '按原订单验收 · 合格与残次分别入账' : '关联原销售订单 · 仅退回原发货仓库 · 本页不处理退款' }}</text>
    <view v-if="error" class="error"><text>{{ error }}</text><button v-if="!denied" size="mini" @click="refresh">重试</button></view>
    <text v-if="success" class="success">{{ success }}</text>
    <text v-if="loading && !data" class="empty">正在加载退货信息…</text>
    <text v-if="updated" class="muted">最近成功更新：{{ updated }}</text>

    <template v-if="!recordId && !originalId">
      <view class="filters"><input v-model="search" placeholder="退货单 / 原订单 / 商品" @confirm="filter" /><button size="mini" @click="filter">搜索</button></view>
      <picker :range="statuses" range-key="label" :value="statusIndex" @change="statusIndex = Number($event.detail.value); filter()"><view class="select">{{ statuses[statusIndex].label }} ▾</view></picker>
      <text v-if="!warehouse" class="hint">请从原订单详情进入“申请退货”。</text>
      <text v-if="data && !data.results?.length" class="empty">暂无退货申请</text>
      <view v-for="item in data?.results || []" :key="item.id" class="card" @click="open(item.id)">
        <view class="heading"><text class="name">{{ item.number }}</text><text class="badge">{{ item.status_label }}</text></view>
        <text>原订单：{{ item.original_order_no }}</text><text>客户：{{ item.customer || '未记录' }}</text><text>仓库：{{ item.warehouse }}</text><text class="muted">{{ time(item.created_at) }} · 查看详情 ›</text>
      </view>
      <button v-if="data?.next" :disabled="loading" @click="load(page + 1)">加载更多</button>
    </template>

    <template v-else-if="data">
      <view class="card"><text class="name">{{ data.number || '申请退货' }}</text><text v-if="data.status_label" class="badge">{{ data.status_label }}</text><text>原订单：{{ data.original_order_no }}</text><text>客户：{{ data.customer || '未记录' }}</text><text>退入仓库：{{ data.warehouse }}</text></view>
      <template v-if="originalId || editing">
        <text class="hint">数量按基本单位填写；提交时会重新校验剩余可退数量。</text>
        <view v-for="(line, i) in form" :key="line.original_line_id" class="card">
          <text class="name">{{ line.product_name }}</text><text class="muted">{{ line.original_line_no ? `原单行 ${line.original_line_no} · ` : '' }}{{ line.product_code }} · 规格：{{ line.spec || '未记录' }} · 单位：{{ line.unit_label || line.unit }}</text>
          <text v-if="!editing">实际履约 {{ formatQuantity(line.fulfilled_qty) }} · 已退 {{ formatQuantity(line.returned_qty) }}</text><text v-if="!editing">处理中 {{ formatQuantity(line.pending_qty) }} · 剩余可退 {{ formatQuantity(line.available_qty) }}</text>
          <text v-if="!editing && line.reason" class="warning">{{ line.reason }}</text>
          <label>申请数量<input v-model="line.qty" type="digit" :disabled="!editing && !line.can_apply" placeholder="0" /></label>
          <picker :range="reasons" range-key="label" :value="reasonIndex(line.reason_code)" @change="line.reason_code = reasons[Number($event.detail.value)].code"><view class="select">退货原因：{{ reasons[reasonIndex(line.reason_code)].label }} ▾</view></picker>
          <textarea v-model="line.reason_text" maxlength="500" placeholder="填写具体原因（必填）" />
          <text v-if="['DAMAGE','QUALITY','WRONG'].includes(line.reason_code)" class="warning">此原因至少需要一张照片；先保存草稿，再上传。</text>
          <view v-if="line.id" class="buttons"><button size="mini" :disabled="busy" @click="upload(line, 'APPLICATION')">上传申请照片</button><button v-for="photo in line.photos || []" :key="photo.id" size="mini" @click="preview(photo)">查看照片 {{ photo.id }}</button><button v-for="photo in (line.photos || []).filter(p => !p.sealed)" :key="`del${photo.id}`" size="mini" :disabled="busy" @click="removePhoto(line, photo)">删除照片 {{ photo.id }}</button></view>
        </view>
        <view class="buttons"><button class="primary" :disabled="busy || denied" @click="save">{{ busy ? '正在保存…' : '保存草稿' }}</button><button v-if="editing" :disabled="busy" @click="editing = false">返回详情</button></view>
      </template>
      <template v-else>
        <view class="buttons">
          <button v-for="operation in data.allowed_actions.filter(a => !['receive','retry-posting','note'].includes(a))" :key="operation" :disabled="busy || denied" :class="['submit','owner-approve','warehouse-confirm'].includes(operation) ? 'primary' : ''" @click="operation === 'edit' ? startEdit() : act(operation)">{{ labels[operation] }}</button>
        </view>
        <view v-for="line in data.lines" :key="line.id" class="card">
          <text class="name">{{ line.product_name }}</text><text class="muted">{{ line.original_line_no ? `原单行 ${line.original_line_no} · ` : '' }}{{ line.product_code }} · 规格：{{ line.spec || '未记录' }} · 单位：{{ line.unit_label || line.unit }}</text>
          <view class="numbers"><text>申请 {{ formatQuantity(line.qty) }}</text><text>已验收 {{ formatQuantity(line.received_qty) }}</text><text>可再售 {{ formatQuantity(line.qty_ok) }}</text><text>残次 {{ formatQuantity(line.qty_damage) }}</text><text>拒收 {{ formatQuantity(line.qty_reject) }}</text><text>待到货 {{ formatQuantity(line.pending_qty) }}</text></view>
          <text>原因：{{ line.reason }}</text><view class="buttons"><button v-for="photo in line.photos" :key="photo.id" size="mini" @click="preview(photo)">申请照片 {{ photo.id }}</button></view>
        </view>
        <template v-if="data.allowed_actions.includes('receive')">
          <text class="section">登记本次到货</text><text class="hint">本次已验收＝合格＋残次＋拒收。可以分批、分批次登记。</text>
          <view v-for="(row, index) in receiptForm" :key="row.key" class="card">
            <picker :range="receiptChoices" range-key="choice_label" :value="row.lineIndex" @change="row.lineIndex = Number($event.detail.value); selectReceiptPhotos(row)"><view class="select">{{ receiptChoices[row.lineIndex]?.choice_label }} ▾</view></picker>
            <text>单位：{{ data.lines[row.lineIndex]?.unit_label || data.lines[row.lineIndex]?.unit }}</text>
            <picker :range="locations" range-key="code" :value="row.locationIndex" @change="row.locationIndex = Number($event.detail.value)"><view class="select">库位：{{ locations[row.locationIndex]?.code || '请选择库位' }} ▾</view></picker>
            <label>合格数量<input v-model="row.qty_ok" type="digit" /></label><label>残次数量<input v-model="row.qty_damage" type="digit" /></label><label>拒收数量<input v-model="row.qty_reject" type="digit" /></label>
            <label>批次<input v-model="row.batch_no" placeholder="按实物批次填写" /></label><label>生产日期<input v-model="row.production_date" placeholder="YYYY-MM-DD（未记录可留空）" /></label><label>到期日<input v-model="row.expiry_date" placeholder="YYYY-MM-DD（未记录可留空）" /></label><label>序列号<input v-model="row.serial_no" placeholder="序列号商品必须逐件验收" /></label>
            <textarea v-model="row.reason" maxlength="500" placeholder="残次或拒收原因（异常时必填）" />
            <view class="buttons"><button size="mini" :disabled="busy" @click="upload(data.lines[row.lineIndex], 'RECEIPT', row)">验收照片（{{ row.photo_ids.length }}）</button><button size="mini" @click="receiptForm.splice(index, 1)">移除此行</button><template v-for="photo in row.photo_objects" :key="photo.id"><button size="mini" @click="preview(photo)">查看照片 {{ photo.id }}</button><button size="mini" :disabled="busy" @click="removeReceiptPhoto(row,photo)">删除照片 {{ photo.id }}</button></template></view>
          </view>
          <view class="buttons"><button :disabled="busy" @click="addReceipt">增加验收行</button><button class="primary" :disabled="busy || !receiptForm.length" @click="receive">确认本次验收并入账</button></view>
        </template>
        <text class="section">分批验收与库存回补</text><text v-if="!data.receipts.length" class="empty">仓库尚未登记验收</text>
        <view v-for="receipt in data.receipts" :key="receipt.id" class="card">
          <text class="name">{{ time(receipt.created_at) }} · {{ receipt.actor }}</text><text :class="receipt.posted_at ? 'success' : 'warning'">{{ receipt.posting_label }}</text><text v-if="receipt.posted_at" class="muted">入账时间：{{ time(receipt.posted_at) }}</text>
          <view v-for="row in receipt.lines" :key="row.id" class="batch"><text>{{ row.product_name }} · 单位：{{ row.unit }}</text><text>合格 {{ formatQuantity(row.qty_ok) }} · 残次 {{ formatQuantity(row.qty_damage) }} · 拒收 {{ formatQuantity(row.qty_reject) }}</text><text>库位 {{ row.location }} · 批次 {{ row.batch_no || '未记录' }}</text><text>生产日期 {{ row.production_date || '未记录' }} · 到期 {{ row.expiry_date || '未记录' }}</text><text v-if="row.serial_no">序列号：{{ row.serial_no }}</text><text>说明：{{ row.reason || '无异常说明' }}</text><view class="buttons"><button v-for="photo in row.photos" :key="photo.id" size="mini" @click="preview(photo)">验收照片 {{ photo.id }}</button></view></view>
          <button v-if="!receipt.posted_at && data.allowed_actions.includes('retry-posting')" :disabled="busy" @click="retryPosting(receipt.id)">重试库存入账</button>
        </view>
        <text class="section">处理记录</text><view class="card timeline"><view v-for="item in data.events" :key="item.id" class="event"><text class="name">{{ item.label }}</text><text class="muted">{{ time(item.occurred_at) }} · {{ item.actor }}</text><text v-if="item.reason">{{ item.reason }}</text><view v-if="['submit','edit'].includes(item.action)" class="muted"><view v-for="line in item.lines || []" :key="line.id"><text>{{ line.product_name }} · {{ formatQuantity(line.qty) }} {{ line.unit }} · {{ line.reason }}</text><button v-for="photoId in line.photo_ids || []" :key="photoId" size="mini" @click="preview({url:url('photos/'+photoId+'/')})">当时的照片 {{ photoId }}</button></view></view></view></view>
        <view v-if="data.allowed_actions.includes('note')" class="card"><textarea v-model="note" maxlength="500" placeholder="补充处理说明，保存后保留记录" /><button :disabled="busy || !note.trim()" @click="act('note', note)">追加说明</button><button v-if="data.lines.length" size="mini" :disabled="busy" @click="upload(data.lines[0], 'NOTE')">追加证据照片</button></view>
        <view class="buttons"><button v-for="photo in data.photos" :key="photo.id" size="mini" @click="preview(photo)">补充照片 {{ photo.id }}</button></view>
      </template>
    </template>
  </view>
</template>
<script setup>
import { formatQuantity } from '../quantity'
import { computed, ref, watch, onUnmounted } from 'vue'
const props = defineProps({ transport: {type: Object, required: true}, identity: String, visible: Boolean, recordId: [Number, String], originalId: [Number, String], filterOrder: [Number, String], warehouse: Boolean })
const receiptChoices = computed(()=>(data.value?.lines || []).map((line,index)=>({...line,choice_label:`${line.product_name} · 原单行 ${line.original_line_no || index+1}`})))
const emit = defineEmits(['open'])
const root = '/api/inbound/customer-returns/'
const data = ref(null), loading = ref(false), busy = ref(false), error = ref(''), success = ref(''), denied = ref(false), updated = ref('')
const search = ref(''), statusIndex = ref(0), editing = ref(false), form = ref([]), note = ref(''), locations = ref([]), receiptForm = ref([])
let formVersion = null
let appliedSearch = '', page = 1, generation = 0, alive = true, queued = false, writeKey = null
const statuses = [{label:'全部状态',value:''}, ...Object.entries({OWNER_PENDING:'待货主审核',WHS_PENDING:'待仓库确认',REJECTED:'被退回',RECEIVING:'待到货',PARTIAL:'部分验收',POSTING:'待入账',COMPLETED:'已完成',CLOSED:'已关闭剩余数量',DRAFT:'草稿',CANCELLED:'已取消'}).map(([value,label])=>({value,label}))]
const reasons = [{code:'UNWANTED',label:'不再需要'},{code:'DAMAGE',label:'破损'},{code:'QUALITY',label:'质量问题'},{code:'WRONG',label:'错货'},{code:'OTHER',label:'其他'}]
const labels = {edit:'修改申请 / 上传照片',submit:'提交申请',withdraw:'撤回申请',cancel:'取消申请','owner-approve':'审核通过','owner-reject':'退回修改','warehouse-confirm':'确认接收','warehouse-reject':'退回申请',close:'关闭剩余数量',claim:'领取验收'}
const time = value => value ? new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '时间未记录'
const reasonIndex = code => Math.max(reasons.findIndex(r=>r.code===code),0)
const active = () => alive && props.visible && !denied.value && (typeof document === 'undefined' || !document.hidden)
const url = suffix => `${root}${props.recordId}/${suffix || ''}`
const open = id => emit('open',id)
function message(e) { return e?.message || e?.data?.detail || '操作失败，请重试' }
function failure(e, reading=false) {
  if ([401,403].includes(Number(e?.statusCode || e?.status || e?.code)) || (props.recordId && data.value && Number(e?.statusCode || e?.code) === 404)) { data.value=null; form.value=[]; receiptForm.value=[]; denied.value=true; error.value='退货访问权限已失效'; return }
  error.value=reading ? (data.value ? '更新失败，当前为上次数据' : '退货功能暂不可用，请重试') : message(e)
}
async function load(next=1) {
  if (!active()) return
  if (loading.value || busy.value) { queued=true; return }
  const tag=generation; loading.value=true; error.value=''; queued=false
  try {
    let result
    if (props.recordId) result=await props.transport.request({url:url(''),silent:true})
    else if (props.originalId) result=await props.transport.request({url:`/api/outbound/orders/${props.originalId}/return-eligibility/`,silent:true})
    else result=await props.transport.request({url:root,data:{search:appliedSearch,status:statuses[statusIndex.value].value,page:next,page_size:20,...(props.filterOrder ? {original_order_id:props.filterOrder} : {})},silent:true})
    if (!active() || tag!==generation) return
    if (!result || (props.recordId && (!Array.isArray(result.lines) || !Array.isArray(result.allowed_actions))) || (props.originalId && !Array.isArray(result.lines)) || (!props.recordId && !props.originalId && !Array.isArray(result.results))) throw new Error('响应不完整')
    if (next>1) result.results=Array.from(new Map([...(data.value?.results || []),...result.results].map(r=>[r.id,r])).values())
    data.value=result; page=next; updated.value=time(new Date().toISOString())
    if (props.originalId && !form.value.length) form.value=result.lines.map(l=>({...l,qty:'',reason_code:'UNWANTED',reason_text:''}))
    if (result.allowed_actions?.includes('receive')) {
      const options=await props.transport.request({url:url('receipt-options/'),silent:true})
      if (active() && tag===generation) locations.value=options.locations
    }
  } catch(e) { if(active() && tag===generation) failure(e,true) }
  finally { loading.value=false; if(queued && active()) load(1) }
}
function refresh() { generation++; load(1) }
function filter() { appliedSearch=search.value; generation++; data.value=null; load(1) }
function startEdit() { formVersion=data.value.version; form.value=data.value.lines.map(l=>({...l,qty:formatQuantity(l.qty),reason_text:l.reason})); editing.value=true }
function uuid() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const n=Math.floor(Math.random()*16);return(c==='x'?n:(n&3)|8).toString(16)}) }
async function write(path, body, method='POST', completed=null) {
  if (busy.value || loading.value || !active()) return
  const signature=JSON.stringify([path,body,method])
  if(writeKey?.signature!==signature) writeKey={signature,id:uuid()}
  const tag=generation; const identity=props.identity; busy.value=true; error.value=''; success.value=''
  try {
    const result=await props.transport.request({url:path,method,data:{...body,request_id:writeKey.id},silent:true})
    if(!active() || tag!==generation || identity!==props.identity) return
    writeKey=null; success.value='操作已保存'; if(completed) completed(result); else {data.value=result;updated.value=time(new Date().toISOString())}
  } catch(e) { if(active() && tag===generation && identity===props.identity) failure(e) }
  finally {busy.value=false; if(queued && active()) load(1)}
}
function save() {
  if(form.value.some(line=>String(line.qty).trim() && !/^(?:\d+(?:\.\d{0,4})?|\.\d{1,4})$/.test(String(line.qty).trim()))) {error.value='数量必须为非负数字，最多保留四位小数';return}
  const lines=form.value.filter(l=>Number(l.qty)>0).map(l=>({...(l.id?{id:l.id}:{}),original_line_id:l.original_line_id,qty:l.qty,reason_code:l.reason_code,reason:l.reason_text}))
  if(!lines.length || lines.some(l=>!l.reason.trim())) {error.value='请填写退货数量和具体原因';return}
  write(props.recordId ? url('') : root,{...(props.recordId?{version:formVersion}:{original_order_id:Number(props.originalId)}),lines},props.recordId?'PATCH':'POST',result=>{if(props.recordId){data.value=result;editing.value=false}else open(result.id)})
}
async function act(operation, text='') {
  if(['owner-reject','warehouse-reject','close','withdraw','cancel'].includes(operation)) {
    const choice=await new Promise(resolve=>uni.showModal({title:labels[operation],editable:true,placeholderText:'填写原因',success:resolve,fail:()=>resolve({confirm:false})}))
    if(!choice.confirm) return
    text=choice.content?.trim(); if(!text){error.value='请填写原因';return}
  }
  await write(url(`actions/${operation}/`),{version:data.value.version,reason:text})
  if(operation==='claim' && !error.value) refresh()
  if(operation==='note' && !error.value) note.value=''
}
function selectReceiptPhotos(row) {
  const used = new Set(receiptForm.value.filter(r=>r!==row).flatMap(r=>r.photo_ids))
  row.photo_objects=(data.value.pending_photos || []).filter(p=>p.line_id===data.value.lines[row.lineIndex]?.id && !used.has(p.id))
  row.photo_ids=row.photo_objects.map(p=>p.id)
}
function addReceipt() {const row={key:uuid(),lineIndex:0,locationIndex:0,qty_ok:'0',qty_damage:'0',qty_reject:'0',batch_no:'',production_date:'',expiry_date:'',serial_no:'',reason:'',photo_ids:[],photo_objects:[]};selectReceiptPhotos(row);receiptForm.value.push(row)}
async function removeReceiptPhoto(row,photo) {
  const holder={photos:row.photo_objects}
  await removePhoto(holder,photo)
  row.photo_objects=holder.photos;row.photo_ids=holder.photos.map(p=>p.id)
  if(!error.value)data.value.pending_photos=(data.value.pending_photos || []).filter(p=>p.id!==photo.id)
}
function receive() {
  const lines=receiptForm.value.map(r=>({line_id:data.value.lines[r.lineIndex]?.id,location_id:locations.value[r.locationIndex]?.id,qty_ok:r.qty_ok || '0',qty_damage:r.qty_damage || '0',qty_reject:r.qty_reject || '0',batch_no:r.batch_no,production_date:r.production_date || null,expiry_date:r.expiry_date || null,serial_no:r.serial_no,reason:r.reason,photo_ids:r.photo_ids}))
  if(lines.some(l=>!l.location_id)){error.value='请选择验收库位';return}
  write(url('receipts/'),{version:data.value.version,lines},'POST',result=>{data.value=result;receiptForm.value=[]})
}
function retryPosting(id) {write(url(`receipts/${id}/retry/`),{version:data.value.version})}
async function upload(line,purpose,row=null) {
  if(busy.value || !active()) return
  const tag=generation, identity=props.identity
  busy.value=true
  try {
    const files=await new Promise((resolve,reject)=>uni.chooseImage({count:1,sizeType:['compressed'],success:resolve,fail:reject}))
    if(tag!==generation || identity!==props.identity || !active()) return
    busy.value=true; error.value=''
    const photo=await props.transport.upload({url:url('photos/'),filePath:files.tempFilePaths[0],formData:{line_id:line.id,purpose}})
    if(tag!==generation || identity!==props.identity || !active()) return
    if(row) {row.photo_ids.push(photo.id);row.photo_objects.push(photo);data.value.pending_photos ||= [];data.value.pending_photos.push(photo)}
    else if(purpose==='APPLICATION') {line.photos ||= [];line.photos.push(photo)}
    else data.value.photos.push(photo)
  } catch(e) {if(tag===generation && active()) failure(e)}
  finally {busy.value=false; if(queued && active()) load(1)}
}
async function removePhoto(line,photo) {
  const tag=generation;busy.value=true
  try {await props.transport.request({url:photo.url,method:'DELETE',silent:true});if(tag===generation && active()) line.photos=line.photos.filter(p=>p.id!==photo.id)}catch(e){if(tag===generation && active()) failure(e)}finally{busy.value=false}
}
async function preview(photo) {
  const tag=generation
  try {const source=await props.transport.image(photo.url);if(tag===generation && active()) uni.previewImage({urls:[source]})}catch(e){if(tag===generation && active()) failure(e)}
}
watch(()=>[props.recordId,props.originalId,props.filterOrder,props.identity],()=>{generation++;data.value=null;form.value=[];receiptForm.value=[];editing.value=false;locations.value=[];denied.value=false;error.value='';success.value='';updated.value='';note.value='';writeKey=null;load()})
watch(()=>props.visible,value=>{generation++;if(value)load();else queued=false},{immediate:true})
function visibility(){generation++;if(active())load()}
if(typeof document!=='undefined')document.addEventListener('visibilitychange',visibility)
onUnmounted(()=>{alive=false;generation++;if(typeof document!=='undefined')document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.returns-page{box-sizing:border-box;max-width:900px;margin:0 auto;padding:20px 16px 48px;background:#f5f7fb;min-height:100vh;color:#1e293b;font-size:15px;line-height:1.6}.returns-page text{display:block;overflow-wrap:anywhere}.heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.title{font-size:23px;font-weight:700}.name{font-weight:650}.section{font-size:18px;font-weight:700;margin:24px 0 10px}.muted{color:#64748b;font-size:13px}.card{display:flex;flex-direction:column;gap:9px;background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:17px;margin:14px 0}.badge{color:#1d4ed8;background:#eff6ff;border-radius:6px;padding:3px 8px;font-size:13px;flex-shrink:0}.buttons{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.buttons button,.heading button{margin:0;font-size:14px}.primary{background:#2563eb;color:#fff}.numbers{display:grid;grid-template-columns:1fr 1fr;gap:8px;background:#f8fafc;border-radius:8px;padding:12px}.hint{color:#475569;margin:12px 0;font-size:13px}.error{color:#b91c1c;background:#fff1f2;padding:12px;border-radius:8px;margin:12px 0}.warning{color:#b45309}.success{color:#15803d}.empty{padding:20px 0;color:#64748b}.filters{display:flex;gap:8px;margin-top:18px}.filters input{flex:1}.select,input,textarea{box-sizing:border-box;border:1px solid #cbd5e1;border-radius:7px;padding:10px;background:#fff;font-size:15px;min-height:43px;height:auto;width:100%}textarea{height:90px}.select{margin:6px 0}label{display:block;color:#475569}.event{border-left:2px solid #bfdbfe;padding:0 0 18px 15px}.event:last-child{padding-bottom:0}.batch{border-top:1px solid #e2e8f0;padding-top:10px}.returns-page button[disabled]{opacity:.55}@media(max-width:480px){.returns-page{padding:16px 12px 35px}.title{font-size:21px}.card{padding:14px}.heading{align-items:flex-start}.card .heading{flex-direction:column;gap:5px}}
</style>
