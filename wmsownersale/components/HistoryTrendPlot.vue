<template>
  <view class="trend-plot">
    <view class="scale"><text>最高 {{ peak ? display(peak) : '—' }}</text><text>{{ unit }}</text></view>
    <scroll-view scroll-x class="plot-scroll">
      <view :style="{minWidth: groups.length * 70 + 'px'}">
        <view class="plot-area">
          <view v-for="segment in segments" :key="segment.key" :class="['segment',segment.category]" :style="segment.style" />
          <button v-for="point in points" :key="point.key" :aria-label="point.label"
            :class="['point', point.row.category, shape, {unknown:point.row.value == null, partial:money && point.row.amount_status === 'PARTIAL'}]"
            :style="point.style" @click="$emit('select',point.row)">
            <text v-if="point.row.value == null">?</text>
          </button>
        </view>
        <view class="ticks"><text v-for="group in groups" :key="group">{{ tick(group) }}</text></view>
      </view>
    </scroll-view>
    <view class="legend">
      <text v-for="category in categories" :key="category" :class="category">{{ category === 'inbound' ? '● 入库' : '● 出库' }}</text>
      <text v-if="money && rows.some(r => r.amount_status === 'PARTIAL')">虚线描边：部分金额待核对</text>
      <text v-if="points.some(p => p.row.value == null)">? 金额待核对</text>
    </view>
  </view>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({
  rows:Array, shape:String, grain:String, unit:String, display:Function, money:Boolean,
})
defineEmits(['select'])
const groups = computed(() => [...new Set(props.rows.map(r => r.period_start))])
const categories = computed(() => ['inbound','outbound'].filter(c => props.rows.some(r => r.category === c)))
// Only drawing coordinates use floating point. Labels retain the API decimal strings.
const peak = computed(() => {
  const priced = props.rows.filter(row => row.value != null)
  // Align decimal points before comparing unsigned chart values, including amounts above 2^53.
  const width = Math.max(0, ...priced.map(row => row.value.split('.')[0].length))
  const key = row => {
    const [integer, fraction = ''] = row.value.split('.')
    return integer.padStart(width, '0') + '.' + fraction.replace(/0+$/, '')
  }
  return priced.reduce((best, row) => !best || key(row) > key(best) ? row : best, null)
})
function tick(start) {
  if (props.grain === 'year') return start.slice(0,4)
  if (props.grain === 'month') return start.slice(0,7)
  return start
}
const points = computed(() => props.rows.map(row => {
  const index = groups.value.indexOf(row.period_start)
  const shift = categories.value.length === 1 ? 0 : row.category === 'inbound' ? -0.13 : 0.13
  const x = (index + 0.5 + shift) * 100 / groups.value.length
  const y = Number(row.value) / (Number(peak.value?.value) || 1) * 92
  const bar = props.shape === 'bars' && row.value != null
  const style = {left:x+'%',bottom:bar ? '0' : y+'%',height:bar ? 'max(3px,' + y + '%)' : undefined}
  return {row,index,x,y,style,key:row.period_start+row.category,
    label:tick(row.period_start)+' '+(row.category === 'inbound' ? '入库 ' : '出库 ')+props.display(row)}
}))
const segments = computed(() => {
  if (props.shape !== 'line') return []
  const lines = [], previous = {}
  for (const point of points.value) {
    const category = point.row.category, last = previous[category]
    if (point.row.value != null && last?.row.value != null && point.index === last.index + 1) {
      const a = (100-last.y)+'%', b = (100-point.y)+'%'
      lines.push({key:point.key,category,style:{left:last.x+'%',width:(point.x-last.x)+'%',
        clipPath:'polygon(0 calc('+a+' - 1px),100% calc('+b+' - 1px),100% calc('+b+' + 1px),0 calc('+a+' + 1px))'}})
    }
    previous[category] = point
  }
  return lines
})
</script>
<style scoped>
.plot-scroll { width:100%; }
.scale,.legend,.ticks { display:flex; font-size:12px; color:#53667a; }
.scale { justify-content:space-between; margin:14px 0 6px; }
.plot-area { position:relative; height:142px; margin:0 0 6px; border-bottom:1px solid #aab9c8; background:repeating-linear-gradient(to top,transparent 0,transparent 34px,#e2e8ed 34px,#e2e8ed 35px); }
.point,.segment { position:absolute; background:#197251; }
.outbound { background:#3468b7; }
.point { width:16px; min-width:0; padding:0; margin:0; transform:translateX(-50%); border-radius:4px 4px 0 0; overflow:visible; }
.point.partial {outline:1px dashed #874b19; outline-offset:1px;}
.point::after { border:0; inset:-5px; width:auto; height:auto; transform:none; }
.point.line,.point.unknown { width:13px; height:13px; border-radius:50%; transform:translate(-50%,50%); }
.point.unknown { bottom:0; background:white; border:1px solid #ab6631; color:#834014; font-size:11px; line-height:13px; }
.segment { top:0; bottom:0; pointer-events:none; }
.ticks text { flex:1; text-align:center; font-size:11px; }
.legend { gap:14px; margin-top:8px; flex-wrap:wrap; }
.legend .inbound { color:#197251; }.legend .outbound { color:#3468b7; background:none; }
</style>
