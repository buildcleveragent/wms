<template>
  <view ref="root" class="name-select">
    <input
      class="name-input" :value="opened ? keyword : selectedLabel" :disabled="disabled"
      :placeholder="allLabel" :aria-label="`${entityName}名称`" role="combobox" aria-autocomplete="list"
      :aria-expanded="opened" :aria-controls="optionListId"
      :aria-activedescendant="opened ? `${optionListId}-${active}` : undefined"
      @focus="open" @click="open" @input="type" @keydown="keyDown"
    />
    <text class="name-arrow" aria-hidden="true" @click="open">▾</text>
    <view v-if="opened" class="name-dropdown">
      <view :id="optionListId" ref="list" class="name-options" role="listbox" :aria-label="`${entityName}选项`">
        <view v-for="(item, index) in choices" :id="`${optionListId}-${index}`" :key="item.id"
          :class="['name-option', { active: active === index }]" role="option"
          :aria-selected="String(modelValue) === String(item.id)" @mousedown.prevent @click="choose(item)">
          {{ label(item) }}
        </view>
      </view>
      <view v-if="loading" class="name-state">加载{{ entityName }}中…</view>
      <view v-else-if="error" class="name-state">{{ error }}<button size="mini" @click="fetchOptions">重试</button></view>
      <view v-else-if="choices.length === 1 + additionalOptions.length && keyword" class="name-state">无匹配{{ entityName }}</view>
    </view>
  </view>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
const props = defineProps({ modelValue: { default: '' }, loadPage: { type: Function, required: true }, disabled: Boolean, preload: Boolean, additionalOptions: { type: Array, default: () => [] }, entityName: { type: String, default: '货主' }, listId: { type: String, required: true } })
const optionListId = computed(() => props.listId)
const allLabel = computed(() => `全部${props.entityName}`)
const emit = defineEmits(['update:modelValue', 'options-state'])
const root = ref(null), list = ref(null), opened = ref(false), keyword = ref(''), active = ref(0)
const options = ref([]), loading = ref(false), loaded = ref(false), error = ref('')
let disposed = false
const duplicateNames = computed(() => {
  const counts = new Map()
  options.value.forEach(item => counts.set(item.name, (counts.get(item.name) || 0) + 1))
  return counts
})
function label(item) { if (item.id === '') return allLabel.value; return duplicateNames.value.get(item.name) > 1 ? `${item.name}（${item.code}）` : item.name }
const selectedLabel = computed(() => {
  const item = [...props.additionalOptions, ...options.value].find(row => String(row.id) === String(props.modelValue))
  return item ? label(item) : allLabel.value
})
const choices = computed(() => [{ id: '', name: allLabel.value }, ...props.additionalOptions, ...options.value.filter(item => item.name.toLocaleLowerCase().includes(keyword.value.trim().toLocaleLowerCase()))])
async function fetchOptions() {
  if (loading.value) return
  loading.value = true; error.value = ''
  try {
    const collected = new Map()
    let page = 1
    while (!disposed) {
      const result = await props.loadPage('', page)
      if (disposed) return
      const rows = Array.isArray(result) ? result : result?.results || []
      const before = collected.size
      rows.forEach(item => collected.set(item.id, item))
      options.value = [...collected.values()]
      if (!result?.next) break
      if (collected.size === before) throw new Error(`${props.entityName}分页数据异常`)
      page += 1
    }
    loaded.value = true
  } catch { if (!disposed) error.value = `${props.entityName}加载失败，请重试` }
  finally { if (!disposed) loading.value = false }
}
watch([options, loading, error], () => emit('options-state', {
  items: options.value.map(item => ({ ...item, label: label(item) })),
  loading: loading.value, error: error.value,
}))
defineExpose({ reload: fetchOptions })
function open() {
  if (props.disabled || opened.value) return
  keyword.value = ''; active.value = 0; opened.value = true
  if (!loaded.value) fetchOptions()
}
function close() { opened.value = false; keyword.value = '' }
function choose(item) { emit('update:modelValue', item.id); close() }
function type(event) { keyword.value = event.detail?.value ?? event.target.value; active.value = 0 }
function outside(event) {
  const element = root.value?.$el || root.value
  if (opened.value && !element?.contains(event.target)) close()
}
async function keyDown(event) {
  if (event.key === 'Escape' || event.key === 'Tab') { close(); return }
  if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return
  event.preventDefault()
  if (!opened.value) { open(); return }
  if (event.key === 'Enter') { choose(choices.value[active.value] || choices.value[0]); return }
  active.value = (active.value + (event.key === 'ArrowDown' ? 1 : -1) + choices.value.length) % choices.value.length
  await nextTick()
  const element = list.value?.$el || list.value
  element?.children[active.value]?.scrollIntoView({ block: 'nearest' })
}
onMounted(() => { if (props.preload) fetchOptions(); if (typeof document !== 'undefined') document.addEventListener('pointerdown', outside) })
onBeforeUnmount(() => { disposed = true; if (typeof document !== 'undefined') document.removeEventListener('pointerdown', outside) })
</script>

<style scoped>
.name-select { position: relative; min-width: 0; }
.name-input { box-sizing: border-box; height: 36px; padding: 0 28px 0 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; background: white; }
.name-arrow { position: absolute; right: 10px; top: 8px; color: #64748b; cursor: pointer; }
.name-dropdown { position: absolute; z-index: 30; top: calc(100% + 4px); left: 0; width: 100%; min-width: 0; max-width: calc(100vw - 24px); box-sizing: border-box; background: white; border: 1px solid #cbd5e1; border-radius: 4px; box-shadow: 0 4px 12px #0002; }
.name-options { max-height: min(280px, 40vh); overflow-y: auto; }
.name-option { padding: 10px; font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; cursor: pointer; }
.name-option.active, .name-option:hover { background: #eff6ff; color: #1677ff; }
.name-state { padding: 10px; font-size: 13px; color: #64748b; }
.name-state button { margin-top: 6px; }
</style>
