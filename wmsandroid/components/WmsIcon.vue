<template>
  <svg class="wms-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <template v-if="name === 'scan'">
      <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
      <rect x="7" y="8" width="10" height="8" rx="2" /><circle cx="12" cy="12" r="2.2" />
    </template>
    <template v-else-if="name === 'receive'">
      <path d="M4 8.5 12 4l8 4.5V18l-8 4-8-4V8.5Z" /><path d="m4.5 8.5 7.5 4 7.5-4M12 12.5V22M8 2v5m0 0L6 5m2 2 2-2" />
    </template>
    <template v-else-if="name === 'putaway'">
      <path d="M4 21V6h16v15M4 11h16M4 16h16" /><path d="M12 14V3m0 0L9 6m3-3 3 3" />
    </template>
    <template v-else-if="name === 'pick'">
      <path d="M3 6h18M5 6l1 14h12l1-14M9 10v6m6-6v6" /><path d="m9 3 3-2 3 2" />
    </template>
    <template v-else-if="name === 'review'">
      <path d="M5 3h14v18H5z" /><path d="m8 12 2.5 2.5L16 9M8 6h8" />
    </template>
    <template v-else-if="name === 'replenish'">
      <path d="M4 7h12m0 0-3-3m3 3-3 3M20 17H8m0 0 3-3m-3 3 3 3" />
    </template>
    <template v-else-if="name === 'move'">
      <path d="M3 12h18M7 8l-4 4 4 4m10-8 4 4-4 4" /><rect x="9" y="4" width="6" height="16" rx="1" />
    </template>
    <template v-else-if="name === 'stocktake'">
      <path d="M4 4h16v16H4zM8 4v16M4 10h16" /><path d="m12 15 2 2 3-4" />
    </template>
    <template v-else-if="name === 'search'">
      <circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" />
    </template>
    <template v-else-if="name === 'history'">
      <path d="M4 4v5h5M5 9a8 8 0 1 1-1 5" /><path d="M12 7v5l3 2" />
    </template>
    <template v-else-if="name === 'user'">
      <circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" />
    </template>
    <template v-else-if="name === 'lock'">
      <rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
    </template>
    <template v-else-if="name === 'logout'">
      <path d="M10 4H5v16h5M14 8l4 4-4 4m4-4H9" />
    </template>
    <template v-else>
      <path d="M9 18l6-6-6-6" />
    </template>
  </svg>
</template>

<script setup>
defineProps({ name: { type: String, default: 'chevron' } })
</script>

<style scoped>
.wms-icon { width: 1em; height: 1em; display: block; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
</style>
