<template>
  <view class="charts">
    <view class="chart-title"><text>出入库图表</text><button size="mini" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起图表' : '展开图表' }}</button></view>
    <view v-if="expanded">
      <view class="chart-toolbar" role="group" aria-label="图表选项">
        <ChartChoice label="图表" :options="tabs" :value="chart" @change="chart=$event" />
        <template v-if="!unsupported">
          <ChartChoice label="指标" :options="metrics" :value="metric" @change="metric=$event" />
          <ChartChoice v-if="dimensions.length" :label="metric === 'amount' ? '币种' : '单位'"
            :options="dimensions" :value="data.dimension.key" @change="dimension=$event" />
          <ChartChoice label="方向" :options="availableDirections" :value="direction" @change="chooseDirection" />
          <template v-if="trend">
            <ChartChoice label="周期" :options="grains" :value="grain" @change="grain=$event" />
            <ChartChoice label="图形" :options="shapes" :value="shape" @change="shape=$event" />
          </template>
          <ChartChoice v-else label="周期" :options="filters.undated ? rankGrains.slice(0, 1) : rankGrains"
            :value="rankGrain" @change="rankGrain=$event" />
        </template>
      </view>
      <text v-if="unsupported" class="status">当前筛选为{{ historyCategories[filters.category] }}，可在下方查看记录。</text>
      <template v-else>
        <view v-if="rankPeriod" class="rank-navigation">
          <button :disabled="rankPeriod.previous < filters.date_from" @click="rankDate=rankPeriod.previous">
            {{ previousLabel }}
          </button>
          <picker :key="rankGrain" mode="date" :fields="pickerFields" :value="pickerValue(anchor)"
            :start="pickerValue(filters.date_from)" :end="pickerValue(lastDate)" @change="jumpToPeriod">
            <button aria-label="选择排行时段">{{ periodTitle }} ▾</button>
          </picker>
          <button :disabled="rankPeriod.next > lastDate" @click="rankDate=rankPeriod.next">
            {{ nextLabel }}
          </button>
        </view>
        <text v-if="rankPeriod && (rankFilters.date_from !== rankPeriod.period_start || rankFilters.date_to !== rankPeriod.period_end)"
          class="status">统计范围：{{ rankFilters.date_from }} 至 {{ rankFilters.date_to }}</text>
        <view v-if="error" class="status">{{ error }} <button size="mini" @click="retry++">重试图表</button></view>
        <text v-else-if="!data" class="status">正在读取图表…</text>
        <template v-else>
          <text v-if="!data.results.length" class="status">{{ trend ? '当前条件下没有已记录时间的收发数据' : rankPeriod ? '本期没有可排行的商品' : '当前条件下没有可排行的商品' }}</text>
          <template v-else-if="trend">
            <HistoryTrendPlot :rows="data.results" :shape="shape" :grain="grain" :unit="unit" :display="display" :money="metric === 'amount'" @select="selected=$event" />
            <view v-if="selected" class="point-info"><text>{{ selected.range_start }} 至 {{ selected.range_end }} · {{ label(selected) }} {{ display(selected) }}</text><button size="mini" @click="open(selected)">查看记录 ›</button></view>
          </template>
          <view v-else class="ranks">
            <text class="status">{{ direction==='inbound' ? '入库' : '出库' }}商品前 10 名 · {{ unit }}</text>
            <button v-for="(row,index) in data.results" :key="row.product_id" class="rank" @click="open(row)">
              <view class="rank-caption"><text>{{ index+1 }}　{{ row.product_name }}</text><text>{{ display(row) }} ›</text></view>
              <view class="track"><view :class="['fill',row.category]" :style="{width:rankWidth(row)+'%'}" /></view>
            </button>
          </view>
          <text v-if="data.excluded_count" class="status">{{ data.excluded_count }} 种商品全部缺价，未列入金额排行。</text>
          <view v-if="trend && (page>1 || data.next_page)" class="switches pager">
            <button :disabled="!data.next_page" @click="page=data.next_page">更早周期</button>
            <button :disabled="page===1" @click="page--">更新周期</button>
          </view>
        </template>
      </template>
    </view>
  </view>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '@/utils/request'
