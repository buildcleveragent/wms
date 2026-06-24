<template>
  <view class="app-shell">
    <slot />
  </view>
</template>

<script>
export default {
  data() {
    return {
      bootRedirectDone: false,
    }
  },
  methods: {
    ensureLoginEntry() {
      if (this.bootRedirectDone || typeof getCurrentPages !== 'function') {
        return
      }
      const pages = getCurrentPages() || []
      const current = pages.length ? pages[pages.length - 1].route : ''
      if (!current) {
        return
      }
      this.bootRedirectDone = true
      if (current !== 'pages/login') {
        uni.reLaunch({ url: '/pages/login' })
      }
    },
  },
  onLaunch() {
    setTimeout(() => {
      this.ensureLoginEntry()
    }, 80)
  },
  onShow() {
    setTimeout(() => {
      this.ensureLoginEntry()
    }, 80)
  },
}
</script>

<style>
page,
.page {
  box-sizing: border-box;
  min-height: 100vh;
  width: 100%;
}

view,
text,
button,
input,
picker {
  box-sizing: border-box;
}

.app-shell {
  min-height: 100vh;
  background: linear-gradient(180deg, #f6f8fc 0%, #edf3ff 100%);
  color: #142033;
}

.btn-primary,
.btn-ghost {
  border-radius: 18rpx;
  font-size: 26rpx;
}

.btn-primary {
  background: linear-gradient(135deg, #0b5fff, #378dff);
  color: #fff;
}

.btn-ghost {
  background: #eef3fb;
  color: #243149;
}
</style>
