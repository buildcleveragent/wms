<template>
  <view v-if="options.length" class="choice-group" role="group" :aria-label="label">
    <text v-if="options.length === 1" class="single">{{ options[0].label }}</text>
    <template v-else>
      <button v-for="option in options" :key="option.key" class="choice"
        :class="{ active: option.key === value }" :aria-pressed="option.key === value"
        @click="emit('change', option.key)">{{ option.label }}</button>
    </template>
  </view>
</template>

<script setup>
defineProps({ label: String, options: Array, value: String })
const emit = defineEmits(['change'])
</script>

<style scoped>
.choice-group {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  box-sizing: border-box;
  max-width: 100%;
  padding: 2px;
  gap: 2px;
  background: #f1f5f8;
  border-radius: 8px;
}
.choice, .single {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-height: 34px;
  max-width: 100%;
  margin: 0;
  padding: 3px 7px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #435c70;
  font-size: 13px;
  line-height: 1.4;
  white-space: normal;
  overflow-wrap: anywhere;
}
.choice::after { border: 0; }
.choice.active { background: #d9e8f7; color: #134e82; font-weight: 650; }
</style>