import { formatQuantity } from '@/utils/quantity'
import { calendarPeriod, historyCategories, historyMoney, historyQuery, periodFilters } from '@/utils/stockHistory'
import ChartChoice from './ChartChoice.vue'
import HistoryTrendPlot from './HistoryTrendPlot.vue'

const props = defineProps({filters:Object, snapshot:String, asOf:String})
const emit = defineEmits(['records','denied'])
const tabs = [{key:'trend',label:'按期统计'},{key:'ranking',label:'商品排行'}]
const metrics = [{key:'quantity',label:'数量'},{key:'amount',label:'金额'}]
const grains = [{key:'day',label:'按天'},{key:'week',label:'按周'},{key:'month',label:'按月'},{key:'year',label:'按年'}]
const rankGrains = [{key:'all',label:'全时段'}, ...grains]
const shapes = [{key:'bars',label:'柱状图'}, {key:'line',label:'折线图'}]
const directions = [{key:'both',label:'收发对比'},{key:'inbound',label:'入库'},{key:'outbound',label:'出库'}]
const expanded = ref(true)
const chart = ref('trend')
const metric = ref('quantity')
const grain = ref('day')
const shape = ref('bars')
const rankGrain = ref('all')
const rankDate = ref('')
const trendDirection = ref('both')
const rankDirection = ref('outbound')
const dimension = ref('')
const page = ref(1)
const selected = ref(null)
const data = ref(null)
const error = ref('')
const retry = ref(0)

const trend = computed(() => chart.value === 'trend')
// The statistics API serializes as_of in Beijing time, including its UTC offset.
const lastDate = computed(() => {
  const snapshotDay = props.asOf?.slice(0, 10) || ''
  return props.filters.date_to || (props.filters.date_from > snapshotDay ? props.filters.date_from : snapshotDay)
})
watch([() => props.filters.date_from, () => props.filters.date_to], () => { rankDate.value = '' },
  { flush:'sync' })
const anchor = computed(() => {
  const date = rankDate.value || lastDate.value
  if (props.filters.date_from && date < props.filters.date_from) return props.filters.date_from
  return date > lastDate.value ? lastDate.value : date
})
const rankPeriod = computed(() => !trend.value && rankGrain.value !== 'all' && anchor.value && !props.filters.undated
  ? calendarPeriod(anchor.value, rankGrain.value) : null)
const rankFilters = computed(() => rankPeriod.value ? periodFilters(props.filters, rankPeriod.value) : props.filters)
const periodTitle = computed(() => {
  const [year, month] = anchor.value.split('-')
  if (rankGrain.value === 'year') return year + ' 年'
  if (rankGrain.value === 'month') return year + ' 年 ' + Number(month) + ' 月'
  if (rankGrain.value === 'week') return rankPeriod.value.period_start + ' 至 ' + rankPeriod.value.period_end
  return anchor.value
})
const previousLabel = computed(() => ({day:'前一天',week:'上周',month:'上月',year:'上年'})[rankGrain.value])
const nextLabel = computed(() => ({day:'后一天',week:'下周',month:'下月',year:'下年'})[rankGrain.value])
const pickerFields = computed(() => ['month','year'].includes(rankGrain.value) ? rankGrain.value : 'day')
function pickerValue(date) {
  return date?.slice(0, {year:4,month:7,day:10}[pickerFields.value]) || undefined
}
function jumpToPeriod(event) {
  const value = event.detail.value
  rankDate.value = value.length === 4 ? value + '-01-01' : value.length === 7 ? value + '-01' : value
}
const unsupported = computed(() =>
  props.filters.category && !['inbound', 'outbound'].includes(props.filters.category))
const availableDirections = computed(() => directions.filter(item => props.filters.category
  ? item.key === props.filters.category : trend.value || item.key !== 'both'))
