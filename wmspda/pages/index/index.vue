<template>
  <view class="page home-page">
    <HomeTodos :page-visible="homeVisible" />
    <view
      v-for="group in actionGroups"
      :key="group.key"
      class="home-section"
      :data-group="group.key"
    >
      <view class="home-section-title">{{ group.title }}</view>
      <view class="home-actions">
        <view
          v-for="item in group.items"
          :key="item.key"
          class="home-action"
          :data-action="item.key"
          role="button"
          tabindex="0"
          @click="go(item)"
          @keydown.enter.prevent="go(item)"
          @keydown.space.prevent="go(item)"
        >
          <text class="home-action-icon" aria-hidden="true">{{ item.emoji }}</text>
          <text class="home-action-title">{{ item.title }}</text>
        </view>
      </view>
    </view>
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
    const groups = [
      { key: 'pos', title: 'POS 销售' },
      { key: 'inbound', title: '入库作业' },
      { key: 'outbound', title: '出库作业' },
      { key: 'inventory', title: '库存管理' },
      { key: 'products', title: '商品资料' },
    ]
    const allActions = [
      { key: "pos", group: "pos", title: "POS收银", emoji: "💳", desc: "扫码收银/销售出库", op: "pos", path: "/pages/pos/index" },
      { key: "pos-report", group: "pos", title: "POS销售报表", emoji: "📊", desc: "销售记录/统计汇总", op: "pos_report", path: "/pages/pos/report" },
      { key: "pos-accuracy", group: "pos", title: "POS数据对账", emoji: "✓", desc: "销售数据核查", op: "pos_accuracy", path: "/pages/pos/accuracy" },
      { key: "arrival-manage", group: "inbound", title: "入库管理", emoji: "📋", desc: "确认发布 / 验收审核 / 入账 / 结束收货", path: "/pages/inbound/arrivals", requiresArrivalManager: true },
      { key: "receiving", group: "inbound", title: "收货(有订单)", emoji: "📋", desc: "到货验收/收货登记", op: "receive", path: "/pages/inbound/receive_task_list" },
      { key: "receivewithoutorder", group: "inbound", title: "收货(无订单)", emoji: "📥", desc: "到货验收/收货登记", op: "receivewithoutorder", path: "/pages/inbound/createwithoutorder/selectowner" },
      { key: "customer-return", group: "inbound", title: "客户退货验收", emoji: "↩️", desc: "原销售订单退回 / 分批验收", path: "/pages/customer-returns/index", requiresCustomerReturns: true },
      { key: "putaway", group: "inbound", title: "上架", emoji: "📦", desc: "库位分配/上架确认", op: "putaway", path: "/pages/inbound/putaway_task_list" },
      { key: "assisted-outbound", group: "outbound", title: "代货主出库", emoji: "🚛", desc: "仓库代货主创建并完成出库", path: "/pages/outbound/assisted", requiresAssistedOutbound: true },
      { key: "outbound-review", group: "outbound", title: "出库确认", emoji: "✅", path: "/pages/outbound/review/index", requiresWarehouseReview: true },
      { key: "picking", group: "outbound", title: "拣货", emoji: "🧾", desc: "波次拣货/拣货确认", op: "pick", path: "/pages/picking/task_list" },
      { key: "recheck", group: "outbound", title: "复核", emoji: "☑️", desc: "对拣货结果复核", op: "recheck", path: "/pages/review/pick_task_list" },
      { key: "pack", group: "outbound", title: "打包", emoji: "🎁", desc: "装箱/封箱/打印", op: "pack" },
      { key: "shipping", group: "outbound", title: "发运", emoji: "🚚", desc: "复核装车/出库发运", op: "ship" },
      { key: "pda-returns", group: "outbound", title: "退货出库", emoji: "↩️", path: "/pages/returns/index", requiresReturns: true },
      { key: "outbound-history", group: "outbound", title: "出库历史", emoji: "📚", path: "/pages/orders/index" },
      { key: "query", group: "inventory", title: "查询", emoji: "🔍", desc: "查询", op: "chaxun", path: "/pages/inventory/company" },
      { key: "replenish", group: "inventory", title: "补货", emoji: "🔀", desc: "从存储区到拣货区", op: "replenish", path: "/pages/inventory/replenish/index" },
      { key: "move", group: "inventory", title: "移库", emoji: "🔁", desc: "库内移位/合并/分拆", op: "move", path: "/pages/inventory/move/index" },
      { key: "stocktake", group: "inventory", title: "盘点", emoji: "🧮", desc: "周期盘点/抽盘/全盘", op: "stocktake", path: "/pages/inventory/stocktake/index" },
      { key: "product-import", group: "products", title: "商品导入", emoji: "📊", desc: "Excel批量创建商品档案", path: "/pages/products/import", requiresProductImport: true },
      { key: "product-export", group: "products", title: "商品导出", emoji: "📤", desc: "按货主导出商品档案", path: "/pages/products/export", requiresProductExport: true },
      { key: "all-product-export", group: "products", title: "全部商品档案导出", emoji: "📚", desc: "导出权限范围内全部商品及包装档案", requiresProductExport: true },
      { key: "missing-gtin-export", group: "products", title: "无标准条码导出", emoji: "🏷️", desc: "导出未维护标准条码的商品", requiresProductExport: true },
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
    const actionGroups = computed(() =>
      groups
        .map((group) => ({
          ...group,
          items: actions.value.filter((item) => item.group === group.key),
        }))
        .filter((group) => group.items.length > 0),
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
      actionGroups,
      go,
      missingGtinExporting,
    }
  }
}
</script>

<style scoped>
.home-page { box-sizing: border-box; padding: 16px 20px; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.home-section { margin-top: 12px; padding: 14px; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; }
.home-section-title { margin-bottom: 10px; color: #172d50; font-size: 17px; line-height: 24px; font-weight: 700; }
.home-actions { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.home-action { box-sizing: border-box; display: flex; align-items: center; gap: 10px; width: 100%; min-width: 0; min-height: 56px; margin: 0; padding: 12px; border: 1px solid #e8edf3; border-radius: 8px; background: #f8fafc; color: #172d50; text-align: left; line-height: 1.5; cursor: pointer; }
.home-action:hover { border-color: #b9d3f5; background: #eff6ff; }
.home-action:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
.home-action:active { background: #dbeafe; }
.home-action-icon { flex-shrink: 0; font-size: 26px; line-height: 1; }
.home-action-title { min-width: 0; font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
@media (max-width: 600px) {
  .home-page { padding: 12px; }
  .home-section { padding: 12px; }
  .home-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .home-action { gap: 8px; padding: 10px; }
  .home-action-icon { font-size: 24px; }
  .home-action-title { font-size: 14px; }
}
</style>
