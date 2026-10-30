<template>
  <!-- 模板部分完全兼容，无需修改 -->
  <view class="page">
<!--    <view class="header">
      <text class="title">仓库作业工作台</text>
    </view> -->

    <HomeTodos :page-visible="homeVisible" />
    <view class="grid">
      <view class="card" v-for="item in actions" :key="item.key" @click="go(item)">
        <view class="emoji">{{ item.emoji }}</view>
        <text class="card-title">{{ item.title }}</text>
      </view>
    </view>

<!--    <view class="section" v-if="lastScan">
      <text class="section-title">最近一次扫描结果</text>
      <view class="scan-box">
        <text class="scan-text">{{ lastScan }}</text>
      </view>
    </view> -->

<!--    <view class="fab" @click="quickScan" v-if="canScan">
      <text class="fab-text">扫</text>
    </view> -->
  </view>
</template>

<script>
import { computed, ref } from 'vue'
import HomeTodos from '@/components/HomeTodos.vue'
import { onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { downloadAllProductArchives, downloadMissingGtinProducts } from '@/utils/request'

export default {
  name: "WarehouseAdminHome",
  components: { HomeTodos },
  setup() {
    const auth = useAuth()
    const homeVisible = ref(false)
    const allProductExporting = ref(false)
    const missingGtinExporting = ref(false)
    // 响应式数据
    const allActions = [
      { key: "outbound-review", title: "出库确认", emoji: "✅", path: "/pages/outbound/review/index", requiresWarehouseReview: true },
      { key: "arrival-manage", title: "入库管理", emoji: "📋", desc: "确认发布 / 验收审核 / 入账 / 结束收货", path: "/pages/inbound/arrivals", requiresArrivalManager: true },
      { key: "customer-return", title: "客户退货验收", emoji: "↩️", desc: "原销售订单退回 / 分批验收", path: "/pages/customer-returns/index", requiresCustomerReturns: true },
      { key: "pda-returns", title: "退货出库", emoji: "↩️", path: "/pages/returns/index", requiresReturns: true },
      { key: "assisted-outbound", title: "代货主出库", emoji: "🚛", desc: "仓库代货主创建并完成出库", path: "/pages/outbound/assisted", requiresAssistedOutbound: true },
      { key: "receiving", title: "收货(有订单)", emoji: "📋", desc: "到货验收/收货登记", op: "receive", path: "/pages/inbound/receive_task_list" },
      { key: "receivewithoutorder", title: "收货(无订单)", emoji: "📥", desc: "到货验收/收货登记", op: "receivewithoutorder",path: "/pages/inbound/createwithoutorder/selectowner" },
      { key: "putaway",   title: "上架", emoji: "📦", desc: "库位分配/上架确认",  op: "putaway", path: "/pages/inbound/putaway_task_list" },
      { key: "picking",   title: "拣货", emoji: "🧾", desc: "波次拣货/拣货确认",   op: "pick",path: "/pages/picking/task_list" },
      { key: "recheck",   title: "复核", emoji: "✅", desc: "对拣货结果复核",     op: "recheck", path: "/pages/review/pick_task_list" },
      { key: "pos",       title: "POS收银", emoji: "💳", desc: "扫码收银/销售出库", op: "pos", path: "/pages/pos/index" },
      { key: "pos-report", title: "POS销售报表", emoji: "📊", desc: "销售记录/统计汇总", op: "pos_report", path: "/pages/pos/report" },
      { key: "pos-accuracy", title: "POS数据对账", emoji: "✓", desc: "销售数据核查", op: "pos_accuracy", path: "/pages/pos/accuracy" },
      { key: "product-import", title: "商品导入", emoji: "📊", desc: "Excel批量创建商品档案", path: "/pages/products/import", requiresProductImport: true },
      { key: "product-export", title: "商品导出", emoji: "📤", desc: "按货主导出商品档案", path: "/pages/products/export", requiresProductExport: true },
      { key: "all-product-export", title: "全部商品档案导出", emoji: "📚", desc: "导出权限范围内全部商品及包装档案", requiresProductExport: true },
      { key: "missing-gtin-export", title: "无标准条码导出", emoji: "📥", desc: "导出未维护标准条码的商品", requiresProductExport: true },
      { key: "pack",      title: "打包", emoji: "🎁", desc: "装箱/封箱/打印",     op: "pack" },
      { key: "shipping",  title: "发运", emoji: "🚚", desc: "复核装车/出库发运",  op: "ship" },
      { key: "replenish", title: "补货", emoji: "🔀", desc: "从存储区到拣货区",   op: "replenish",path: "/pages/inventory/replenish/index" },
      { key: "move",      title: "移库", emoji: "🔁", desc: "库内移位/合并/分拆", op: "move",path: "/pages/inventory/move/index" },
      { key: "stocktake", title: "盘点", emoji: "🧮", desc: "周期盘点/抽盘/全盘", op: "stocktake",path: "/pages/inventory/stocktake/index" },
	  { key: "query", title: "查询", emoji: "🔍", desc: "查询", op: "chaxun",path: "/pages/inventory/company" },
    ]
    const actions = computed(() =>
      allActions
        .filter(
          (item) =>
            (!item.requiresWarehouseReview || auth.canReviewWarehouseOutbound) &&
            (!item.requiresArrivalManager || auth.perms.includes("inbound.manage_arrival")) &&
            (!item.requiresReturns || auth.canProcessPdaReturn) &&
            (!item.requiresCustomerReturns || auth.perms.includes("inbound.receive_customer_return")) &&
            (!item.requiresAssistedOutbound || auth.canProcessAssistedOutbound) &&
            (!item.requiresProductImport || auth.canImportProducts) &&
            (!item.requiresProductExport || auth.canExportProducts),
        )
        .map((item) =>
          (item.key === 'all-product-export' && allProductExporting.value) ||
          (item.key === 'missing-gtin-export' && missingGtinExporting.value)
            ? { ...item, title: '正在导出…' }
            : item,
        ),
    )

    const exportProducts = async (exporting, download, label) => {
      if (!auth.canExportProducts || allProductExporting.value || missingGtinExporting.value) return
      exporting.value = true
      uni.showLoading({ title: '正在生成 Excel' })
      let toast = { title: `${label}已导出`, icon: 'success' }
      try {
        const result = await download()
        if (result?.tempFilePath) {
          // #ifndef H5
          const filePath = await new Promise((resolve) => {
            uni.saveFile({
              tempFilePath: result.tempFilePath,
              success: (saved) => resolve(saved.savedFilePath || result.tempFilePath),
              fail: () => resolve(result.tempFilePath),
            })
          })
          uni.openDocument({
            filePath,
            fileType: 'xlsx',
            showMenu: true,
            fail: () =>
              uni.showToast({ title: '文件已下载，请在文件管理中查看', icon: 'none' }),
          })
          // #endif
        }
      } catch (error) {
        if (Number(error?.statusCode) === 403) {
          await auth.loadProfile({ force: true }).catch(() => {})
        }
        toast = { title: error?.message || `${label}导出失败`, icon: 'none' }
      } finally {
        uni.hideLoading()
        exporting.value = false
      }
      uni.showToast(toast)
    }

    const exportAllProducts = () =>
      exportProducts(allProductExporting, downloadAllProductArchives, '全部商品档案')

    const exportMissingGtinProducts = () =>
      exportProducts(missingGtinExporting, downloadMissingGtinProducts, '无标准条码商品')
    
    // 方法
    const go = (item) => {
      if (item.key === 'all-product-export') {
        exportAllProducts()
        return
      }
      if (item.key === 'missing-gtin-export') {
        exportMissingGtinProducts()
        return
      }
      console.log("👉 go() 被调用，准备跳转：", item.path)
      uni.showToast({ title: "跳转中...", icon: "none" })
      uni.navigateTo({ url: item.path })
    }

    onShow(() => {
      homeVisible.value = true
      // 每次回到工作台都重新确认能力，保证后台撤权能及时生效。
      auth.loadProfile({ force: true }).catch((error) => {
        console.warn('权限资料暂不可用，代货主出库入口保持隐藏', error)
      })
    })
    onHide(() => { homeVisible.value = false })
    onUnload(() => { homeVisible.value = false })

    // 返回模板需要的数据和方法
    return {
      homeVisible,
      actions,
      go,
      missingGtinExporting,
    }
  }
}
</script>

<style scoped>
/* 样式部分完全兼容，无需修改 */
.page { box-sizing: border-box; padding: 24rpx; }
.header { margin-top: 8rpx; margin-bottom: 20rpx; }
.title { font-size: 40rpx; font-weight: 700; }
.subtitle { margin-top: 8rpx; color: #666; font-size: 26rpx; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); grid-gap: 24rpx; }
.card { background: #fff; border-radius: 24rpx; padding: 28rpx; box-shadow: 0 6rpx 24rpx rgba(0,0,0,.05); }
.card:active { opacity: .85; }
.emoji { font-size: 56rpx; margin-bottom: 10rpx; }
.card-title { font-size: 32rpx; font-weight: 600; }
.card-desc { margin-top: 6rpx; color: #888; font-size: 24rpx; }
.section { margin-top: 28rpx; }
.section-title { font-size: 28rpx; color: #333; margin-bottom: 12rpx; }
.scan-box { background: #f7f7f9; border-radius: 18rpx; padding: 20rpx; }
.scan-text { font-size: 28rpx; word-break: break-all; }
.fab { position: fixed; right: 36rpx; bottom: 60rpx; width: 100rpx; height: 100rpx; background: #007aff; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 12rpx 30rpx rgba(0,0,0,.15); }
.fab-text { color: #fff; font-size: 36rpx; font-weight: 700; }
</style>