const direction = computed(() => props.filters.category ||
  (trend.value ? trendDirection.value : rankDirection.value))
const dimensions = computed(() => (data.value?.options || []).map(item =>
  metric.value === 'amount' && item.code === 'CNY' ? {...item, label:'人民币'} : item))
const unit = computed(() => data.value?.dimension?.label === 'CNY' ? '元' : data.value?.dimension?.label)

function chooseDirection(key) {
  if (trend.value) trendDirection.value = key
  else rankDirection.value = key
}
const query = computed(() => ({
  ...historyQuery(trend.value ? props.filters : rankFilters.value),
  snapshot: props.snapshot,
  chart: chart.value,
  direction: direction.value,
  metric: metric.value,
  grain: trend.value ? grain.value : rankGrain.value,
  dimension: dimension.value,
}))
watch(query, () => { page.value = 1 }, {flush: 'sync'})
watch([query, page, expanded, retry], async ([params, currentPage, isOpen], _, onCleanup) => {
  let valid = true
  onCleanup(() => { valid = false })
  selected.value = null
  data.value = null
  error.value = ''
  if (!isOpen || !params.snapshot || unsupported.value) return
  try {
    const result = await api.stockStatistics('chart', {...params, page: currentPage})
    if (valid) data.value = result
  } catch (e) {
    if (!valid) return
    if ([401, 403, 409].includes(Number(e?.statusCode || e?.status))) emit('denied')
    else error.value = e?.message || '图表读取失败'
  }
}, {immediate: true})
function label(row) {
  if (metric.value === 'amount') return row.category === 'inbound' ? '采购金额' : '销售金额'
  return row.category === 'inbound' ? '入库数量' : '出库数量'
}
function display(row) {
  if(row.value==null) return '金额待核对'
  const amount=metric.value==='amount'
  return (amount ? historyMoney(row.value) : formatQuantity(row.value))+' '+unit.value+
    (amount && row.amount_status==='PARTIAL' ? '（已知，部分待核对）' : '')
}
function rankWidth(row) { return 100*Number(row.value)/(Number(data.value.results[0].value)||1) }
function open(row) {
  const filters=trend.value ? periodFilters(props.filters,row) : {...rankFilters.value,grain:'all',product_id:row.product_id}
  const code=data.value.dimension.code
  if(metric.value==='amount') filters.currency=code
  else if(code) filters.base_unit=code
  else filters.missing_unit=true
  emit('records',{...filters,category:row.category,snapshot:data.value.snapshot})
}
</script>
<style scoped>
.charts { padding:16px; margin:14px 0; background:#fff; border:1px solid #dce5ed; border-radius:12px; color:#1d3347; }
.chart-title,.point-info,.rank-caption { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.chart-title {font-size:18px;font-weight:650}
button { margin:0; font-size:13px; line-height:1.5; padding:6px 10px; border-radius:6px; color:#486078; background:#f2f5f8; }
button::after { border:0; }.switches {display:flex; align-items:center; flex-wrap:wrap;gap:5px;margin-top:9px}
.chart-toolbar { display:flex; flex-wrap:wrap; align-items:flex-start; gap:6px; margin-top:8px; }
.rank-navigation { display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-top:12px; }
.rank-navigation button[disabled] { color:#8794a1; }
.status {display:block;color:#637588;font-size:13px;margin-top:12px}.point-info {margin-top:12px;padding:10px;background:#f3f6f9;font-size:13px;flex-wrap:wrap}
.rank {display:block;background:white;width:100%;text-align:left;padding:9px 0}.rank-caption {align-items:baseline;overflow-wrap:anywhere}.rank-caption text:last-child {text-align:right}
.track {height:5px;border-radius:10px;background:#edf1f5;margin-top:5px;overflow:hidden}.fill {height:100%;background:#197251}.fill.outbound {background:#3468b7}.pager{justify-content:flex-end}
</style>
