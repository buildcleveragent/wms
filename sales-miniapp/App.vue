<script>
export default {
  onLaunch() {
    const token = uni.getStorageSync('sales_access')
    const pages = getCurrentPages()
    const currentPage = pages[pages.length - 1]
    const route = currentPage ? currentPage.route : ''
    if (token && route === 'pages/login/login') {
      uni.switchTab({ url: '/pages/index/index' })
    }
  },
}
</script>

<style>
view,
text,
input,
button,
uni-view,
uni-text,
uni-input,
uni-button {
  box-sizing: border-box;
}

page {
  min-height: 100%;
  background: #f4f6f8;
  color: #17202a;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

uni-page-body {
  overflow-x: hidden;
}
</style>
