<template>
  <view class="pos-page">
    <view class="topbar">
      <button class="nav-back" @click="goBack">‹</button>
      <text class="title">金桥融通仓POS收银</text>
      <view class="nav-spacer"></view>
    </view>

    <view class="section shift-section">
      <view class="section-head">
        <text class="section-title">当前班次</text>
        <view class="section-actions">
          <button class="ghost-btn shift-refresh-btn" :disabled="shiftLoading" @click="loadCurrentShift">
            刷新
          </button>
          <button v-if="currentShift" class="ghost-btn shift-refresh-btn" @click="printCurrentShift">
            打印
          </button>
          <button v-if="currentShift" class="ghost-btn shift-refresh-btn" @click="exportCurrentShift">
            导出
          </button>
        </view>
      </view>
      <view v-if="currentShift" class="shift-card">
        <view class="shift-main">
          <text class="shift-no">{{ currentShift.shift_no }}</text>
          <text class="shift-meta">{{ currentShift.cashier_username || '-' }} / {{ shiftStatusText(currentShift.status) }}</text>
          <text class="shift-meta">开班 {{ formatDateTime(currentShift.opened_at) }}</text>
        </view>
        <view class="shift-numbers">
          <text>净销售 {{ money(shiftSummary.net_amount) }}</text>
          <text>实收 {{ money(shiftSummary.received_amount) }} / 赊账 {{ money(shiftSummary.credit_amount) }} / 还款 {{ money(shiftSummary.repayment_amount) }}</text>
          <text>完成 {{ shiftSummary.completed_count || 0 }} 单 / 退货 {{ shiftSummary.return_count || 0 }} 单 / 作废 {{ shiftSummary.voided_count || 0 }} 单</text>
          <text>退货金额 {{ money(shiftSummary.return_amount) }}</text>
          <text>现金应点 {{ money(shiftSummary.expected_cash_amount) }}</text>
        </view>
        <view v-if="isActiveShift(currentShift)" class="shift-close-panel">
          <view class="shift-close-row">
            <input class="input shift-cash-input" v-model.trim="shiftActualCashAmount" type="digit" placeholder="现金实点金额" />
            <button class="primary-btn shift-action-btn" :disabled="shiftLoading" @click="closeCurrentShift">
              交班
            </button>
          </view>
          <view v-if="shiftPaymentRows.length" class="shift-payment-actuals">
            <view class="shift-payment-row" v-for="row in shiftPaymentRows" :key="row.method">
              <text class="shift-payment-name">{{ row.method_label || paymentMethodName(row.method) }}</text>
              <text class="shift-payment-expected">应 {{ money(row.expected_amount) }} / 退 {{ money(row.refund_amount) }}</text>
              <input class="input shift-payment-input" v-model.trim="shiftPaymentActuals[row.method]" type="digit" placeholder="实点" />
              <text :class="['shift-payment-diff', { danger: Number(paymentActualDiff(row)) !== 0 }]">
                差 {{ money(paymentActualDiff(row)) }}
              </text>
            </view>
          </view>
        </view>
      </view>
      <view v-else class="shift-open-row">
        <input class="input shift-cash-input" v-model.trim="shiftOpeningCashAmount" type="digit" placeholder="备用金" />
        <button class="primary-btn shift-action-btn" :disabled="shiftLoading" @click="openCurrentShift">
          开班
        </button>
        <text class="shift-hint">开班后才能结账</text>
      </view>
    </view>

    <view class="pos-main">
      <view class="pos-left">
    <view class="section pos-toolbar">
      <view class="scan-line">
        <text class="toolbar-label primary-label">商品</text>
        <input
          class="input scan-input"
          :value="productKeyword"
          :focus="productInputFocus"
          placeholder="扫码或输入商品名称/编码/SKU/条码"
          confirm-type="search"
          :confirm-hold="true"
          @focus="productInputFocus = true"
          @blur="productInputFocus = false"
          @input="onProductInput"
          @confirm="handleProductConfirm"
        />
        <button class="ghost-btn scan-btn" @click="triggerQuickScan">扫码</button>
        <button class="primary-btn scan-btn" :disabled="productLoading" @click="searchProducts">搜索</button>
        <button class="ghost-btn clear-btn" @click="confirmResetSale">清空</button>
      </view>

      <view v-if="lastScan || scanFeedbackMessage" :class="['scan-feedback', scanFeedbackType]">
        <text v-if="lastScan" class="scan-code">最近扫码：{{ lastScan }}</text>
        <text v-if="scanFeedbackMessage" class="scan-message">{{ scanFeedbackMessage }}</text>
      </view>

      <view class="product-list" v-if="products.length">
        <view class="product-row" v-for="product in products" :key="product.id">
          <view class="product-main">
            <text class="product-name">{{ product.name || product.sku || product.code }}</text>
            <text class="product-meta">{{ product.code }} {{ product.sku || '' }}</text>
            <text class="product-meta">
              售价 {{ money(product.price) }} / 可售 {{ qtyText(stockAvailableQty(product), product) }}
            </text>
          </view>
          <button class="primary-btn add-btn" :disabled="!hasAvailableStock(product)" @click="addToCart(product)">
            {{ hasAvailableStock(product) ? '加入' : '无货' }}
          </button>
        </view>
      </view>
      <view class="empty-tip" v-else-if="productSearched && !productLoading && !scanFeedbackMessage">未找到商品</view>
    </view>
    <view class="section cart-section">
      <view class="section-head">
        <text class="section-title">购物车</text>
        <text class="selected-text">{{ cartItems.length }}项 / {{ money(totalAmount) }}</text>
      </view>

      <view v-if="!cartItems.length" class="empty-cart">扫码或搜索商品后加入购物车</view>

      <view v-else class="cart-table">
        <view
          class="cart-table-head cart-line-row"
          style="display: flex; flex-direction: row; align-items: center; flex-wrap: nowrap; width: 100%;"
        >
          <view class="cart-goods-col" style="flex: 1 1 auto; min-width: 0; padding-right: 12rpx; text-align: right; box-sizing: border-box;">商品</view>
          <view class="cart-unit-col" style="width: 96rpx; flex: 0 0 96rpx; margin-left: 8rpx; box-sizing: border-box;">单位</view>
          <view class="cart-qty-col" style="width: 130rpx; flex: 0 0 130rpx; margin-left: 8rpx; box-sizing: border-box;">数量</view>
          <view class="cart-price-col" style="width: 150rpx; flex: 0 0 150rpx; margin-left: 8rpx; box-sizing: border-box;">单价</view>
          <view class="cart-amount-col" style="width: 160rpx; flex: 0 0 160rpx; margin-left: 8rpx; box-sizing: border-box;">金额</view>
          <view class="cart-action-col" style="width: 70rpx; flex: 0 0 70rpx; margin-left: 8rpx; text-align: center; box-sizing: border-box;">操作</view>
        </view>

        <view
          v-for="(item, index) in cartItems"
          :key="item.product_id"
          class="cart-row cart-line-row"
          style="display: flex; flex-direction: row; align-items: center; flex-wrap: nowrap; width: 100%;"
        >
          <view class="cart-info cart-goods-col" style="flex: 1 1 auto; min-width: 0; padding-right: 12rpx; text-align: right; box-sizing: border-box;">
            <text class="cart-goods-line">
              {{ item.name }}　{{ item.code }} / 可售 {{ qtyText(item.available_qty, item) }} {{ item.base_unit_name }} / 基本数量 {{ qtyText(lineBaseQty(item), item) }} {{ item.base_unit_name }}
            </text>
          </view>

          <view class="cart-unit-col" style="width: 96rpx; flex: 0 0 96rpx; margin-left: 8rpx; box-sizing: border-box;">
            <picker
              class="unit-picker"
              mode="selector"
              :range="item.unit_labels"
              :value="item.unit_index"
              @change="changeUnit(index, $event)"
            >
              <view class="picker-value">{{ item.unit_labels[item.unit_index] }}</view>
            </picker>
          </view>

          <view class="cart-qty-col" style="width: 130rpx; flex: 0 0 130rpx; margin-left: 8rpx; box-sizing: border-box;">
            <input
              class="small-input"
              :type="isCountItem(item) ? 'number' : 'digit'"
              v-model="item.qty"
              @blur="normalizeLine(index)"
            />
          </view>

          <view class="cart-price-col" style="width: 150rpx; flex: 0 0 150rpx; margin-left: 8rpx; box-sizing: border-box;">
            <view class="price-wrap">
              <text class="yuan">¥</text>
              <input
                class="price-input"
                type="digit"
                v-model="item.price"
                @blur="normalizeLine(index)"
              />
            </view>
          </view>

          <view class="cart-amount-col" style="width: 160rpx; flex: 0 0 160rpx; margin-left: 8rpx; box-sizing: border-box;">
            <text class="line-amount">{{ money(lineAmount(item)) }}</text>
          </view>

          <view class="cart-action-col" style="width: 70rpx; flex: 0 0 70rpx; margin-left: 8rpx; text-align: center; box-sizing: border-box;">
            <button class="danger-btn remove-btn" @click="removeLine(index)">删</button>
          </view>
        </view>
      </view>
    </view>
      </view>
      <view class="pos-right">

    <view class="section customer-panel">
      <view class="section-head">
        <text class="section-title">客户信息</text>
        <text class="selected-text">{{ selectedCustomerName }}</text>
      </view>
      <view class="customer-search-row">
        <input
          class="input customer-input"
          v-model.trim="customerKeyword"
          placeholder="散客 / 客户名称 / 编码"
          confirm-type="search"
          @confirm="searchCustomers"
        />
        <button class="primary-btn customer-search-btn" :disabled="customerLoading" @click="searchCustomers">搜索</button>
        <button class="ghost-btn customer-add-btn" @click="openCustomerCreate">新增</button>
      </view>
      <view v-if="customerCreateOpen" class="customer-create-panel">
        <input class="input customer-create-input" v-model.trim="customerForm.name" placeholder="客户名称" />
        <input class="input customer-create-input" v-model.trim="customerForm.phone" placeholder="电话" />
        <input class="input customer-create-input wide" v-model.trim="customerForm.address" placeholder="地址" />
        <view class="customer-create-actions">
          <button class="ghost-btn customer-create-btn" @click="cancelCustomerCreate">取消</button>
          <button class="primary-btn customer-create-btn" :disabled="customerSaving" @click="submitCustomerCreate">
            保存
          </button>
        </view>
      </view>
      <view class="customer-list" v-if="customers.length">
        <view
          v-for="customer in customers"
          :key="customer.id"
          :class="['choice-row', { active: selectedCustomer && selectedCustomer.id === customer.id }]"
          @click="selectCustomer(customer)"
        >
          <view>
            <text class="choice-name">{{ customer.name || customer.code || customer.id }}</text>
            <text class="choice-code">ID: {{ customer.id }} {{ customer.code || '' }}</text>
          </view>
          <text class="choice-check" v-if="selectedCustomer && selectedCustomer.id === customer.id">
            已选
          </text>
        </view>
      </view>
      <view v-if="selectedCustomer" class="customer-debt-panel">
        <view class="customer-debt-head">
          <text>累计欠款</text>
          <text class="customer-debt-amount">{{ money(customerDebtBalance) }}</text>
          <button class="ghost-btn debt-refresh-btn" :disabled="customerDebtLoading" @click="loadCustomerDebt({ force: true })">
            刷新
          </button>
        </view>
        <view class="repayment-row">
          <picker
            class="payment-picker repayment-picker"
            mode="selector"
            :range="repaymentMethodLabels"
            :value="repaymentMethodIndex"
            @change="changeRepaymentMethod"
          >
            <view class="payment-value">{{ selectedRepaymentMethod.label }}</view>
          </picker>
          <input class="payment-input repayment-input" type="digit" v-model.trim="repaymentAmount" placeholder="还款金额" />
          <button class="ghost-btn repayment-fill-btn" @click="fillRepaymentAmount">全额</button>
        </view>
        <view class="repayment-row">
          <input class="bill-input repayment-ref-input" v-model.trim="repaymentReferenceNo" placeholder="参考号/可选" />
          <button class="primary-btn repayment-submit-btn" :disabled="repaymentSubmitting || customerDebtBalance <= 0" @click="submitCustomerRepayment">
            收款
          </button>
        </view>
      </view>
      <view class="doc-info-stack">
        <view class="doc-info-item">
          <text class="doc-label">日期</text>
          <text class="doc-value">{{ saleDateText }}</text>
        </view>
        <view class="doc-info-item">
          <text class="doc-label">小票号</text>
          <input class="input doc-input" v-model.trim="srcBillNo" placeholder="小票号" />
        </view>
      </view>
    </view>

    <view class="submit-panel">
      <view class="checkout-row">
        <view class="bill-row remark-row">
          <text class="bill-label">备注</text>
          <input class="bill-input" v-model.trim="remark" placeholder="可选" />
        </view>
        <checkbox-group class="split-payment-check" @change="onSplitPaymentChange">
          <label class="print-option">
            <checkbox value="split" :checked="splitPaymentEnabled" color="#1677ff" />
            <text>拆分支付</text>
          </label>
        </checkbox-group>
        <view v-if="!splitPaymentEnabled" class="bill-row payment-row">
          <text class="bill-label">支付</text>
          <picker
            class="payment-picker"
            mode="selector"
            :range="paymentMethodLabels"
            :value="paymentMethodIndex"
            @change="changePaymentMethod"
          >
            <view class="payment-value">{{ selectedPaymentMethod.label }}</view>
          </picker>
          <input
            class="payment-input"
            type="digit"
            v-model="amountReceived"
            placeholder="实收"
            :disabled="paymentMethod !== 'CASH'"
          />
        </view>
        <view v-if="!splitPaymentEnabled" class="bill-row reference-row">
          <text class="bill-label">参考号</text>
          <input class="bill-input" v-model.trim="paymentReferenceNo" placeholder="支付流水号/可选" />
        </view>
        <view v-else class="split-payment-panel">
          <view class="split-payment-row" v-for="(line, index) in paymentLines" :key="index">
            <picker
              class="payment-picker split-picker"
              mode="selector"
              :range="paymentMethodLabels"
              :value="paymentLineMethodIndex(line)"
              @change="changePaymentLineMethod(index, $event)"
            >
              <view class="payment-value">{{ paymentMethodName(line.method) }}</view>
            </picker>
            <input class="payment-input split-input" type="digit" v-model.trim="line.amount" placeholder="抵扣" @blur="normalizePaymentLine(index)" />
            <input class="payment-input split-input" type="digit" v-model.trim="line.amount_received" placeholder="实收" @blur="normalizePaymentLine(index)" />
            <input class="bill-input split-ref-input" v-model.trim="line.reference_no" placeholder="参考号" />
            <button class="danger-btn split-remove-btn" :disabled="paymentLines.length <= 1" @click="removePaymentLine(index)">删</button>
          </view>
          <view class="split-payment-tools">
            <text>已分配 {{ money(splitPaymentAmount) }} / 待收 {{ money(splitPaymentRemaining) }}</text>
            <button class="ghost-btn split-add-btn" @click="addPaymentLine">加一行</button>
          </view>
        </view>
        <view class="summary-row">
          <view class="amount-due">
            <text class="amount-label">应收金额</text>
            <text class="summary-title">{{ money(totalAmount) }}</text>
          </view>
          <view class="amount-grid">
            <view class="amount-box">
              <text class="amount-label">实收</text>
              <text class="amount-value">{{ money(receivedAmount) }}</text>
            </view>
            <view class="amount-box">
              <text class="amount-label">找零</text>
              <text class="amount-value change-value">{{ money(changeAmount) }}</text>
            </view>
            <view class="amount-box">
              <text class="amount-label">本单赊账</text>
              <text class="amount-value debt-value">{{ money(creditAmount) }}</text>
            </view>
          </view>
          <view class="summary-sub">
            <text>商品 {{ cartItems.length }} 项</text>
            <text>基本数量 {{ qtyText(totalBaseQty) }}</text>
            <text v-if="ownerCount > 1">货主 {{ ownerCount }} 个，将自动拆单</text>
          </view>
          <view v-if="selectedCustomer && ownerCount > 1" class="owner-warning">
            已选客户仅用于同货主订单，其他货主自动使用散客
          </view>
          <view v-if="creditAmount > 0 && !selectedCustomer" class="owner-warning">
            赊账必须先选择客户
          </view>
          <view class="print-settings">
            <checkbox-group class="print-check-group" @change="onAutoPrintChange">
              <label class="print-option">
                <checkbox value="auto" :checked="autoPrintSale" color="#1677ff" />
                <text>自动打印销售单</text>
              </label>
            </checkbox-group>
            <view class="print-mode-control">
              <text class="print-mode-label">纸型</text>
              <picker
                class="print-mode-picker"
                mode="selector"
                :range="salePrintModeLabels"
                :value="salePrintModeIndex"
                @change="changeSalePrintMode"
              >
                <view class="print-mode-value">{{ selectedSalePrintMode.label }}</view>
              </picker>
            </view>
            <view class="print-mode-control receipt-info-control">
              <text class="print-mode-label">仓库信息</text>
              <picker
                class="print-mode-picker"
                mode="selector"
                :range="receiptWarehouseInfoLabels"
                :value="selectedReceiptWarehouseInfoIndex"
                @change="changeReceiptWarehouseInfo"
              >
                <view class="print-mode-value">{{ selectedReceiptWarehouseInfo.name }}</view>
              </picker>
            </view>
          </view>
          <button class="submit-btn" :disabled="!canCheckout || submitting" @click="checkout">
            结账
          </button>
        </view>
      </view>
    </view>

    <view class="section stats-section">
      <view class="section-head">
        <text class="section-title">今日统计</text>
        <view class="section-actions">
          <button class="ghost-btn stats-refresh-btn" :disabled="statsLoading" @click="loadPosStats({ force: true })">
            刷新
          </button>
          <button class="ghost-btn stats-refresh-btn" @click="exportTodayStats">
            导出
          </button>
        </view>
      </view>
      <view class="stats-grid">
        <view class="stats-card primary">
          <text class="stats-label">净销售</text>
          <text class="stats-value">{{ money(statsSummary.net_amount) }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">实收金额</text>
          <text class="stats-value">{{ money(statsSummary.received_amount) }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">赊账金额</text>
          <text class="stats-value debt-value">{{ money(statsSummary.credit_amount) }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">客户还款</text>
          <text class="stats-value">{{ money(statsSummary.repayment_amount) }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">完成单</text>
          <text class="stats-value">{{ statsSummary.completed_count || 0 }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">作废单</text>
          <text class="stats-value danger">{{ statsSummary.voided_count || 0 }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">退货金额</text>
          <text class="stats-value danger">{{ money(statsSummary.return_amount) }}</text>
        </view>
        <view class="stats-card">
          <text class="stats-label">作废金额</text>
          <text class="stats-value danger">{{ money(statsSummary.voided_amount) }}</text>
        </view>
      </view>
      <view class="stats-payments" v-if="statsPayments.length">
        <view class="stats-payment-row" v-for="row in statsPayments" :key="row.method || row.method_label">
          <text>{{ row.method_label || paymentMethodName(row.method) }}</text>
          <text>{{ money(row.amount) }} / 销 {{ row.sale_count || 0 }} / 还 {{ row.repayment_count || 0 }}</text>
        </view>
      </view>
    </view>

    <view class="section receipt-section" v-if="lastReceipt">
      <view class="section-head">
        <text class="section-title">最近小票</text>
        <text class="selected-text">{{ lastReceipt.sale_no || lastReceipt.src_bill_no }}</text>
      </view>
      <button class="ghost-btn receipt-print-btn" :disabled="!lastSalePrintData" @click="printLastSale">
        打印销售单
      </button>
      <view class="receipt-row">
        <text>应收 {{ money(lastReceipt.total_amount) }}</text>
        <text>
          {{ paymentMethodName(lastReceipt.payment?.method) }}
          实收 {{ money(lastReceipt.payment?.amount_received) }}
        </text>
      </view>
      <view class="receipt-row">
        <text>本单赊账 {{ money(lastReceipt.payment?.credit_amount || lastReceipt.credit_amount) }}</text>
        <text>累计欠款 {{ money(lastReceipt.cumulative_debt) }}</text>
      </view>
      <view class="receipt-row">
        <text>找零 {{ money(lastReceipt.payment?.change_amount) }}</text>
        <text>出库单 {{ (lastReceipt.orders || []).length }} 张</text>
      </view>
      <view class="receipt-row" v-if="orderNosText(lastReceipt.orders)">
        <text>出库单号</text>
        <text class="receipt-nos">{{ orderNosText(lastReceipt.orders) }}</text>
      </view>
    </view>

    <view class="section history-section">
      <view class="section-head">
        <text class="section-title">销售历史</text>
        <view class="section-actions">
          <button class="ghost-btn history-refresh-btn" @click="exportSalesHistory">
            导出
          </button>
          <button class="ghost-btn history-refresh-btn" :disabled="historyLoading" @click="loadPosSaleHistory({ force: true })">
            刷新
          </button>
        </view>
      </view>
      <view class="history-search-row">
        <input
          class="input history-input"
          v-model.trim="historyKeyword"
          placeholder="小票号 / POS单号"
          confirm-type="search"
          @confirm="loadPosSaleHistory({ force: true })"
        />
        <button class="primary-btn history-search-btn" :disabled="historyLoading" @click="loadPosSaleHistory({ force: true })">
          查询
        </button>
      </view>

      <view v-if="pendingVoidSale" class="void-panel">
        <text class="void-title">作废 {{ saleDisplayNo(pendingVoidSale) }}</text>
        <input class="input void-input" v-model.trim="voidReason" placeholder="请输入作废原因" />
        <view class="void-actions">
          <button class="ghost-btn void-action-btn" @click="cancelVoidSale">取消</button>
          <button class="danger-btn void-action-btn" @click="confirmVoidSale">确认作废</button>
        </view>
      </view>

      <view v-if="pendingReturnSale" class="void-panel return-panel">
        <text class="void-title">退货 {{ saleDisplayNo(pendingReturnSale) }}</text>
        <view class="return-line" v-for="(line, index) in returnLines" :key="line.id">
          <view class="return-line-main">
            <text class="return-name">{{ line.product_name || line.product_code }}</text>
            <text class="history-meta">可退 {{ qtyText(line.returnable_qty) }} / 单价 {{ money(line.price) }}</text>
          </view>
          <input class="input return-qty-input" v-model.trim="line.return_qty" type="digit" placeholder="退货数量" @blur="normalizeReturnLine(index)" />
        </view>
        <input class="input void-input" v-model.trim="returnReason" placeholder="请输入退货原因" />
        <view class="bill-row payment-row return-refund-row">
          <text class="bill-label">退款</text>
          <picker
            class="payment-picker"
            mode="selector"
            :range="paymentMethodLabels"
            :value="returnPaymentMethodIndex"
            @change="changeReturnPaymentMethod"
          >
            <view class="payment-value">{{ paymentMethodName(returnRefundMethod) }}</view>
          </picker>
          <input class="payment-input" type="digit" v-model.trim="returnRefundAmount" placeholder="退款金额" />
        </view>
        <view class="bill-row reference-row">
          <text class="bill-label">参考号</text>
          <input class="bill-input" v-model.trim="returnRefundReferenceNo" placeholder="退款流水号/可选" />
        </view>
        <view class="void-actions">
          <button class="ghost-btn void-action-btn" @click="cancelReturnSale">取消</button>
          <button class="danger-btn void-action-btn" :disabled="returnSubmitting" @click="confirmReturnSale">
            确认退货 {{ money(returnTotalAmount) }}
          </button>
        </view>
      </view>

      <view v-if="!historySales.length && !historyLoading" class="empty-tip">暂无销售记录</view>
      <view v-else class="history-list">
        <view class="history-row" v-for="sale in historyPreviewSales" :key="sale.id">
          <view class="history-main">
            <view class="history-title-row sale-history-line">
              <text class="history-no">{{ saleDisplayNo(sale) }}</text>
              <text class="history-money">{{ money(sale.total_amount) }}</text>
              <text v-if="saleCreditAmount(sale) > 0" class="history-debt">欠 {{ money(saleCreditAmount(sale)) }}</text>
              <text class="history-order-count">出库单 {{ saleOrderCount(sale) }} 张</text>
              <text :class="['history-status', isVoidedSale(sale) ? 'voided' : 'completed']">
                {{ saleStatusText(sale.status) }}
              </text>
            </view>
          </view>
          <view class="history-actions sale-history-actions">
            <button class="ghost-btn history-action-btn" @click="reprintSale(sale)">重打</button>
            <button class="ghost-btn history-action-btn" :disabled="!saleCanReturn(sale)" @click="startReturnSale(sale)">退货</button>
            <button class="danger-btn history-action-btn" :disabled="isVoidedSale(sale)" @click="startVoidSale(sale)">作废</button>
          </view>
        </view>
      </view>
      <button
        v-if="historyMoreSales.length"
        class="ghost-btn history-more-btn"
        @click="toggleHistoryMore"
      >
        {{ historyMoreButtonText }}
      </button>
      <view v-if="historyMoreOpen && historyMoreSales.length" class="history-more-panel">
        <view class="history-row" v-for="sale in historyMoreSales" :key="'more-' + sale.id">
          <view class="history-main">
            <view class="history-title-row sale-history-line">
              <text class="history-no">{{ saleDisplayNo(sale) }}</text>
              <text class="history-money">{{ money(sale.total_amount) }}</text>
              <text v-if="saleCreditAmount(sale) > 0" class="history-debt">欠 {{ money(saleCreditAmount(sale)) }}</text>
              <text class="history-order-count">出库单 {{ saleOrderCount(sale) }} 张</text>
              <text :class="['history-status', isVoidedSale(sale) ? 'voided' : 'completed']">
                {{ saleStatusText(sale.status) }}
              </text>
            </view>
          </view>
          <view class="history-actions sale-history-actions">
            <button class="ghost-btn history-action-btn" @click="reprintSale(sale)">重打</button>
            <button class="ghost-btn history-action-btn" :disabled="!saleCanReturn(sale)" @click="startReturnSale(sale)">退货</button>
            <button class="danger-btn history-action-btn" :disabled="isVoidedSale(sale)" @click="startVoidSale(sale)">作废</button>
          </view>
        </view>
      </view>
    </view>

    <view class="section shift-history-section">
      <view class="section-head">
        <text class="section-title">交班记录</text>
        <button class="ghost-btn history-refresh-btn" :disabled="shiftHistoryLoading" @click="loadShiftHistory({ force: true })">
          刷新
        </button>
      </view>
      <view v-if="!shiftHistory.length && !shiftHistoryLoading" class="empty-tip">暂无班次记录</view>
      <view v-else class="shift-history-list">
        <view class="shift-history-row" v-for="shift in shiftHistory" :key="shift.id">
          <view class="shift-history-main">
            <view class="history-title-row">
              <text class="history-no">{{ shift.shift_no }}</text>
              <text :class="['history-status', isActiveShift(shift) ? 'active' : 'completed']">
                {{ shiftStatusText(shift.status) }}
              </text>
            </view>
            <text class="history-meta">
              {{ shift.cashier_username || '-' }} / 净销售 {{ money(shift.summary?.net_amount) }} / 赊账 {{ money(shift.summary?.credit_amount) }} / 还款 {{ money(shift.summary?.repayment_amount) }}
            </text>
            <text class="history-meta">
              开班 {{ formatDateTime(shift.opened_at) }} / 交班 {{ shift.closed_at ? formatDateTime(shift.closed_at) : '-' }}
            </text>
          </view>
          <view class="history-actions">
            <button class="ghost-btn history-action-btn" @click="printShift(shift)">打印</button>
            <button class="ghost-btn history-action-btn" @click="exportShift(shift)">导出</button>
            <button v-if="shift.status === 'CLOSED'" class="danger-btn history-action-btn" @click="reopenShift(shift)">重开</button>
          </view>
        </view>
      </view>
    </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { onHide, onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'

const customerKeyword = ref('')
const customers = ref([])
const selectedCustomer = ref(null)
const customerLoading = ref(false)
const customerCreateOpen = ref(false)
const customerSaving = ref(false)
const customerForm = ref({
  name: '',
  phone: '',
  address: '',
})

const productKeyword = ref('')
const productInputFocus = ref(true)
const products = ref([])
const productLoading = ref(false)
const productSearched = ref(false)
const scanFeedbackMessage = ref('')
const scanFeedbackType = ref('info')
const productLookupQueue = []
let productLookupRunning = false
let lastProductLookup = { keyword: '', time: 0 }
let cartStockRefreshing = false
let lastCartStockRefreshAt = 0

const cartItems = ref([])
const srcBillNo = ref(makeReceiptNo())
const saleDate = ref(new Date())
const remark = ref('')
const submitting = ref(false)
const idempotencyKey = ref(makeIdempotencyKey())
const lastReceipt = ref(null)
const lastSalePrintData = ref(null)
const historyKeyword = ref('')
const historySales = ref([])
const historyLoading = ref(false)
const historyMoreOpen = ref(false)
const pendingVoidSale = ref(null)
const voidReason = ref('')
const posStats = ref(defaultPosStats())
const statsLoading = ref(false)
const currentShift = ref(null)
const shiftLoading = ref(false)
const shiftOpeningCashAmount = ref('0.00')
const shiftActualCashAmount = ref('')
const shiftPaymentActuals = ref({})
const shiftHistory = ref([])
const shiftHistoryLoading = ref(false)
const POS_DRAFT_KEY = 'pos_sale_draft_v1'
const POS_AUTO_PRINT_KEY = 'pos_auto_print_sale_v1'
const POS_SALE_PRINT_MODE_KEY = 'pos_sale_print_mode_v2'
const POS_RECEIPT_WAREHOUSE_INFO_KEY = 'pos_receipt_warehouse_info_v1'
const SALE_PRINT_COMPANY_NAME = '金桥融通仓'
const SALE_PRINT_SHOP_ADDRESS = ' '
const SALE_PRINT_SHOP_PHONE = ' '
const SALE_PRINT_BANK_ACCOUNT = ' '
const HISTORY_PREVIEW_LIMIT = 3
const POS_STOCK_QUERY = {
  zone_type: 1,
  picking_only: 1,
}
const SALE_PRINT_MODES = [
  { label: '三等分241×93', value: 'dot_241_93' },
  { label: 'A4横向', value: 'a4_landscape' },
  { label: '针式9.5x5.5', value: 'dot_9_5_5' },
  { label: '针式9.5x11', value: 'dot_9_5_11' },
]
const DEFAULT_SALE_PRINT_MODE = 'dot_241_93'
const SALE_PRINT_METHOD_FRONTEND = 'frontend_html'
const SALE_PRINT_METHOD_BACKEND = 'backend_html'
const DEFAULT_SALE_PRINT_METHOD = SALE_PRINT_METHOD_FRONTEND

const paymentMethods = [
  { label: '现金', value: 'CASH' },
  { label: '微信', value: 'WECHAT' },
  { label: '支付宝', value: 'ALIPAY' },
  { label: '银行卡', value: 'BANK_CARD' },
  { label: '赊账', value: 'CREDIT' },
  { label: '其他', value: 'OTHER' },
]
const paymentMethod = ref('WECHAT')
const amountReceived = ref('')
const paymentReferenceNo = ref('')
const splitPaymentEnabled = ref(false)
const paymentLines = ref([])
const customerDebtBalance = ref(0)
const customerDebtLoading = ref(false)
const repaymentMethod = ref('CASH')
const repaymentAmount = ref('')
const repaymentReferenceNo = ref('')
const repaymentRemark = ref('')
const repaymentSubmitting = ref(false)
const autoPrintSale = ref(true)
const salePrintMode = ref(DEFAULT_SALE_PRINT_MODE)
const salePrintMethod = ref(DEFAULT_SALE_PRINT_METHOD)
const systemSettingsLoading = ref(false)
const receiptWarehouseInfos = ref([])
const receiptWarehouseInfosLoading = ref(false)
const selectedReceiptWarehouseInfoId = ref('')
const pendingReturnSale = ref(null)
const returnLines = ref([])
const returnReason = ref('')
const returnRefundMethod = ref('CASH')
const returnRefundAmount = ref('')
const returnRefundReferenceNo = ref('')
const returnSubmitting = ref(false)

const {
  lastScan,
  quickScan,
  setScanCallback,
  initScanner,
  unRegisterBroadcast,
} = useBarcodeScanner()

const totalBaseQty = computed(() =>
  cartItems.value.reduce((sum, item) => sum + Number(lineBaseQty(item) || 0), 0)
)
const totalAmount = computed(() =>
  cartItems.value.reduce((sum, item) => sum + Number(lineAmount(item) || 0), 0)
)
const ownerCount = computed(() => {
  const ownerIds = cartItems.value
    .map((item) => item.owner_id)
    .filter((ownerId) => ownerId !== undefined && ownerId !== null && ownerId !== '')
  return new Set(ownerIds).size
})
const paymentMethodLabels = computed(() => paymentMethods.map((method) => method.label))
const repaymentMethods = computed(() => paymentMethods.filter((method) => method.value !== 'CREDIT'))
const repaymentMethodLabels = computed(() => repaymentMethods.value.map((method) => method.label))
const paymentMethodIndex = computed(() =>
  Math.max(0, paymentMethods.findIndex((method) => method.value === paymentMethod.value))
)
const selectedPaymentMethod = computed(() => paymentMethods[paymentMethodIndex.value] || paymentMethods[0])
const repaymentMethodIndex = computed(() =>
  Math.max(0, repaymentMethods.value.findIndex((method) => method.value === repaymentMethod.value))
)
const selectedRepaymentMethod = computed(() => repaymentMethods.value[repaymentMethodIndex.value] || repaymentMethods.value[0])
const salePrintModeLabels = computed(() => SALE_PRINT_MODES.map((mode) => mode.label))
const salePrintModeIndex = computed(() => {
  const index = SALE_PRINT_MODES.findIndex((mode) => mode.value === salePrintMode.value)
  return index >= 0 ? index : 0
})
const selectedSalePrintMode = computed(() => SALE_PRINT_MODES[salePrintModeIndex.value] || SALE_PRINT_MODES[0])
const useBackendSalePrint = computed(() => salePrintMethod.value === SALE_PRINT_METHOD_BACKEND)
const fallbackReceiptWarehouseInfo = computed(() => ({
  id: '',
  name: '默认仓库信息',
  address: SALE_PRINT_SHOP_ADDRESS,
  phone: SALE_PRINT_SHOP_PHONE,
  bank_account: SALE_PRINT_BANK_ACCOUNT,
}))
const receiptWarehouseInfoOptions = computed(() =>
  receiptWarehouseInfos.value.length ? receiptWarehouseInfos.value : [fallbackReceiptWarehouseInfo.value]
)
const receiptWarehouseInfoLabels = computed(() =>
  receiptWarehouseInfoOptions.value.map((info) => info.name || info.warehouse_name || '未命名仓库信息')
)
const selectedReceiptWarehouseInfoIndex = computed(() => {
  const selectedId = String(selectedReceiptWarehouseInfoId.value || '')
  const index = receiptWarehouseInfoOptions.value.findIndex((info) => String(info.id || '') === selectedId)
  return index >= 0 ? index : 0
})
const selectedReceiptWarehouseInfo = computed(
  () => receiptWarehouseInfoOptions.value[selectedReceiptWarehouseInfoIndex.value] || fallbackReceiptWarehouseInfo.value
)
const splitPaymentAmount = computed(() =>
  paymentLines.value.reduce((sum, line) => sum + numberFromValue(line.amount, 0), 0)
)
const splitPaymentReceivedAmount = computed(() =>
  paymentLines.value.reduce((sum, line) => {
    if (line.method === 'CREDIT') return sum
    return sum + numberFromValue(line.amount_received || line.amount, 0)
  }, 0)
)
const splitPaymentChangeAmount = computed(() =>
  paymentLines.value.reduce((sum, line) => {
    if (line.method !== 'CASH') return sum
    return sum + Math.max(numberFromValue(line.amount_received, 0) - numberFromValue(line.amount, 0), 0)
  }, 0)
)
const splitPaymentCreditAmount = computed(() =>
  paymentLines.value.reduce((sum, line) => {
    if (line.method !== 'CREDIT') return sum
    return sum + numberFromValue(line.amount, 0)
  }, 0)
)
const splitPaymentRemaining = computed(() => Math.max(totalAmount.value - splitPaymentAmount.value, 0))
const selectedCustomerName = computed(() =>
  selectedCustomer.value
    ? selectedCustomer.value.name || selectedCustomer.value.code || selectedCustomer.value.id
    : '散客'
)
const saleDateText = computed(() => formatDateTime(saleDate.value))
const receivedAmount = computed(() =>
  splitPaymentEnabled.value
    ? splitPaymentReceivedAmount.value
    : paymentMethod.value === 'CREDIT'
      ? 0
      : Number(amountReceived.value || 0)
)
const creditAmount = computed(() =>
  splitPaymentEnabled.value
    ? splitPaymentCreditAmount.value
    : paymentMethod.value === 'CREDIT'
      ? totalAmount.value
      : 0
)
const changeAmount = computed(() =>
  splitPaymentEnabled.value
    ? splitPaymentChangeAmount.value
    : paymentMethod.value === 'CASH'
      ? Math.max(receivedAmount.value - totalAmount.value, 0)
      : 0
)
const paymentReady = computed(() => {
  if (totalAmount.value <= 0) return false
  if (splitPaymentEnabled.value) {
    if (!paymentLines.value.length || !moneyEqual(splitPaymentAmount.value, totalAmount.value)) return false
    return paymentLines.value.every((line) => {
      const amount = numberFromValue(line.amount, 0)
      const received = numberFromValue(line.amount_received || line.amount, 0)
      if (amount <= 0) return false
      if (line.method === 'CREDIT') {
        return !!selectedCustomer.value && moneyEqual(numberFromValue(line.amount_received, 0), 0)
      }
      return line.method === 'CASH' ? received >= amount : moneyEqual(received, amount)
    })
  }
  if (paymentMethod.value === 'CREDIT') {
    return !!selectedCustomer.value
  }
  if (paymentMethod.value === 'CASH') {
    return receivedAmount.value >= totalAmount.value
  }
  return moneyEqual(receivedAmount.value, totalAmount.value)
})
const returnPaymentMethodIndex = computed(() =>
  Math.max(0, paymentMethods.findIndex((method) => method.value === returnRefundMethod.value))
)
const returnTotalAmount = computed(() =>
  returnLines.value.reduce((sum, line) => {
    const qty = numberFromValue(line.return_qty, 0)
    const price = numberFromValue(line.price, 0)
    return sum + qty * price
  }, 0)
)
const shiftSummary = computed(() => currentShift.value?.summary || {})
const canCheckout = computed(() =>
  cartItems.value.length > 0 && paymentReady.value && isActiveShift(currentShift.value)
)
const historyPreviewSales = computed(() => historySales.value.slice(0, HISTORY_PREVIEW_LIMIT))
const historyMoreSales = computed(() => historySales.value.slice(HISTORY_PREVIEW_LIMIT))
const historyMoreButtonText = computed(() =>
  historyMoreOpen.value ? '收起销售记录' : `更多销售记录（${historyMoreSales.value.length}）`
)
const shiftPaymentRows = computed(() => {
  const rows = Array.isArray(shiftSummary.value.payments) ? shiftSummary.value.payments : []
  return rows.filter((row) => row.method && row.method !== 'CASH' && row.method !== 'CREDIT')
})
const statsSummary = computed(() => posStats.value?.summary || defaultPosStats().summary)
const statsPayments = computed(() => {
  const rows = Array.isArray(posStats.value?.payments) ? posStats.value.payments : []
  return rows.slice(0, 4)
})

onMounted(() => {
  restoreAutoPrintPreference()
  restoreSalePrintModePreference()
  restoreReceiptWarehouseInfoPreference()
  restoreSaleDraft()
  setScanCallback(handleScan)
  initScanner()
  focusProductInput()
  loadSystemSettings()
  loadReceiptWarehouseInfos()
  loadCurrentShift()
  loadShiftHistory({ silent: true })
  loadPosSaleHistory({ silent: true })
  loadPosStats({ silent: true })
})

onShow(() => {
  focusProductInput()
  loadSystemSettings({ silent: true })
  loadReceiptWarehouseInfos({ silent: true })
  refreshCartStock()
  loadCurrentShift()
  loadShiftHistory({ silent: true })
  loadPosSaleHistory({ silent: true })
  loadPosStats({ silent: true })
})

onHide(() => {
  saveSaleDraft()
})

onUnmounted(() => {
  saveSaleDraft()
  productInputFocus.value = false
  unRegisterBroadcast()
})

watch(
  () => [
    cartItems.value,
    selectedCustomer.value,
    customerKeyword.value,
    srcBillNo.value,
    saleDate.value,
    remark.value,
    paymentMethod.value,
    amountReceived.value,
    paymentReferenceNo.value,
    splitPaymentEnabled.value,
    paymentLines.value,
    idempotencyKey.value,
  ],
  () => saveSaleDraft(),
  { deep: true }
)

watch(totalAmount, () => {
  syncNonCashAmount()
  syncSplitPaymentLines()
})

watch(returnTotalAmount, () => {
  if (pendingReturnSale.value) {
    returnRefundAmount.value = plainMoney(returnTotalAmount.value)
  }
})

watch(
  () => selectedCustomer.value?.id || '',
  () => {
    customerDebtBalance.value = 0
    repaymentAmount.value = ''
    repaymentReferenceNo.value = ''
    repaymentRemark.value = ''
    if (selectedCustomer.value?.id) {
      loadCustomerDebt({ silent: true })
    }
  }
)

function makeReceiptNo() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return [
    'POS',
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
    pad(d.getHours()),
    pad(d.getMinutes()),
    pad(d.getSeconds()),
  ].join('')
}

function makeIdempotencyKey() {
  return `${makeReceiptNo()}-${Math.random().toString(16).slice(2, 10)}`
}

function formatDateTime(value) {
  const d = value instanceof Date ? value : new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return [
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
  ].join('-') + ` ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function normalizePage(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

function getStorage() {
  return typeof uni !== 'undefined' ? uni : null
}

function restoreAutoPrintPreference() {
  try {
    const stored = getStorage()?.getStorageSync(POS_AUTO_PRINT_KEY)
    autoPrintSale.value = stored === undefined || stored === null || stored === '' ? true : stored !== false
  } catch (e) {
    autoPrintSale.value = true
  }
}

function saveAutoPrintPreference() {
  try {
    getStorage()?.setStorageSync(POS_AUTO_PRINT_KEY, autoPrintSale.value)
  } catch (e) {
    console.warn('save POS auto print preference failed', e)
  }
}

function isValidSalePrintMode(value) {
  return SALE_PRINT_MODES.some((mode) => mode.value === value)
}

function restoreSalePrintModePreference() {
  try {
    const stored = getStorage()?.getStorageSync(POS_SALE_PRINT_MODE_KEY)
    salePrintMode.value = isValidSalePrintMode(stored) ? stored : DEFAULT_SALE_PRINT_MODE
  } catch (e) {
    salePrintMode.value = DEFAULT_SALE_PRINT_MODE
  }
}

function saveSalePrintModePreference() {
  try {
    getStorage()?.setStorageSync(POS_SALE_PRINT_MODE_KEY, salePrintMode.value)
  } catch (e) {
    console.warn('save POS sale print mode failed', e)
  }
}

function normalizeSalePrintMethod(value) {
  return value === SALE_PRINT_METHOD_BACKEND ? SALE_PRINT_METHOD_BACKEND : DEFAULT_SALE_PRINT_METHOD
}

function applySystemSettings(payload = {}) {
  const grouped = payload.settings || {}
  const flat = payload.flat || {}
  const value =
    flat['pos.sale_print_method'] ||
    grouped.pos?.sale_print_method ||
    DEFAULT_SALE_PRINT_METHOD
  salePrintMethod.value = normalizeSalePrintMethod(value)
}

async function loadSystemSettings(options = {}) {
  if (systemSettingsLoading.value) return
  systemSettingsLoading.value = true
  try {
    applySystemSettings(await api.systemSettings())
  } catch (e) {
    salePrintMethod.value = DEFAULT_SALE_PRINT_METHOD
    if (!options.silent) {
      console.warn('load system settings failed', e)
    }
  } finally {
    systemSettingsLoading.value = false
  }
}

function normalizeReceiptWarehouseInfo(info = {}) {
  return {
    id: info.id ?? '',
    warehouse_id: info.warehouse_id ?? null,
    warehouse_name: info.warehouse_name || '',
    name: info.name || info.warehouse_name || '未命名仓库信息',
    address: info.address || '',
    phone: info.phone || '',
    bank_account: info.bank_account || '',
    is_default: !!info.is_default,
  }
}

function restoreReceiptWarehouseInfoPreference() {
  try {
    selectedReceiptWarehouseInfoId.value = String(
      getStorage()?.getStorageSync(POS_RECEIPT_WAREHOUSE_INFO_KEY) || ''
    )
  } catch (e) {
    selectedReceiptWarehouseInfoId.value = ''
  }
}

function saveReceiptWarehouseInfoPreference() {
  try {
    getStorage()?.setStorageSync(
      POS_RECEIPT_WAREHOUSE_INFO_KEY,
      selectedReceiptWarehouseInfoId.value || ''
    )
  } catch (e) {
    console.warn('save POS receipt warehouse info failed', e)
  }
}

async function loadReceiptWarehouseInfos(options = {}) {
  if (receiptWarehouseInfosLoading.value) return
  receiptWarehouseInfosLoading.value = true
  try {
    const rows = normalizePage(await api.posReceiptWarehouseInfos())
    receiptWarehouseInfos.value = rows.map(normalizeReceiptWarehouseInfo)
    const currentId = String(selectedReceiptWarehouseInfoId.value || '')
    const currentExists = receiptWarehouseInfos.value.some((info) => String(info.id || '') === currentId)
    if (!currentExists) {
      const defaultInfo = receiptWarehouseInfos.value.find((info) => info.is_default)
      selectedReceiptWarehouseInfoId.value = defaultInfo?.id
        ? String(defaultInfo.id)
        : String(receiptWarehouseInfos.value[0]?.id || '')
      saveReceiptWarehouseInfoPreference()
    }
  } catch (e) {
    if (!options.silent) {
      console.warn('load POS receipt warehouse info failed', e)
    }
  } finally {
    receiptWarehouseInfosLoading.value = false
  }
}

function onAutoPrintChange(event) {
  const values = Array.isArray(event?.detail?.value) ? event.detail.value : []
  autoPrintSale.value = values.includes('auto')
  saveAutoPrintPreference()
}

function changeSalePrintMode(event) {
  const index = Number(event?.detail?.value || 0)
  salePrintMode.value = SALE_PRINT_MODES[index]?.value || DEFAULT_SALE_PRINT_MODE
  saveSalePrintModePreference()
}

function changeReceiptWarehouseInfo(event) {
  const index = Number(event?.detail?.value || 0)
  const info = receiptWarehouseInfoOptions.value[index] || fallbackReceiptWarehouseInfo.value
  selectedReceiptWarehouseInfoId.value = String(info.id || '')
  saveReceiptWarehouseInfoPreference()
}

function removeSaleDraft() {
  try {
    getStorage()?.removeStorageSync(POS_DRAFT_KEY)
  } catch (e) {
    console.warn('remove POS draft failed', e)
  }
}

function saveSaleDraft() {
  try {
    const storage = getStorage()
    if (!storage) return

    if (!cartItems.value.length) {
      storage.removeStorageSync(POS_DRAFT_KEY)
      return
    }

    const draftItems = JSON.parse(JSON.stringify(cartItems.value))
    const draftCustomer = selectedCustomer.value ? JSON.parse(JSON.stringify(selectedCustomer.value)) : null

    storage.setStorageSync(POS_DRAFT_KEY, {
      saved_at: Date.now(),
      cart_items: draftItems,
      selected_customer: draftCustomer,
      customer_keyword: customerKeyword.value || '',
      src_bill_no: srcBillNo.value || '',
      sale_date: saleDate.value instanceof Date ? saleDate.value.toISOString() : saleDate.value,
      remark: remark.value || '',
      payment_method: paymentMethod.value || 'CASH',
      amount_received: amountReceived.value || '',
      payment_reference_no: paymentReferenceNo.value || '',
      split_payment_enabled: splitPaymentEnabled.value,
      payment_lines: JSON.parse(JSON.stringify(paymentLines.value || [])),
      idempotency_key: idempotencyKey.value || '',
    })
  } catch (e) {
    console.warn('save POS draft failed', e)
  }
}

function restoreSaleDraft() {
  try {
    const draft = getStorage()?.getStorageSync(POS_DRAFT_KEY)
    if (!draft || !Array.isArray(draft.cart_items) || !draft.cart_items.length) return

    const savedAt = Number(draft.saved_at || 0)
    if (savedAt && Date.now() - savedAt > 24 * 60 * 60 * 1000) {
      removeSaleDraft()
      return
    }

    cartItems.value = draft.cart_items.map(normalizeDraftCartItem)
    selectedCustomer.value = draft.selected_customer || null
    customerKeyword.value = draft.customer_keyword || ''
    srcBillNo.value = draft.src_bill_no || makeReceiptNo()
    const draftDate = draft.sale_date ? new Date(draft.sale_date) : new Date()
    saleDate.value = Number.isNaN(draftDate.getTime()) ? new Date() : draftDate
    remark.value = draft.remark || ''
    paymentMethod.value = draft.payment_method || 'WECHAT'
    amountReceived.value = draft.amount_received || ''
    paymentReferenceNo.value = draft.payment_reference_no || ''
    splitPaymentEnabled.value = !!draft.split_payment_enabled
    paymentLines.value = Array.isArray(draft.payment_lines)
      ? draft.payment_lines.map((line) => ({
          method: line.method || 'CASH',
          amount: line.amount || '',
          amount_received: line.amount_received || line.amount || '',
          reference_no: line.reference_no || '',
        }))
      : []
    idempotencyKey.value = draft.idempotency_key || makeIdempotencyKey()
    syncNonCashAmount()
    syncSplitPaymentLines()
    setScanFeedback(`已恢复未结账购物车：${cartItems.value.length}项`, 'info')
    refreshCartStock({ force: true, silent: true })
  } catch (e) {
    console.warn('restore POS draft failed', e)
    removeSaleDraft()
  }
}

function normalizeDraftCartItem(item) {
  const options = Array.isArray(item.unit_options) && item.unit_options.length
    ? item.unit_options
    : [{
        kind: 'base',
        package_id: null,
        label: item.base_unit_name || '基本单位',
        multiplier: '1',
        barcode: '',
      }]
  const unitLabels = Array.isArray(item.unit_labels) && item.unit_labels.length
    ? item.unit_labels
    : options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  const unitIndex = Math.min(Math.max(Number(item.unit_index || 0), 0), Math.max(unitLabels.length - 1, 0))

  return {
    ...item,
    available_qty: item.available_qty || 0,
    qty: String(item.qty || '1'),
    price: priceInputText(item.price),
    unit_options: options,
    unit_labels: unitLabels,
    unit_index: unitIndex,
  }
}

function money(value) {
  const n = Number(value || 0)
  return `¥${Number.isFinite(n) ? n.toFixed(2) : '0.00'}`
}

function priceInputText(value) {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

function numberFromValue(value, fallback = 0) {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'number') return Number.isFinite(value) ? value : fallback

  const text = String(value).replace(/,/g, '').trim()
  const matched = text.match(/-?\d+(\.\d+)?/)
  if (!matched) return fallback

  const n = Number(matched[0])
  return Number.isFinite(n) ? n : fallback
}

function stockAvailableQty(source) {
  if (!source || typeof source !== 'object') return 0

  const candidates = [
    source.pos_available_qty,
    source.sale_available_qty,
    source.sales_available_qty,
    source.picking_available_qty,
    source.pick_available_qty,
    source.available_qty_display,
    source.available_qty,
    source.available,
  ]

  for (const value of candidates) {
    if (value !== undefined && value !== null && value !== '') {
      return Math.max(numberFromValue(value, 0), 0)
    }
  }

  return 0
}

function paymentMethodName(value) {
  return paymentMethods.find((method) => method.value === value)?.label || value || '-'
}

function defaultPaymentLine(method = 'CASH', amount = '') {
  const normalizedAmount = amount === '' ? '' : plainMoney(amount)
  return {
    method,
    amount: normalizedAmount,
    amount_received: normalizedAmount,
    reference_no: '',
  }
}

function paymentLineMethodIndex(line = {}) {
  return Math.max(0, paymentMethods.findIndex((method) => method.value === line.method))
}

function ensurePaymentLines() {
  if (paymentLines.value.length) return
  paymentLines.value = [defaultPaymentLine(paymentMethod.value || 'CASH', totalAmount.value)]
}

function onSplitPaymentChange(event) {
  const values = Array.isArray(event?.detail?.value) ? event.detail.value : []
  splitPaymentEnabled.value = values.includes('split')
  if (splitPaymentEnabled.value) {
    ensurePaymentLines()
    syncSplitPaymentLines()
  } else {
    syncNonCashAmount()
  }
}

function addPaymentLine() {
  paymentLines.value.push(defaultPaymentLine('CASH', splitPaymentRemaining.value || ''))
  syncSplitPaymentLines()
}

function removePaymentLine(index) {
  if (paymentLines.value.length <= 1) return
  paymentLines.value.splice(index, 1)
}

function changePaymentLineMethod(index, event) {
  const method = paymentMethods[Number(event?.detail?.value || 0)]?.value || 'CASH'
  const line = paymentLines.value[index]
  if (!line) return
  line.method = method
  normalizePaymentLine(index)
}

function normalizePaymentLine(index) {
  const line = paymentLines.value[index]
  if (!line) return
  const amount = numberFromValue(line.amount, 0)
  line.amount = amount > 0 ? plainMoney(amount) : ''
  if (line.method === 'CREDIT') {
    line.amount_received = '0.00'
  } else if (line.method === 'CASH') {
    const received = numberFromValue(line.amount_received || line.amount, amount)
    line.amount_received = received > 0 ? plainMoney(Math.max(received, amount)) : line.amount
  } else {
    line.amount_received = line.amount
  }
}

function syncSplitPaymentLines() {
  if (!splitPaymentEnabled.value) return
  ensurePaymentLines()
  paymentLines.value.forEach((line, index) => {
    if (line.method === 'CREDIT') {
      line.amount_received = '0.00'
    } else if (line.method !== 'CASH') {
      const amount = numberFromValue(line.amount, 0)
      if (amount > 0) line.amount_received = plainMoney(amount)
    }
    if (index === 0 && paymentLines.value.length === 1 && totalAmount.value > 0) {
      line.amount = plainMoney(totalAmount.value)
      if (line.method === 'CREDIT') {
        line.amount_received = '0.00'
      } else if (line.method !== 'CASH') {
        line.amount_received = line.amount
      }
      if (!line.amount_received) line.amount_received = line.amount
    }
  })
}

function plainMoney(value) {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

function moneyEqual(a, b) {
  return Math.abs(Number(a || 0) - Number(b || 0)) < 0.005
}

function syncNonCashAmount() {
  if (splitPaymentEnabled.value) {
    syncSplitPaymentLines()
    return
  }
  if (paymentMethod.value === 'CREDIT') {
    amountReceived.value = '0.00'
  } else if (paymentMethod.value !== 'CASH') {
    amountReceived.value = plainMoney(totalAmount.value)
  }
}

function confirmDialog({ title, content, confirmText = '确定', cancelText = '取消' }) {
  return new Promise((resolve) => {
    uni.showModal({
      title,
      content,
      confirmText,
      cancelText,
      success: (res) => resolve(!!res.confirm),
      fail: () => resolve(false),
    })
  })
}

function errorMessage(error, fallback = '操作失败') {
  if (!error) return fallback
  if (typeof error === 'string') return error
  if (typeof error.message === 'string' && error.message) return error.message
  const data = error.data || error.response || error
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data[0] || fallback
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail) && data.detail.length) return data.detail[0]
  for (const key in data) {
    const value = data[key]
    if (Array.isArray(value) && value.length) return value[0]
    if (typeof value === 'string' && value) return value
  }
  return fallback
}

function showPosError(error, fallback = 'POS 操作失败') {
  const message = errorMessage(error, fallback)
  setScanFeedback(message, 'error')
  uni.showModal({
    title: '操作失败',
    content: message,
    showCancel: false,
  })
}

function plainQty(value, context) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return '0'
  if (isCountItem(context) || Number.isInteger(n)) return String(Math.trunc(n))
  return String(Number(n.toFixed(3)))
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function dateOnly(value) {
  if (!value) return formatDateTime(new Date()).slice(0, 10)
  const text = String(value)
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.slice(0, 10)
  return formatDateTime(value).slice(0, 10)
}

function amountToChinese(value) {
  const digits = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
  const units = ['', '拾', '佰', '仟']
  const groups = ['', '万', '亿']
  const n = Math.round(Number(value || 0) * 100)
  if (!Number.isFinite(n) || n <= 0) return '零元整'

  const integer = Math.floor(n / 100)
  const jiao = Math.floor((n % 100) / 10)
  const fen = n % 10

  function sectionToChinese(section) {
    let str = ''
    let zero = false
    for (let i = 0; i < 4; i++) {
      const d = section % 10
      if (d === 0) {
        if (str) zero = true
      } else {
        if (zero) {
          str = digits[0] + str
          zero = false
        }
        str = digits[d] + units[i] + str
      }
      section = Math.floor(section / 10)
    }
    return str
  }

  let intText = ''
  let rest = integer
  let groupIndex = 0
  let needZero = false
  while (rest > 0) {
    const section = rest % 10000
    if (section === 0) {
      needZero = !!intText
    } else {
      let sectionText = sectionToChinese(section) + groups[groupIndex]
      if (needZero || (section < 1000 && intText)) {
        sectionText = digits[0] + sectionText
      }
      intText = sectionText + intText
      needZero = false
    }
    rest = Math.floor(rest / 10000)
    groupIndex += 1
  }

  let result = `${intText || digits[0]}元`
  if (!jiao && !fen) return `${result}整`
  if (jiao) result += `${digits[jiao]}角`
  if (fen) result += `${digits[fen]}分`
  return result
}

function buildSalePrintData(response = {}) {
  const receipt = response.receipt || {}
  const sale = response.sale || (response.sale_no || response.id ? response : {})
  const payment = receipt.payment || response.payment || sale.payment || {}
  const receiptWarehouseInfo = selectedReceiptWarehouseInfo.value || fallbackReceiptWarehouseInfo.value
  const receiptCompanyName = receiptWarehouseInfo.id
    ? receiptWarehouseInfo.name || receiptWarehouseInfo.warehouse_name || SALE_PRINT_COMPANY_NAME
    : SALE_PRINT_COMPANY_NAME
  const orders = Array.isArray(response.orders)
    ? response.orders
    : Array.isArray(receipt.orders)
      ? receipt.orders
      : []
  const customer = response.customer || receipt.customer || (cartItems.value.length ? selectedCustomer.value || {} : {})
  const receiptLines = Array.isArray(receipt.lines)
    ? receipt.lines
    : Array.isArray(response.lines)
      ? response.lines
      : []
  const lines = cartItems.value.length
    ? cartItems.value.map((item, index) => {
        const qty = Number(lineBaseQty(item) || 0)
        const price = Number(item.price || 0)
        const amount = Number(lineAmount(item) || 0)
        const unit = item.base_unit_name || item.unit_labels?.[item.unit_index] || ''
        return {
          index: index + 1,
          name: item.name || item.code || item.sku || '',
          code: item.code || item.sku || '',
          spec: item.spec || item.product_spec || selectedUnit(item).label || '',
          unit,
          qty,
          qtyText: plainQty(qty, item),
          price,
          priceText: plainMoney(price),
          amount,
          amountText: plainMoney(amount),
          remark: '',
          locationCode: item.location_code || '',
        }
      })
    : receiptLines.map((line, index) => {
        const qty = numberFromValue(line.qty, 0)
        const price = numberFromValue(line.price, 0)
        const amount = numberFromValue(line.amount, qty * price)
        return {
          index: index + 1,
          name: line.name || line.product_name || line.code || line.product_code || '',
          code: line.code || line.product_code || '',
          spec: line.spec || '',
          unit: line.unit || '',
          qty,
          qtyText: plainQty(qty),
          price,
          priceText: plainMoney(price),
          amount,
          amountText: plainMoney(amount),
          remark: '',
          locationCode: line.location_code || '',
        }
      })
  const totalQty = lines.reduce((sum, line) => sum + Number(line.qty || 0), 0)
  const total = Number(receipt.total_amount ?? sale.total_amount ?? totalAmount.value ?? 0)
  const amountReceivedValue = numberFromValue(payment.amount_received ?? receivedAmount.value, 0)
  const changeValue = numberFromValue(payment.change_amount ?? changeAmount.value, 0)
  const creditAmountValue = numberFromValue(
    receipt.credit_amount ?? payment.credit_amount ?? sale.credit_amount,
    creditAmount.value
  )
  const cumulativeDebtValue = numberFromValue(
    receipt.cumulative_debt ??
      sale.cumulative_debt ??
      response.cumulative_debt ??
      customer.cumulative_debt ??
      customer.debt_balance,
    creditAmountValue
  )
  const billNo =
    sale.sale_no ||
    receipt.sale_no ||
    receipt.src_bill_no ||
    srcBillNo.value ||
    orders[0]?.order_no ||
    orders[0]?.id ||
    ''

  return {
    saleId: sale.id || receipt.sale_id || response.sale_id || null,
    companyName: receiptCompanyName,
    title: '销售单',
    billNo,
    billDate: dateOnly(sale.created_at || receipt.created_at || saleDate.value),
    customerName: customer.name || customer.code || '散客',
    customerAddress: customer.address || customer.full_address || '',
    customerPhone: customer.phone || customer.mobile || '',
    receiptWarehouseInfoId: receiptWarehouseInfo.id || '',
    receiptWarehouseInfoName: receiptWarehouseInfo.name || '',
    shopAddress: receiptWarehouseInfo.address || '',
    shopPhone: receiptWarehouseInfo.phone || '',
    bankAccount: receiptWarehouseInfo.bank_account || '',
    lines,
    totalQty,
    totalQtyText: plainQty(totalQty),
    totalAmount: total,
    totalAmountText: plainMoney(total),
    totalAmountChinese: amountToChinese(total),
    amountReceived: amountReceivedValue,
    amountReceivedText: plainMoney(amountReceivedValue),
    changeAmount: changeValue,
    creditAmount: creditAmountValue,
    creditAmountText: plainMoney(creditAmountValue),
    cumulativeDebt: cumulativeDebtValue,
    cumulativeDebtText: plainMoney(cumulativeDebtValue),
    remark: receipt.remark || sale.remark || remark.value || '',
    orderNos: orders.map((order) => order.order_no || order.id).filter(Boolean).join('、'),
  }
}

function isDotMatrixPrintMode(printMode) {
  return printMode === 'dot_241_93' || printMode === 'dot_9_5_5' || printMode === 'dot_9_5_11'
}

function buildSalePrintHtml(data, printMode = salePrintMode.value) {
  if (isDotMatrixPrintMode(printMode)) {
    return buildDotMatrixSalePrintHtml(data, printMode)
  }
  return buildA4SalePrintHtml(data)
}

function buildA4SalePrintHtml(data) {
  const rows = data.lines.map((line) => `
    <tr>
      <td class="name">${escapeHtml(line.name)}</td>
      <td>${escapeHtml(line.spec)}</td>
      <td>${escapeHtml(line.unit)}</td>
      <td class="num">${escapeHtml(line.qtyText)}</td>
      <td class="num">${escapeHtml(line.priceText)}</td>
      <td class="num">${escapeHtml(line.amountText)}</td>
      <td>${escapeHtml(line.remark)}</td>
      <td>${escapeHtml(line.locationCode)}</td>
    </tr>
  `).join('')

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(data.companyName)}${escapeHtml(data.title)}</title>
  <style>
    @page { size: A4 landscape; margin: 0; }
    * { box-sizing: border-box; }
    body { margin: 0; color: #111; font-family: SimSun, "Microsoft YaHei", Arial, sans-serif; font-size: 22.5px; }
    .sheet { width: 98%; margin: 0 auto; padding: 1mm 4px 0; }
    .company { text-align: center; font-size: 42px; font-weight: 700; line-height: 1.05; }
    .title { text-align: center; font-size: 27px; line-height: 1.05; margin-bottom: 2px; }
    .meta { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 18px; font-size: 22.5px; line-height: 1.2; margin-bottom: 2px; }
    .meta .wide { grid-column: 1 / -1; }
    table { width: 100%; border-collapse: collapse; border-spacing: 0; table-layout: fixed; font-size: 22.5px; }
    th, td { border: 1px solid #111; padding: 1px 3px; line-height: 1.05; vertical-align: middle; word-break: break-all; }
    th { text-align: center; font-weight: 400; font-size: 22.5px; }
    .name { text-align: left; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .summary-name { text-align: left; }
    .money-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 14px; margin-top: 8px; font-size: 22.5px; line-height: 1.2; }
    .footer-line { margin-top: 1px; font-size: 22.5px; line-height: 1.18; white-space: normal; }
  </style>
</head>
<body>
  <div class="sheet">
    <div class="company">${escapeHtml(data.companyName)}</div>
    <div class="title">${escapeHtml(data.title)}</div>
    <div class="meta">
      <div>客户名称：${escapeHtml(data.customerName)}</div>
      <div>单据日期：${escapeHtml(data.billDate)}</div>
      <div>单据编号：${escapeHtml(data.billNo)}</div>
      <div class="wide">客户地址：${escapeHtml(data.customerAddress)}</div>
    </div>
    <table>
      <colgroup>
        <col style="width: 36%" />
        <col style="width: 14%" />
        <col style="width: 5%" />
        <col style="width: 7%" />
        <col style="width: 8%" />
        <col style="width: 11%" />
        <col style="width: 7%" />
        <col style="width: 12%" />
      </colgroup>
      <thead>
        <tr>
          <th>商品名称</th>
          <th>规格</th>
          <th>单位</th>
          <th>数量</th>
          <th>单价</th>
          <th>金额</th>
          <th>备注</th>
          <th>库位</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
        <tr>
          <td colspan="3" class="summary-name">本页小计</td>
          <td class="num">${escapeHtml(data.totalQtyText)}</td>
          <td></td>
          <td class="num">${escapeHtml(data.totalAmountText)}</td>
          <td colspan="2"></td>
        </tr>
        <tr>
          <td colspan="3" class="summary-name">合计: ${escapeHtml(data.totalAmountChinese)}</td>
          <td class="num">${escapeHtml(data.totalQtyText)}</td>
          <td></td>
          <td class="num">${escapeHtml(data.totalAmountText)}</td>
          <td colspan="2"></td>
        </tr>
      </tbody>
    </table>
    <div class="money-row">
      <div>本单应收：${escapeHtml(data.totalAmountText)}</div>
      <div>本单实收：${escapeHtml(data.amountReceivedText)}</div>
      <div>本单赊账：${escapeHtml(data.creditAmountText)}</div>
      <div>累计欠款：${escapeHtml(data.cumulativeDebtText)}</div>
    </div>
    <div class="footer-line">店铺地址：${escapeHtml(data.shopAddress || '')}　　店铺电话：${escapeHtml(data.shopPhone || '')}</div>
    <div class="footer-line">银行账号：${escapeHtml(data.bankAccount || '')}</div>
    <div class="footer-line">备注：${escapeHtml(data.remark || '无')}</div>
  </div>
</body>
</html>`
}

function dotMatrixPageCss(printMode) {
  if (printMode === 'dot_241_93') {
    return '@page { size: 241mm 93mm; margin: 0; }'
  }
  if (printMode === 'dot_9_5_11') {
    return '@page { size: 9.5in 11in; margin: 0; }'
  }
  return '@page { size: 9.5in 5.5in; margin: 0; }'
}

function buildDotMatrixSalePrintHtml(data, printMode) {
  const rows = data.lines.map((line) => `
    <tr>
      <td class="name">${escapeHtml(line.name)}</td>
      <td>${escapeHtml(line.spec)}</td>
      <td>${escapeHtml(line.unit)}</td>
      <td class="num">${escapeHtml(line.qtyText)}</td>
      <td class="num">${escapeHtml(line.priceText)}</td>
      <td class="num">${escapeHtml(line.amountText)}</td>
      <td>${escapeHtml(line.remark)}</td>
      <td>${escapeHtml(line.locationCode)}</td>
    </tr>
  `).join('')

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(data.companyName)}${escapeHtml(data.title)}</title>
  <style>
    ${dotMatrixPageCss(printMode)}
    * { box-sizing: border-box; }
    body { margin: 0; color: #111; font-family: SimSun, "Microsoft YaHei", Arial, sans-serif; font-size: 18px; }
    .sheet { width: 98%; margin: 0 auto; padding: 1mm 0 0; }
    .company { text-align: center; font-size: 30px; font-weight: 700; line-height: 1.05; }
    .title { text-align: center; font-size: 21px; line-height: 1.05; margin-bottom: 2px; }
    .meta { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 8px; font-size: 18px; line-height: 1.15; margin-bottom: 2px; }
    .meta .wide { grid-column: 1 / -1; }
    table { width: 100%; border-collapse: collapse; border-spacing: 0; table-layout: fixed; font-size: 18px; }
    th, td { border: 1px solid #111; padding: 1px 2px; line-height: 1.05; vertical-align: middle; word-break: break-all; }
    th { text-align: center; font-weight: 400; font-size: 18px; }
    .name { text-align: left; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .summary-name { text-align: left; }
    .money-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 6px; margin-top: 5px; font-size: 18px; line-height: 1.15; }
    .footer-line { margin-top: 1px; font-size: 18px; line-height: 1.12; white-space: normal; }
  </style>
</head>
<body>
  <div class="sheet">
    <div class="company">${escapeHtml(data.companyName)}</div>
    <div class="title">${escapeHtml(data.title)}</div>
    <div class="meta">
      <div>客户名称：${escapeHtml(data.customerName)}</div>
      <div>单据日期：${escapeHtml(data.billDate)}</div>
      <div>单据编号：${escapeHtml(data.billNo)}</div>
      <div class="wide">客户地址：${escapeHtml(data.customerAddress)}</div>
    </div>
    <table>
      <colgroup>
        <col style="width: 36%" />
        <col style="width: 14%" />
        <col style="width: 5%" />
        <col style="width: 7%" />
        <col style="width: 8%" />
        <col style="width: 11%" />
        <col style="width: 7%" />
        <col style="width: 12%" />
      </colgroup>
      <thead>
        <tr>
          <th>商品名称</th>
          <th>规格</th>
          <th>单位</th>
          <th>数量</th>
          <th>单价</th>
          <th>金额</th>
          <th>备注</th>
          <th>库位</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
        <tr>
          <td colspan="3" class="summary-name">本页小计</td>
          <td class="num">${escapeHtml(data.totalQtyText)}</td>
          <td></td>
          <td class="num">${escapeHtml(data.totalAmountText)}</td>
          <td colspan="2"></td>
        </tr>
        <tr>
          <td colspan="3" class="summary-name">合计: ${escapeHtml(data.totalAmountChinese)}</td>
          <td class="num">${escapeHtml(data.totalQtyText)}</td>
          <td></td>
          <td class="num">${escapeHtml(data.totalAmountText)}</td>
          <td colspan="2"></td>
        </tr>
      </tbody>
    </table>
    <div class="money-row">
      <div>本单应收：${escapeHtml(data.totalAmountText)}</div>
      <div>本单实收：${escapeHtml(data.amountReceivedText)}</div>
      <div>本单赊账：${escapeHtml(data.creditAmountText)}</div>
      <div>累计欠款：${escapeHtml(data.cumulativeDebtText)}</div>
    </div>
    <div class="footer-line">店铺地址：${escapeHtml(data.shopAddress || '')}　店铺电话：${escapeHtml(data.shopPhone || '')}</div>
    <div class="footer-line">银行账号：${escapeHtml(data.bankAccount || '')}</div>
    <div class="footer-line">备注：${escapeHtml(data.remark || '无')}</div>
  </div>
</body>
</html>`
}

function openSalePrintWindow() {
  if (typeof window === 'undefined' || typeof window.open !== 'function') return null
  try {
    return window.open('', '_blank', 'width=1200,height=800')
  } catch (e) {
    return null
  }
}

function printSaleDocument(data, preparedWindow = null) {
  const html = buildSalePrintHtml(data)
  const win = preparedWindow && !preparedWindow.closed ? preparedWindow : openSalePrintWindow()
  if (!win) {
    uni.showToast({ title: '浏览器阻止打印窗口，请手动打印销售单', icon: 'none' })
    return false
  }

  win.document.open()
  win.document.write(html)
  win.document.close()
  setTimeout(() => {
    try {
      win.focus()
      win.print()
    } catch (e) {
      console.warn('print sale document failed', e)
    }
  }, 300)
  return true
}

function printSaleDocumentBackend(data) {
  const saleId = data?.saleId
  if (!saleId) {
    uni.showToast({ title: '销售单缺少ID，无法后端打印', icon: 'none' })
    return false
  }
  openAuthenticatedUrl(api.posSalePrintUrl(saleId))
  return true
}

async function printSaleBySystemSetting(
  data,
  preparedWindow = null,
  frontendRemark = '前端HTML打印'
) {
  if (useBackendSalePrint.value) {
    return printSaleDocumentBackend(data)
  }
  if (printSaleDocument(data, preparedWindow)) {
    await recordFrontendSalePrint(data, frontendRemark)
    return true
  }
  return false
}

async function recordFrontendSalePrint(data, remark = '前端HTML打印') {
  const saleId = data?.saleId
  if (!saleId) return
  try {
    await api.posSalePrintLog(saleId, { remark })
  } catch (e) {
    uni.showToast({ title: '销售单已打印，打印日志记录失败', icon: 'none' })
    console.warn('record POS frontend print log failed', e)
  }
}

async function printLastSale() {
  if (!lastSalePrintData.value) {
    uni.showToast({ title: '没有可打印的销售单', icon: 'none' })
    return
  }
  await printSaleBySystemSetting(lastSalePrintData.value, null, '最近小票前端打印')
}

function normalizePosSaleRows(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

function compactPosNo(value) {
  const text = String(value || '')
  const oldMatch = text.match(/^POS(\d{4})(\d{2})(\d{2})(\d{6})/)
  if (oldMatch) {
    return `P${oldMatch[1].slice(2)}${oldMatch[2]}${oldMatch[3]}${oldMatch[4]}`
  }
  return text
}

function saleDisplayNo(sale = {}) {
  return compactPosNo(sale.sale_no || sale.src_bill_no || String(sale.id || ''))
}

function saleStatusText(status) {
  if (status === 'VOIDED') return '已作废'
  if (status === 'COMPLETED') return '已完成'
  return status || '-'
}

function isVoidedSale(sale = {}) {
  return sale.status === 'VOIDED'
}

function salePaymentMethod(sale = {}) {
  return paymentMethodName(sale.payment?.method || sale.receipt?.payment?.method)
}

function saleCreditAmount(sale = {}) {
  return numberFromValue(
    sale.receipt?.credit_amount ??
      sale.receipt?.payment?.credit_amount ??
      sale.payment?.credit_amount ??
      sale.credit_amount,
    0
  )
}

function saleCreatedText(sale = {}) {
  return sale.created_at ? formatDateTime(sale.created_at) : '-'
}

function saleOrderCount(sale = {}) {
  const orders = Array.isArray(sale.orders)
    ? sale.orders
    : Array.isArray(sale.receipt?.orders)
      ? sale.receipt.orders
      : []
  return orders.length
}

function orderNosText(orders = []) {
  if (!Array.isArray(orders)) return ''
  return orders.map((order) => order.order_no || order.id).filter(Boolean).join('、')
}

async function loadPosSaleHistory(options = {}) {
  if (historyLoading.value && !options.force) return
  historyLoading.value = true
  try {
    const res = await api.posSales({
      search: historyKeyword.value || '',
      page: 1,
      page_size: 10,
    })
    historySales.value = normalizePosSaleRows(res)
    historyMoreOpen.value = false
  } catch (e) {
    if (!options.silent) {
      showPosError(e, '销售历史加载失败')
    }
  } finally {
    historyLoading.value = false
  }
}

function toggleHistoryMore() {
  historyMoreOpen.value = !historyMoreOpen.value
}

function defaultPosStats() {
  return {
    summary: {
      sale_count: 0,
      completed_count: 0,
      voided_count: 0,
      gross_amount: '0.00',
      sales_amount: '0.00',
      return_amount: '0.00',
      net_amount: '0.00',
      voided_amount: '0.00',
      received_amount: '0.00',
      credit_amount: '0.00',
      repayment_amount: '0.00',
    },
    payments: [],
    owners: [],
    products: [],
    cashiers: [],
  }
}

async function loadPosStats(options = {}) {
  if (statsLoading.value && !options.force) return
  statsLoading.value = true
  try {
    const today = dateOnly(new Date())
    const res = await api.posStats({
      start_date: today,
      end_date: today,
      top_n: 5,
    })
    posStats.value = res || defaultPosStats()
  } catch (e) {
    if (!options.silent) {
      showPosError(e, 'POS 统计加载失败')
    }
  } finally {
    statsLoading.value = false
  }
}

function shiftStatusText(status) {
  if (status === 'OPEN') return '进行中'
  if (status === 'CLOSED') return '已交班'
  if (status === 'REOPENED') return '已重开'
  return status || '-'
}

function isActiveShift(shift) {
  return shift?.status === 'OPEN' || shift?.status === 'REOPENED'
}

function syncShiftActualInputs(shift) {
  const summary = shift?.summary || {}
  shiftActualCashAmount.value = summary.expected_cash_amount || shift?.actual_cash_amount || ''
  const next = {}
  const rows = Array.isArray(summary.payments) ? summary.payments : []
  rows.forEach((row) => {
    if (row.method && row.method !== 'CASH' && row.method !== 'CREDIT') {
      next[row.method] = plainMoney(row.actual_amount ?? row.expected_amount)
    }
  })
  shiftPaymentActuals.value = next
}

function paymentActualDiff(row) {
  const expected = numberFromValue(row?.expected_amount, 0)
  const actual = numberFromValue(
    shiftPaymentActuals.value[row?.method],
    expected
  )
  return plainMoney(actual - expected)
}

function authHeader() {
  try {
    const token = uni.getStorageSync('access') || ''
    return token ? { Authorization: `Bearer ${token}` } : {}
  } catch (e) {
    return {}
  }
}

function openAuthenticatedUrl(url) {
  const target = api.authUrl(url)
  if (typeof window !== 'undefined' && typeof window.open === 'function') {
    window.open(target, '_blank')
    return
  }
  if (typeof plus !== 'undefined' && plus.runtime?.openURL) {
    plus.runtime.openURL(target)
    return
  }
  uni.showToast({ title: '当前环境不支持打开打印页', icon: 'none' })
}

function downloadExcel(url, filename = 'pos.xlsx') {
  if (typeof uni.downloadFile !== 'function') {
    if (typeof window !== 'undefined' && window.open) {
      window.open(api.authUrl(url), '_blank')
    }
    return
  }
  uni.downloadFile({
    url,
    header: authHeader(),
    success: (res) => {
      if (res.statusCode && res.statusCode !== 200) {
        uni.showToast({ title: '导出失败', icon: 'none' })
        return
      }
      const filePath = res.tempFilePath
      if (typeof uni.openDocument === 'function') {
        uni.openDocument({
          filePath,
          fileType: 'xlsx',
          showMenu: true,
          fail: () => {
            uni.showToast({ title: `${filename} 已下载`, icon: 'none' })
          },
        })
      } else {
        uni.showToast({ title: `${filename} 已下载`, icon: 'none' })
      }
    },
    fail: () => {
      uni.showToast({ title: '导出失败', icon: 'none' })
    },
  })
}

function exportTodayStats() {
  const today = dateOnly(new Date())
  downloadExcel(api.posStatsExport({ start_date: today, end_date: today, top_n: 50 }), 'pos-stats.xlsx')
}

function exportSalesHistory() {
  downloadExcel(
    api.posSalesExport({ search: historyKeyword.value || '', page_size: 500 }),
    'pos-sales.xlsx'
  )
}

function exportCurrentShift() {
  if (!currentShift.value?.id) return
  downloadExcel(api.posShiftExportUrl(currentShift.value.id), `${currentShift.value.shift_no || 'pos-shift'}.xlsx`)
}

function printCurrentShift() {
  if (!currentShift.value?.id) return
  printShift(currentShift.value)
}

function printShift(shift) {
  if (!shift?.id) return
  openAuthenticatedUrl(api.posShiftPrintUrl(shift.id))
}

function exportShift(shift) {
  if (!shift?.id) return
  downloadExcel(api.posShiftExportUrl(shift.id), `${shift.shift_no || 'pos-shift'}.xlsx`)
}

async function loadCurrentShift() {
  if (shiftLoading.value) return
  shiftLoading.value = true
  try {
    const res = await api.posShiftCurrent()
    currentShift.value = res.shift || null
    syncShiftActualInputs(currentShift.value)
  } catch (e) {
    showPosError(e, '班次加载失败')
  } finally {
    shiftLoading.value = false
  }
}

function normalizeShiftRows(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

async function loadShiftHistory(options = {}) {
  if (shiftHistoryLoading.value && !options.force) return
  shiftHistoryLoading.value = true
  try {
    const res = await api.posShifts({ page: 1, page_size: 10 })
    shiftHistory.value = normalizeShiftRows(res)
  } catch (e) {
    if (!options.silent) {
      showPosError(e, '交班记录加载失败')
    }
  } finally {
    shiftHistoryLoading.value = false
  }
}

async function openCurrentShift() {
  if (shiftLoading.value) return
  shiftLoading.value = true
  try {
    const res = await api.posShiftOpen({
      opening_cash_amount: shiftOpeningCashAmount.value || '0.00',
    })
    currentShift.value = res.shift || null
    syncShiftActualInputs(currentShift.value)
    loadShiftHistory({ force: true, silent: true })
    uni.showToast({ title: '开班成功', icon: 'none' })
  } catch (e) {
    showPosError(e, '开班失败')
  } finally {
    shiftLoading.value = false
  }
}

async function closeCurrentShift() {
  if (!currentShift.value?.id || shiftLoading.value) return
  const confirmed = await confirmDialog({
    title: '确认交班',
    content: `现金应点 ${money(shiftSummary.value.expected_cash_amount)}，确认交班？`,
    confirmText: '交班',
  })
  if (!confirmed) return
  shiftLoading.value = true
  try {
    await api.posShiftClose(currentShift.value.id, {
      actual_cash_amount: shiftActualCashAmount.value || shiftSummary.value.expected_cash_amount || '0.00',
      payments: shiftPaymentRows.value.map((row) => ({
        method: row.method,
        actual_amount: shiftPaymentActuals.value[row.method] || row.expected_amount || '0.00',
      })),
      remark: '',
    })
    currentShift.value = null
    syncShiftActualInputs(currentShift.value)
    uni.showToast({ title: '交班完成', icon: 'none' })
    loadShiftHistory({ force: true, silent: true })
    loadPosStats({ force: true, silent: true })
    loadPosSaleHistory({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '交班失败')
  } finally {
    shiftLoading.value = false
  }
}

async function reopenShift(shift) {
  if (!shift?.id || shiftLoading.value) return
  const confirmed = await confirmDialog({
    title: '确认重开班次',
    content: `重开 ${shift.shift_no} 后可继续结账，交班时会重新汇总该班次销售。`,
    confirmText: '重开',
  })
  if (!confirmed) return

  shiftLoading.value = true
  try {
    const res = await api.posShiftReopen(shift.id, {
      reason: `POS前端重开 ${formatDateTime(new Date())}`,
    })
    currentShift.value = res.shift || null
    syncShiftActualInputs(currentShift.value)
    uni.showToast({ title: '班次已重开', icon: 'none' })
    await loadShiftHistory({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '重开班次失败')
  } finally {
    shiftLoading.value = false
  }
}

async function reprintSale(sale) {
  if (!sale?.id) return
  try {
    const detail = await api.posSaleDetail(sale.id)
    const printData = buildSalePrintData(detail)
    lastSalePrintData.value = printData
    lastReceipt.value = detail.receipt || lastReceipt.value
    await printSaleBySystemSetting(printData, null, '销售历史前端重打')
  } catch (e) {
    showPosError(e, '重打销售单失败')
  }
}

function saleCanReturn(sale = {}) {
  if (!sale?.id || isVoidedSale(sale)) return false
  const lines = Array.isArray(sale.lines) ? sale.lines : []
  return lines.some((line) => numberFromValue(line.returnable_qty ?? line.qty, 0) > 0)
}

async function startReturnSale(sale) {
  if (!saleCanReturn(sale)) return
  try {
    const detail = await api.posSaleDetail(sale.id)
    const lines = Array.isArray(detail.lines) ? detail.lines : []
    const returnableLines = lines
      .filter((line) => numberFromValue(line.returnable_qty ?? line.qty, 0) > 0)
      .map((line) => ({
        ...line,
        return_qty: '',
      }))
    if (!returnableLines.length) {
      uni.showToast({ title: '该销售单已无可退数量', icon: 'none' })
      await loadPosSaleHistory({ force: true, silent: true })
      return
    }
    pendingReturnSale.value = detail
    returnLines.value = returnableLines
    returnReason.value = ''
    returnRefundMethod.value = detail.payment?.method && detail.payment.method !== 'OTHER'
      ? detail.payment.method
      : 'CASH'
    returnRefundReferenceNo.value = ''
    returnRefundAmount.value = '0.00'
  } catch (e) {
    showPosError(e, '加载退货销售单失败')
  }
}

function cancelReturnSale() {
  pendingReturnSale.value = null
  returnLines.value = []
  returnReason.value = ''
  returnRefundMethod.value = 'CASH'
  returnRefundAmount.value = ''
  returnRefundReferenceNo.value = ''
  returnSubmitting.value = false
}

function normalizeReturnLine(index) {
  const line = returnLines.value[index]
  if (!line) return
  const maxQty = numberFromValue(line.returnable_qty ?? line.qty, 0)
  const qty = Math.min(Math.max(numberFromValue(line.return_qty, 0), 0), maxQty)
  line.return_qty = qty > 0 ? qty.toFixed(3) : ''
  returnRefundAmount.value = plainMoney(returnTotalAmount.value)
}

function changeReturnPaymentMethod(event) {
  returnRefundMethod.value = paymentMethods[Number(event?.detail?.value || 0)]?.value || 'CASH'
}

async function confirmReturnSale() {
  if (!pendingReturnSale.value?.id || returnSubmitting.value) return
  const lines = returnLines.value
    .map((line) => ({
      sale_line_id: line.id,
      qty: numberFromValue(line.return_qty, 0),
    }))
    .filter((line) => line.qty > 0)
  if (!lines.length) {
    uni.showToast({ title: '请输入退货数量', icon: 'none' })
    return
  }
  if (!(returnReason.value || '').trim()) {
    uni.showToast({ title: '请输入退货原因', icon: 'none' })
    return
  }
  const refundAmount = numberFromValue(returnRefundAmount.value, 0)
  if (!moneyEqual(refundAmount, returnTotalAmount.value)) {
    uni.showToast({ title: '退款金额必须等于退货金额', icon: 'none' })
    return
  }
  const confirmed = await confirmDialog({
    title: '确认退货',
    content: `退货金额 ${money(returnTotalAmount.value)}，确认退货并记录退款？`,
    confirmText: '退货',
  })
  if (!confirmed) return

  returnSubmitting.value = true
  try {
    await api.posReturnCreate({
      sale_id: pendingReturnSale.value.id,
      reason: returnReason.value,
      idempotency_key: `${pendingReturnSale.value.id}-${Date.now()}`,
      lines: lines.map((line) => ({
        sale_line_id: line.sale_line_id,
        qty: line.qty.toFixed(3),
      })),
      refunds: [
        {
          method: returnRefundMethod.value,
          amount: plainMoney(refundAmount),
          reference_no: returnRefundReferenceNo.value || '',
        },
      ],
    })
    cancelReturnSale()
    uni.showToast({ title: '退货完成', icon: 'none' })
    await loadPosSaleHistory({ force: true, silent: true })
    await loadPosStats({ force: true, silent: true })
    await loadCurrentShift()
    await refreshCartStock({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '退货失败')
  } finally {
    returnSubmitting.value = false
  }
}

function startVoidSale(sale) {
  if (!sale || isVoidedSale(sale)) return
  pendingVoidSale.value = sale
  voidReason.value = ''
}

function cancelVoidSale() {
  pendingVoidSale.value = null
  voidReason.value = ''
}

async function confirmVoidSale() {
  const sale = pendingVoidSale.value
  const reason = (voidReason.value || '').trim()
  if (!sale?.id) return
  if (!reason) {
    uni.showToast({ title: '请输入作废原因', icon: 'none' })
    return
  }
  const confirmed = await confirmDialog({
    title: '确认作废',
    content: `确定作废 ${saleDisplayNo(sale)}？库存将按原扣减明细恢复。`,
    confirmText: '作废',
  })
  if (!confirmed) return

  try {
    const res = await api.posSaleVoid(sale.id, { reason })
    cancelVoidSale()
    uni.showToast({ title: '已作废并恢复库存', icon: 'none' })
    const receipt = res.receipt || null
    if (receipt && lastReceipt.value && saleDisplayNo(lastReceipt.value) === saleDisplayNo(sale)) {
      lastReceipt.value = receipt
    }
    await loadPosSaleHistory({ force: true, silent: true })
    await loadPosStats({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '作废失败')
  }
}

function qtyText(value, context) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return isCountItem(context) ? '0' : '0.000'
  if (isCountItem(context) || Number.isInteger(n)) return String(Math.trunc(n))
  return n.toFixed(3)
}

function toPositiveNumber(value, fallback = 1) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : fallback
}

function explicitIntegerQty(source) {
  if (!source || typeof source !== 'object') return null

  if (source.is_count === true || source.integer_qty === true || source.qty_integer === true) return true
  if (source.allow_decimal === false || source.allow_fraction === false) return true
  if (source.is_decimal === true || source.allow_decimal === true || source.allow_fraction === true) return false

  const decimalPlaces = source.decimal_places ?? source.qty_decimal_places
  if (decimalPlaces !== undefined && decimalPlaces !== null && decimalPlaces !== '') {
    return Number(decimalPlaces) <= 0
  }

  return null
}

function unitNameOf(context) {
  if (!context || typeof context !== 'object') return ''
  const unit = Array.isArray(context.unit_options) ? selectedUnit(context) : null
  return String(
    context.base_unit_name ||
    context.base_unit?.name ||
    context.base_unit?.code ||
    unit?.label ||
    unit?.name ||
    unit?.code ||
    ''
  ).trim().toLowerCase()
}

function isCountUnitName(name) {
  if (!name) return false
  const countUnits = ['个', '件', '只', '支', '瓶', '盒', '箱', '包', '袋', '提', '条', '张', '卷', '罐', '听', '桶', '套', '枚', '把', '双', '台', '部', '本', '片', '颗', '粒', 'pcs', 'pc', 'ea', 'each']
  return countUnits.some((unit) => name === unit || name.endsWith(unit))
}

function isCountItem(context) {
  if (!context || typeof context !== 'object') return false
  const unit = Array.isArray(context.unit_options) ? selectedUnit(context) : null
  const explicit = [context, context.base_unit, unit]
    .map(explicitIntegerQty)
    .find((value) => value !== null)

  if (explicit !== undefined) return explicit
  return isCountUnitName(unitNameOf(context))
}

function formatSaleQty(value, context) {
  const n = toPositiveNumber(value, isCountItem(context) ? 1 : 0.001)
  return isCountItem(context) ? String(Math.max(Math.floor(n), 1)) : String(Math.max(n, 0.001))
}

function setScanFeedback(message, type = 'info') {
  scanFeedbackMessage.value = message || ''
  scanFeedbackType.value = type
}

function collapseProductResults() {
  products.value = []
  productKeyword.value = ''
  productSearched.value = false
}

async function searchCustomers() {
  customerLoading.value = true
  try {
    const res = await api.posCustomers(customerKeyword.value || '', 1)
    customers.value = normalizePage(res)
    if (!customers.value.length) {
      uni.showToast({ title: '未找到客户', icon: 'none' })
    }
  } catch (e) {
    console.error('search customers failed', e)
  } finally {
    customerLoading.value = false
  }
}

function openCustomerCreate() {
  customerCreateOpen.value = true
  const keyword = (customerKeyword.value || '').trim()
  customerForm.value = {
    name: keyword,
    phone: '',
    address: '',
  }
}

function cancelCustomerCreate() {
  customerCreateOpen.value = false
  customerSaving.value = false
  customerForm.value = {
    name: '',
    phone: '',
    address: '',
  }
}

async function submitCustomerCreate() {
  if (customerSaving.value) return
  const payload = {
    name: (customerForm.value.name || '').trim(),
    phone: (customerForm.value.phone || '').trim(),
    address: (customerForm.value.address || '').trim(),
  }
  if (!payload.name) {
    uni.showToast({ title: '请输入客户名称', icon: 'none' })
    return
  }
  customerSaving.value = true
  try {
    const customer = await api.posCustomerCreate(payload)
    customers.value = [customer, ...customers.value.filter((item) => item.id !== customer.id)]
    selectedCustomer.value = customer
    customerKeyword.value = customer.name || customer.code || ''
    customerCreateOpen.value = false
    customerForm.value = { name: '', phone: '', address: '' }
    customerDebtBalance.value = 0
    uni.showToast({ title: '客户已新增', icon: 'none' })
    loadCustomerDebt({ silent: true })
  } catch (e) {
    showPosError(e, '客户新增失败')
  } finally {
    customerSaving.value = false
  }
}

function selectCustomer(customer) {
  selectedCustomer.value = customer
  loadCustomerDebt({ silent: true })
}

async function loadCustomerDebt(options = {}) {
  const customerId = selectedCustomer.value?.id
  if (!customerId || customerDebtLoading.value) return
  customerDebtLoading.value = true
  try {
    const res = await api.posCustomerDebt(customerId)
    customerDebtBalance.value = numberFromValue(res?.debt_balance, 0)
  } catch (e) {
    customerDebtBalance.value = 0
    if (!options.silent) {
      showPosError(e, '客户欠款加载失败')
    }
  } finally {
    customerDebtLoading.value = false
  }
}

function changeRepaymentMethod(event) {
  const index = Number(event?.detail?.value || 0)
  repaymentMethod.value = repaymentMethods.value[index]?.value || 'CASH'
}

function fillRepaymentAmount() {
  repaymentAmount.value = plainMoney(Math.max(customerDebtBalance.value, 0))
}

async function submitCustomerRepayment() {
  if (repaymentSubmitting.value) return
  if (!selectedCustomer.value?.id) {
    uni.showToast({ title: '请先选择客户', icon: 'none' })
    return
  }
  if (!isActiveShift(currentShift.value)) {
    uni.showToast({ title: '请先开班后再收款', icon: 'none' })
    return
  }
  const amount = numberFromValue(repaymentAmount.value, 0)
  if (amount <= 0) {
    uni.showToast({ title: '请输入还款金额', icon: 'none' })
    return
  }
  if (amount > customerDebtBalance.value + 0.005) {
    uni.showToast({ title: '还款金额不能大于累计欠款', icon: 'none' })
    return
  }
  const confirmed = await confirmDialog({
    title: '确认客户还款',
    content: `${selectedCustomerName.value} 还款 ${money(amount)}，确认记录？`,
    confirmText: '收款',
  })
  if (!confirmed) return

  repaymentSubmitting.value = true
  try {
    const res = await api.posRepaymentCreate({
      customer_id: selectedCustomer.value.id,
      method: repaymentMethod.value,
      amount: plainMoney(amount),
      reference_no: repaymentReferenceNo.value || '',
      remark: repaymentRemark.value || '',
    })
    customerDebtBalance.value = numberFromValue(res?.debt_after, 0)
    repaymentAmount.value = ''
    repaymentReferenceNo.value = ''
    repaymentRemark.value = ''
    uni.showToast({ title: '客户还款已记录', icon: 'none' })
    await loadCurrentShift()
    await loadPosStats({ force: true, silent: true })
  } catch (e) {
    showPosError(e, '客户还款失败')
  } finally {
    repaymentSubmitting.value = false
  }
}

function handleScan(code) {
  enqueueProductLookup(code, { exactOnly: true, clearInput: true })
}

async function handleProductConfirm(event) {
  const eventValue = getEventValue(event)
  if (eventValue) {
    productKeyword.value = eventValue
  }

  const keyword = await waitKeywordStable(() => productKeyword.value, {
    interval: 50,
    stableTimes: 2,
    maxWait: 500
  })

  enqueueProductLookup(keyword, { clearInput: true })
}

function triggerQuickScan() {
  quickScan()
  focusProductInput()
}

function enqueueProductLookup(keyword, options = {}) {
  const value = String(keyword || '').trim()
  if (!value) {
    focusProductInput()
    return
  }

  const now = Date.now()
  if (lastProductLookup.keyword === value && now - lastProductLookup.time < 120) {
    if (options.clearInput !== false) {
      productKeyword.value = ''
    }
    focusProductInput()
    return
  }
  lastProductLookup = { keyword: value, time: now }

  productLookupQueue.push({
    keyword: value,
    exactOnly: options.exactOnly === true,
  })

  if (options.clearInput !== false) {
    productKeyword.value = ''
  }

  productSearched.value = true
  setScanFeedback(`正在查询：${value}`, 'info')
  focusProductInput()
  drainProductLookupQueue()
}

async function drainProductLookupQueue() {
  if (productLookupRunning) return
  productLookupRunning = true
  productLoading.value = true
  productSearched.value = true

  try {
    while (productLookupQueue.length) {
      const item = productLookupQueue.shift()
      try {
        if (item.exactOnly) {
          await lookupByBarcode(item.keyword)
        } else {
          await lookupProductKeyword(item.keyword)
        }
      } catch (e) {
        console.error('lookup product failed', e)
      }
    }
  } finally {
    productLookupRunning = false
    productLoading.value = false
    focusProductInput()
  }
}

async function fetchProductsByBarcode(barcode) {
  const res = await api.posProducts({ ...POS_STOCK_QUERY, barcode, page: 1, page_size: 20 })
  return normalizePage(res)
}

async function lookupByBarcode(barcode, options = {}) {
  const showNotFound = options.showNotFound !== false
  const rows = await fetchProductsByBarcode(barcode)
  products.value = rows
  if (rows.length === 1) {
    if (addToCart(rows[0])) {
      products.value = []
      productSearched.value = false
    }
    return rows
  }
  if (rows.length > 1) {
    setScanFeedback(`找到 ${rows.length} 个匹配商品，请选择`, 'info')
  }
  if (showNotFound && !rows.length) {
    setScanFeedback(`未找到商品：${barcode}`, 'error')
    uni.showToast({ title: '未找到商品', icon: 'none' })
  }
  return rows
}

function getEventValue(event) {
  return String(event?.detail?.value ?? event?.target?.value ?? '').trim()
}

async function lookupProductKeyword(keyword) {
  const value = String(keyword || '').trim()
  if (!value) {
    products.value = []
    productSearched.value = false
    return []
  }

  const exactRows = await lookupByBarcode(value, { showNotFound: false })
  if (exactRows.length) return exactRows

  const res = await api.posProducts({
    ...POS_STOCK_QUERY,
    search: value,
    page: 1,
    page_size: 20
  })

  products.value = normalizePage(res)

  if (!products.value.length) {
    setScanFeedback(`未找到商品：${value}`, 'error')
    uni.showToast({ title: '未找到商品', icon: 'none' })
  } else {
    setScanFeedback(`找到 ${products.value.length} 个匹配商品，请选择`, 'info')
  }

  return products.value
}

async function searchProducts(event) {
  const eventValue = getEventValue(event)
  if (eventValue) {
    productKeyword.value = eventValue
  }

  const keyword = await waitKeywordStable(() => productKeyword.value, {
    interval: 50,
    stableTimes: 2,
    maxWait: 500
  })

  enqueueProductLookup(keyword, { clearInput: false })
}

function unitOptions(product) {
  const options = Array.isArray(product.unit_options) ? product.unit_options : []
  if (options.length) return options
  return [
    {
      kind: 'base',
      package_id: null,
      label: product.base_unit?.name || product.base_unit?.code || '基本单位',
      multiplier: '1',
      barcode: product.unit_barcode || product.gtin || '',
    },
  ]
}

function productDisplayName(product) {
  return product?.name || product?.sku || product?.code || String(product?.id || '')
}

function hasAvailableStock(product) {
  const available = stockAvailableQty(product)
  return Number.isFinite(available) && available > 0
}

async function refreshCartStock(options = {}) {
  if (cartStockRefreshing || !cartItems.value.length) return

  const now = Date.now()
  if (!options.force && now - lastCartStockRefreshAt < 3000) return

  cartStockRefreshing = true
  lastCartStockRefreshAt = now

  try {
    await Promise.all(cartItems.value.map(async (item) => {
      const keyword = item.code || item.sku || item.name
      if (!keyword) return

      try {
        const rows = normalizePage(await api.posProducts({
          ...POS_STOCK_QUERY,
          search: keyword,
          page: 1,
          page_size: 20,
        }))
        const product = rows.find((row) =>
          row.id === item.product_id ||
          row.code === item.code ||
          (item.sku && row.sku === item.sku)
        )

        if (product) {
          applyProductSnapshotToCartItem(item, product)
          normalizeCartLine(item, { silent: options.silent === true })
        }
      } catch (e) {
        console.warn('refresh POS cart stock failed', item.code || item.product_id, e)
      }
    }))
  } finally {
    cartStockRefreshing = false
  }
}

function applyProductSnapshotToCartItem(item, product) {
  const currentUnitLabel = item.unit_labels?.[item.unit_index]
  const options = unitOptions(product)
  const unitLabels = options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  const matchedUnitIndex = currentUnitLabel ? unitLabels.findIndex((label) => label === currentUnitLabel) : -1

  item.code = product.code || item.code || ''
  item.owner_id = product.owner_id ?? item.owner_id
  item.sku = product.sku || item.sku || ''
  item.name = productDisplayName(product) || item.name
  item.spec = product.spec || product.specification || product.product_spec || product.package_spec || item.spec || ''
  item.location_code = product.location_code || product.location || product.bin_code || item.location_code || ''
  item.base_unit_name = product.base_unit?.name || product.base_unit?.code || item.base_unit_name || ''
  item.available_qty = stockAvailableQty(product)
  item.min_price = product.min_price
  item.max_discount = product.max_discount
  item.unit_options = options
  item.unit_labels = unitLabels
  item.unit_index = matchedUnitIndex >= 0
    ? matchedUnitIndex
    : Math.min(Math.max(Number(item.unit_index || 0), 0), Math.max(unitLabels.length - 1, 0))
}

function addToCart(product) {
  const available = stockAvailableQty(product)
  const productName = productDisplayName(product)
  const options = unitOptions(product)
  const firstUnit = options[0] || { multiplier: 1 }
  const addBaseQty = toPositiveNumber(firstUnit.multiplier)

  if (!hasAvailableStock(product)) {
    setScanFeedback(`库存不足：${productName}`, 'error')
    uni.showToast({ title: '库存不足，不能加入购物车', icon: 'none' })
    focusProductInput()
    return false
  }

  const exists = cartItems.value.find((item) => item.product_id === product.id)
  if (exists) {
    applyProductSnapshotToCartItem(exists, product)
    const existsAddBaseQty = toPositiveNumber(selectedUnit(exists).multiplier)

    if (Number(lineBaseQty(exists) || 0) + Number(existsAddBaseQty || 0) > Number(exists.available_qty || 0)) {
      setScanFeedback(`库存不足：${exists.name}`, 'error')
      uni.showToast({ title: '数量超过可售库存，不能继续加入', icon: 'none' })
      focusProductInput()
      return false
    }

    exists.qty = String(toPositiveNumber(exists.qty) + 1)
    normalizeCartLine(exists)
    setScanFeedback(`已加入：${exists.name}`, 'success')
    collapseProductResults()
    focusProductInput()
    return true
  }

  if (addBaseQty > available) {
    setScanFeedback(`库存不足：${productName}`, 'error')
    uni.showToast({ title: '库存不足，不能加入购物车', icon: 'none' })
    focusProductInput()
    return false
  }

  const unitLabels = options.map((option) => option.label || (option.kind === 'base' ? '基本单位' : '包装'))
  cartItems.value.push({
    product_id: product.id,
    owner_id: product.owner_id,
    code: product.code || '',
    sku: product.sku || '',
    name: productName,
    spec: product.spec || product.specification || product.product_spec || product.package_spec || '',
    location_code: product.location_code || product.location || product.bin_code || '',
    base_unit_name: product.base_unit?.name || product.base_unit?.code || '',
    available_qty: stockAvailableQty(product),
    min_price: product.min_price,
    max_discount: product.max_discount,
    qty: '1',
    price: priceInputText(product.price),
    unit_options: options,
    unit_labels: unitLabels,
    unit_index: 0,
  })
  setScanFeedback(`已加入：${productName}`, 'success')
  uni.showToast({ title: '已加入购物车', icon: 'none' })
  collapseProductResults()
  focusProductInput()
  return true
}

function selectedUnit(item) {
  return item.unit_options[item.unit_index] || item.unit_options[0] || { multiplier: 1 }
}

function lineBaseQty(item) {
  const unit = selectedUnit(item)
  const qty = isCountItem(item)
    ? Math.max(Math.floor(toPositiveNumber(item.qty)), 1)
    : toPositiveNumber(item.qty)
  return qty * toPositiveNumber(unit.multiplier)
}

function payloadQty(item) {
  const qty = Number(lineBaseQty(item) || 0)
  return isCountItem(item) ? String(Math.trunc(qty)) : qty.toFixed(3)
}

function lineAmount(item) {
  const price = Number(item.price || 0)
  return Number(lineBaseQty(item) || 0) * (Number.isFinite(price) ? price : 0)
}

function normalizeCartLine(item, options = {}) {
  const available = Number(item.available_qty || 0)
  const unit = selectedUnit(item)
  const multiplier = toPositiveNumber(unit.multiplier)
  let saleQty = toPositiveNumber(item.qty)
  const baseQty = saleQty * multiplier

  if (available > 0 && baseQty > available) {
    saleQty = Math.floor((available / multiplier) * 1000) / 1000
    if (!options.silent) {
      uni.showToast({ title: '数量超过可售库存，已调整', icon: 'none' })
    }
  }

  item.qty = formatSaleQty(saleQty, item)
  item.price = priceInputText(item.price)
}

function normalizeLine(index) {
  const item = cartItems.value[index]
  if (item) normalizeCartLine(item)
}

function changeUnit(index, event) {
  const item = cartItems.value[index]
  if (!item) return
  item.unit_index = Number(event.detail.value || 0)
  normalizeCartLine(item)
}

function removeLine(index) {
  cartItems.value.splice(index, 1)
}

function changePaymentMethod(event) {
  const index = Number(event.detail.value || 0)
  paymentMethod.value = paymentMethods[index]?.value || 'CASH'
  if (paymentMethod.value === 'CASH') {
    amountReceived.value = ''
  } else {
    syncNonCashAmount()
  }
}

function goBack() {
  let pageCount = 0
  try {
    pageCount = typeof getCurrentPages === 'function' ? getCurrentPages().length : 0
  } catch (e) {
    pageCount = 0
  }

  if (pageCount > 1) {
    uni.navigateBack()
    return
  }

  uni.switchTab({ url: '/pages/index/index' })
}

function confirmResetSale() {
  uni.showModal({
    title: '清空清单',
    content: '确定清空当前购物车和单据信息？',
    confirmText: '清空',
    cancelText: '取消',
    success: (res) => {
      if (res.confirm) {
        resetSale()
      }
    },
  })
}

function resetSale(options = {}) {
  removeSaleDraft()
  selectedCustomer.value = null
  customerDebtBalance.value = 0
  repaymentAmount.value = ''
  repaymentReferenceNo.value = ''
  repaymentRemark.value = ''
  customers.value = []
  customerKeyword.value = ''
  customerCreateOpen.value = false
  customerSaving.value = false
  customerForm.value = { name: '', phone: '', address: '' }
  products.value = []
  productKeyword.value = ''
  productSearched.value = false
  setScanFeedback('', 'info')
  productLookupQueue.length = 0
  lastProductLookup = { keyword: '', time: 0 }
  cartItems.value = []
  saleDate.value = new Date()
  srcBillNo.value = makeReceiptNo()
  idempotencyKey.value = makeIdempotencyKey()
  remark.value = ''
  paymentMethod.value = 'WECHAT'
  amountReceived.value = ''
  paymentReferenceNo.value = ''
  splitPaymentEnabled.value = false
  paymentLines.value = []
  if (!options.keepReceipt) {
    lastReceipt.value = null
    lastSalePrintData.value = null
  }
  focusProductInput()
}

async function validateBeforeCheckout() {
  if (!isActiveShift(currentShift.value)) {
    uni.showToast({ title: '请先开班后再结账', icon: 'none' })
    return false
  }
  if (!cartItems.value.length) {
    uni.showToast({ title: '购物车不能为空', icon: 'none' })
    return false
  }

  await refreshCartStock({ force: true, silent: true })

  const overStock = cartItems.value.find((item) => Number(lineBaseQty(item)) > Number(item.available_qty || 0))
  if (overStock) {
    uni.showToast({ title: `${overStock.code} 可售库存不足`, icon: 'none' })
    return false
  }
  if (!paymentReady.value) {
    const message = splitPaymentEnabled.value
      ? '拆分支付合计必须等于应收，赊账必须选择客户'
      : paymentMethod.value === 'CASH'
        ? '实收金额不足'
        : paymentMethod.value === 'CREDIT'
          ? '赊账必须先选择客户'
          : '非现金支付实收必须等于应收'
    uni.showToast({ title: message, icon: 'none' })
    return false
  }
  if (selectedCustomer.value && ownerCount.value > 1) {
    const confirmed = await confirmDialog({
      title: '多货主拆单',
      content: '已选客户仅用于同货主订单，其他货主将自动使用散客客户。是否继续结账？',
      confirmText: '继续',
    })
    if (!confirmed) return false
  }
  return true
}

async function checkout() {
  if (!(await validateBeforeCheckout())) return
  submitting.value = true
  const preparedPrintWindow = autoPrintSale.value && !useBackendSalePrint.value ? openSalePrintWindow() : null
  try {
    syncNonCashAmount()
    const payload = {
      src_bill_no: srcBillNo.value || '',
      idempotency_key: idempotencyKey.value || '',
      stock_zone_type: POS_STOCK_QUERY.zone_type,
      remark: remark.value || '',
      items: cartItems.value.map((item) => ({
        product_id: item.product_id,
        qty: payloadQty(item),
        price: Number(item.price || 0).toFixed(2),
      })),
    }
    if (splitPaymentEnabled.value) {
      payload.payments = paymentLines.value.map((line) => ({
        method: line.method,
        amount: plainMoney(line.amount),
        amount_received: plainMoney(line.amount_received || line.amount),
        reference_no: line.reference_no || '',
      }))
    } else {
      payload.payment = {
        method: paymentMethod.value,
        amount_received: Number(receivedAmount.value || 0).toFixed(2),
        reference_no: paymentReferenceNo.value || '',
      }
    }
    if (selectedCustomer.value) {
      payload.customer_id = selectedCustomer.value.id
    }
    const res = await api.posCheckout(payload)
    const orders = Array.isArray(res.orders) ? res.orders : []
    const printData = buildSalePrintData(res)
    lastSalePrintData.value = printData
    lastReceipt.value = res.receipt || {
      sale_no: printData.billNo,
      src_bill_no: printData.billNo,
      total_amount: printData.totalAmount,
      payment: {
        method: paymentMethod.value,
        amount_received: printData.amountReceived,
        change_amount: printData.changeAmount,
      },
      orders,
    }
    const msg =
      orders.length > 1
        ? `结账成功：已生成${orders.length}张销售出库单`
        : `结账成功：${res.sale?.sale_no || orders[0]?.order_no || orders[0]?.id || ''}`
    uni.showToast({ title: msg, icon: 'none' })
    if (autoPrintSale.value) {
      await printSaleBySystemSetting(printData, preparedPrintWindow, '结账后前端自动打印')
    }
    resetSale({ keepReceipt: true })
    loadPosSaleHistory({ force: true, silent: true })
    loadPosStats({ force: true, silent: true })
    loadCurrentShift()
    loadShiftHistory({ force: true, silent: true })
  } catch (e) {
    if (preparedPrintWindow && !preparedPrintWindow.closed) {
      preparedPrintWindow.close()
    }
    showPosError(e, '结账失败')
    console.error('pos checkout failed', e)
  } finally {
    submitting.value = false
  }
}


function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function waitKeywordStable(getValue, options = {}) {
  const interval = options.interval || 50       // 每 50ms 检查一次
  const stableTimes = options.stableTimes || 2  // 连续 2 次不变，认为稳定
  const maxWait = options.maxWait || 500        // 最多等 500ms

  let lastValue = String(getValue() || '').trim()
  let sameCount = 0
  let waited = 0

  while (waited < maxWait) {
    await sleep(interval)
    waited += interval

    const currentValue = String(getValue() || '').trim()

    if (currentValue === lastValue) {
      sameCount++
      if (sameCount >= stableTimes) {
        return currentValue
      }
    } else {
      lastValue = currentValue
      sameCount = 0
    }
  }

  return String(getValue() || '').trim()
}

function onProductInput(event) {
  productKeyword.value = String(event.detail.value || '').trim()
}

function focusProductInput(delay = 80) {
  productInputFocus.value = false
  nextTick(() => {
    setTimeout(() => {
      productInputFocus.value = true
    }, delay)
  })
}

</script>

<style scoped>
.pos-page {
  min-height: 100vh;
  height: 100vh;
  background: #f4f6f8;
  padding: 6rpx;
  box-sizing: border-box;
  overflow: hidden;
  font-size: 28rpx;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 42rpx;
  margin-bottom: 6rpx;
  box-sizing: border-box;
}

.nav-back,
.nav-spacer {
  flex: 0 0 46rpx;
  width: 46rpx;
  height: 42rpx;
  box-sizing: border-box;
}

.nav-back {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #172033;
  background: transparent;
  border: 0;
  font-size: 38rpx;
  line-height: 42rpx;
  padding: 0;
}

.title {
  flex: 1;
  color: #172033;
  font-size: 32rpx;
  font-weight: 700;
  line-height: 42rpx;
  text-align: center;
}

.subtitle {
  display: none;
  color: #667085;
  font-size: 21rpx;
  margin-top: 0;
  margin-left: 16rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section {
  background: #fff;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 10rpx;
  margin-bottom: 8rpx;
}

.pos-toolbar {
  padding: 8rpx 10rpx;
  margin-bottom: 0;
}

.shift-section {
  margin: 8rpx 10rpx;
}

.shift-refresh-btn {
  width: 96rpx;
  height: 46rpx;
  font-size: 25rpx;
}

.shift-card,
.shift-open-row {
  display: flex;
  align-items: flex-start;
  gap: 12rpx;
  min-width: 0;
}

.shift-main {
  display: flex;
  flex-direction: column;
  width: 360rpx;
  min-width: 0;
}

.shift-no {
  color: #172033;
  font-size: 30rpx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shift-meta,
.shift-hint {
  color: #667085;
  font-size: 25rpx;
  line-height: 1.35;
}

.shift-numbers {
  display: flex;
  flex: 1;
  gap: 18rpx;
  min-width: 0;
  color: #344054;
  font-size: 26rpx;
}

.shift-close-row {
  display: flex;
  align-items: center;
  gap: 8rpx;
}

.shift-close-panel {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  min-width: 360rpx;
}

.shift-cash-input {
  width: 180rpx;
  height: 52rpx;
}

.shift-action-btn {
  width: 112rpx;
  height: 52rpx;
  font-size: 25rpx;
}

.shift-payment-actuals {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.shift-payment-row {
  display: flex;
  align-items: center;
  gap: 8rpx;
  min-width: 0;
  color: #475467;
  font-size: 23rpx;
}

.shift-payment-name {
  width: 72rpx;
  flex: 0 0 72rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shift-payment-expected {
  width: 170rpx;
  flex: 0 0 170rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shift-payment-input {
  width: 132rpx;
  height: 44rpx;
  font-size: 20rpx;
}

.shift-payment-diff {
  flex: 0 0 96rpx;
  color: #027a48;
  text-align: right;
}

.shift-payment-diff.danger {
  color: #b42318;
}

.scan-line,
.meta-field {
  display: flex;
  align-items: center;
  min-width: 0;
}

.scan-line {
  gap: 6rpx;
}

.toolbar-label {
  flex: 0 0 auto;
  color: #475467;
  font-size: 25rpx;
  font-weight: 600;
  white-space: nowrap;
}

.primary-label {
  color: #172033;
  font-size: 28rpx;
}

.pos-toolbar .scan-input {
  flex: 1 1 520rpx;
  min-width: 0;
  height: 58rpx;
  font-size: 29rpx;
}

.pos-toolbar .scan-btn,
.pos-toolbar .clear-btn {
  flex: 0 0 112rpx;
  width: 112rpx;
  height: 58rpx;
  font-size: 25rpx;
  padding: 0 12rpx;
  white-space: nowrap;
  line-height: 58rpx;
}

.customer-field {
  flex: 0 1 370rpx;
}

.pos-toolbar .meta-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 58rpx;
  font-size: 22rpx;
  padding: 0 10rpx;
  margin-left: 6rpx;
}

.pos-toolbar .meta-btn {
  flex: 0 0 68rpx;
  width: 68rpx;
  height: 58rpx;
  margin-left: 6rpx;
  font-size: 21rpx;
}

.meta-value,
.meta-scan {
  flex: 0 1 auto;
  max-width: 360rpx;
  color: #667085;
  font-size: 21rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pos-toolbar .customer-list,
.pos-toolbar .product-list {
  margin-top: 6rpx;
}

.pos-toolbar .empty-tip {
  padding: 6rpx 0 0;
}

.scan-feedback {
  display: flex;
  align-items: center;
  gap: 12rpx;
  min-height: 36rpx;
  margin-top: 6rpx;
  padding: 0 8rpx;
  border-radius: 6rpx;
  font-size: 25rpx;
  box-sizing: border-box;
}

.scan-feedback.info {
  color: #475467;
  background: #f8fafc;
}

.scan-feedback.success {
  color: #027a48;
  background: #ecfdf3;
}

.scan-feedback.error {
  color: #b42318;
  background: #fff5f5;
}

.scan-code,
.scan-message {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scan-code {
  flex: 0 1 auto;
  max-width: 360rpx;
}

.scan-message {
  flex: 1;
  min-width: 0;
}

.doc-info-row {
  display: flex;
  align-items: center;
  gap: 18rpx;
  min-height: 46rpx;
  padding: 6rpx 10rpx;
}

.doc-info-item {
  display: flex;
  align-items: center;
  min-width: 0;
}

.customer-doc {
  flex: 1 1 auto;
}

.date-doc {
  flex: 0 0 240rpx;
}

.receipt-doc {
  flex: 0 0 420rpx;
}

.doc-label {
  flex: 0 0 auto;
  color: #475467;
  font-size: 25rpx;
  font-weight: 600;
  margin-right: 8rpx;
  white-space: nowrap;
}

.doc-value {
  color: #172033;
  font-size: 26rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-input {
  flex: 1;
  min-width: 0;
  height: 42rpx;
  font-size: 25rpx;
  padding: 0 10rpx;
}

.pos-main {
  display: flex;
  align-items: stretch;
  gap: 10rpx;
  height: calc(100vh - 54rpx);
  min-height: 0;
}

.pos-left {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.pos-right {
  flex: 0 0 900rpx;
  width: 900rpx;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10rpx;
  overflow-y: auto;
  padding-right: 2rpx;
  box-sizing: border-box;
}

.pos-right .section-title {
  font-size: 32rpx;
}

.pos-right .selected-text,
.pos-right .doc-label,
.pos-right .doc-value,
.pos-right .bill-label,
.pos-right .print-option,
.pos-right .print-mode-label,
.pos-right .stats-label,
.pos-right .history-status,
.pos-right .history-meta,
.pos-right .receipt-row,
.pos-right .amount-label,
.pos-right .summary-sub,
.pos-right .owner-warning,
.pos-right .choice-code {
  font-size: 26rpx;
}

.pos-right .input,
.pos-right .bill-input,
.pos-right .payment-input,
.pos-right .payment-value,
.pos-right .print-mode-value,
.pos-right .primary-btn,
.pos-right .ghost-btn,
.pos-right .danger-btn {
  font-size: 28rpx;
}

.pos-right .choice-name,
.pos-right .history-no,
.pos-right .stats-value,
.pos-right .amount-value,
.pos-right .void-title,
.pos-right .return-name {
  font-size: 32rpx;
}

.pos-right .summary-title {
  font-size: 64rpx;
}

.customer-panel {
  order: 1;
  margin-bottom: 0;
}

.customer-search-row {
  display: flex;
  align-items: center;
  margin-top: 10rpx;
}

.customer-input {
  flex: 1;
  min-width: 0;
  height: 52rpx;
  font-size: 23rpx;
}

.customer-search-btn {
  flex: 0 0 92rpx;
  width: 92rpx;
  height: 52rpx;
  margin-left: 8rpx;
  font-size: 22rpx;
  white-space: nowrap;
  line-height: 52rpx;
}

.customer-add-btn {
  flex: 0 0 82rpx;
  width: 82rpx;
  height: 52rpx;
  margin-left: 8rpx;
  font-size: 22rpx;
  line-height: 52rpx;
}

.customer-create-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8rpx;
  margin-top: 8rpx;
  padding: 8rpx;
  border: 1rpx solid #e4e7ec;
  border-radius: 8rpx;
  background: #f8fafc;
}

.customer-create-input {
  height: 52rpx;
  min-width: 0;
  font-size: 24rpx;
}

.customer-create-input.wide {
  grid-column: 1 / -1;
}

.customer-create-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: 8rpx;
}

.customer-create-btn {
  width: 96rpx;
  height: 50rpx;
  margin: 0;
  font-size: 22rpx;
  line-height: 50rpx;
}

.customer-panel .customer-list {
  margin-top: 8rpx;
  max-height: 156rpx;
  overflow-y: auto;
}

.customer-debt-panel {
  margin-top: 10rpx;
  border-top: 1rpx solid #edf0f4;
  padding-top: 10rpx;
}

.customer-debt-head,
.repayment-row {
  display: flex;
  align-items: center;
  gap: 8rpx;
  min-width: 0;
}

.customer-debt-head {
  color: #475467;
  font-size: 25rpx;
  margin-bottom: 8rpx;
}

.customer-debt-amount {
  color: #9a3412;
  font-size: 31rpx;
  font-weight: 700;
}

.debt-refresh-btn,
.repayment-fill-btn,
.repayment-submit-btn {
  flex: 0 0 82rpx;
  width: 82rpx;
  height: 50rpx;
  font-size: 22rpx;
  line-height: 50rpx;
}

.repayment-row + .repayment-row {
  margin-top: 8rpx;
}

.repayment-picker {
  flex: 0 0 120rpx;
  width: 120rpx;
}

.repayment-input {
  flex: 1 1 auto;
  min-width: 0;
}

.repayment-ref-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 52rpx;
  font-size: 24rpx;
}

.doc-info-stack {
  margin-top: 10rpx;
  border-top: 1rpx solid #edf0f4;
  padding-top: 10rpx;
}

.doc-info-stack .doc-label {
  flex: 0 0 96rpx;
  width: 96rpx;
  margin-right: 8rpx;
  text-align: left;
}

.doc-info-stack .doc-value,
.doc-info-stack .doc-input {
  flex: 1;
  min-width: 0;
}

.doc-info-stack .doc-info-item + .doc-info-item {
  margin-top: 8rpx;
}

.section-head,
.search-row,
.summary-row,
.bill-row,
.cart-controls {
  display: flex;
  align-items: center;
}

.section-head,
.summary-row {
  justify-content: space-between;
}

.section-title {
  color: #172033;
  font-size: 30rpx;
  font-weight: 700;
}

.section-actions {
  display: flex;
  align-items: center;
  gap: 8rpx;
}

.selected-text,
.scan-text {
  color: #667085;
  font-size: 24rpx;
}

.search-row {
  margin-top: 8rpx;
}

.input,
.bill-input,
.payment-input,
.small-input,
.price-input {
  height: 60rpx;
  background: #f8fafc;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  padding: 0 14rpx;
  box-sizing: border-box;
  font-size: 28rpx;
}

.search-row .input {
  height: 56rpx;
  font-size: 27rpx;
}

.flex-input,
.bill-input {
  flex: 1;
}

button {
  margin: 0;
  line-height: 1;
  white-space: nowrap;
  writing-mode: horizontal-tb;
  text-orientation: mixed;
  word-break: keep-all;
}

.primary-btn,
.ghost-btn,
.danger-btn,
.submit-btn {
  height: 60rpx;
  border-radius: 8rpx;
  font-size: 27rpx;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  padding: 0 12rpx;
  box-sizing: border-box;
  white-space: nowrap;
  writing-mode: horizontal-tb;
  text-orientation: mixed;
  word-break: keep-all;
}

.search-row .primary-btn,
.search-row .ghost-btn {
  height: 56rpx;
  font-size: 27rpx;
}

.primary-btn {
  color: #fff;
  background: #1677ff;
}

.ghost-btn {
  color: #1f2937;
  background: #fff;
  border: 1rpx solid #cfd6e0;
}

.danger-btn {
  color: #b42318;
  background: #fff5f5;
  border: 1rpx solid #ffd0d0;
}

.side-btn {
  width: 96rpx;
  margin-left: 8rpx;
}

.compact-btn {
  width: 78rpx;
  height: 40rpx;
  font-size: 22rpx;
}

.choice-row,
.product-row,
.cart-row {
  display: flex;
  border-top: 1rpx solid #edf0f4;
  padding: 10rpx 0;
}

.choice-row {
  justify-content: space-between;
}

.choice-row.active {
  background: #f0f7ff;
}

.choice-name,
.product-name {
  display: block;
  color: #172033;
  font-size: 30rpx;
  font-weight: 600;
}

.choice-code,
.product-meta {
  display: block;
  color: #667085;
  font-size: 24rpx;
  margin-top: 3rpx;
}

.choice-check {
  color: #1677ff;
  font-size: 27rpx;
}

.product-row {
  align-items: center;
  justify-content: flex-end;
  min-height: 58rpx;
  padding: 6rpx 0;
}

.product-main {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex: 0 1 auto;
  gap: 10rpx;
  margin-left: auto;
  overflow: hidden;
  text-align: right;
}

.product-main .product-name {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 520rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-main .product-meta {
  flex: 0 0 auto;
  margin-top: 0;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.add-btn {
  width: 100rpx;
  margin-left: 12rpx;
}

.empty-tip,
.empty-cart {
  color: #98a2b3;
  font-size: 26rpx;
  padding: 12rpx 0 4rpx;
  text-align: center;
}

.cart-section {
  flex: 1;
  min-width: 0;
  margin-bottom: 0;
  min-height: 0;
}

 .cart-table {
  width: 100%;
  margin-top: 8rpx;
  overflow-x: auto;
}

.cart-line-row,
.cart-table-head,
.cart-row {
  display: flex !important;
  flex-direction: row !important;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: nowrap !important;
  width: 100%;
  box-sizing: border-box;
}

.cart-table-head {
  height: 38rpx;
  color: #667085;
  font-size: 25rpx;
  border-top: 1rpx solid #edf0f4;
  border-bottom: 1rpx solid #edf0f4;
}

.cart-row {
  min-height: 58rpx;
  padding: 4rpx 0;
  border-top: 1rpx solid #edf0f4;
}

.cart-goods-col {
  flex: 1 1 auto;
  min-width: 0;
  padding-right: 12rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-unit-col {
  width: 96rpx;
  flex: 0 0 96rpx;
  margin-left: 8rpx;
  box-sizing: border-box;
}

.cart-qty-col {
  width: 130rpx;
  flex: 0 0 130rpx;
  margin-left: 8rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-price-col {
  width: 150rpx;
  flex: 0 0 150rpx;
  margin-left: 8rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-amount-col {
  width: 160rpx;
  flex: 0 0 160rpx;
  margin-left: 8rpx;
  text-align: right;
  box-sizing: border-box;
}

.cart-action-col {
  width: 70rpx;
  flex: 0 0 70rpx;
  margin-left: 8rpx;
  text-align: center;
  box-sizing: border-box;
}

.cart-info {
  display: block;
  overflow: visible;
  text-align: right;
}

.cart-goods-name,
.cart-goods-line {
  display: block;
  max-width: 100%;
  color: #172033;
  font-size: 30rpx;
  font-weight: 600;
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
  word-break: break-all;
}

.cart-goods-line {
  line-height: 40rpx;
  text-align: right;
}

.cart-goods-meta {
  display: block;
  max-width: 100%;
  color: #667085;
  font-size: 25rpx;
  margin-left: 0;
  margin-top: 4rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.unit-picker {
  width: 100%;
}

.picker-value {
  width: 100%;
  height: 56rpx;
  line-height: 56rpx;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  background: #fff;
  color: #172033;
  font-size: 27rpx;
  text-align: center;
  box-sizing: border-box;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.small-input {
  width: 100%;
  height: 56rpx;
  min-height: 56rpx;
  text-align: right;
  box-sizing: border-box;
}

.price-wrap {
  width: 100%;
  height: 56rpx;
  background: #f8fafc;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  display: flex;
  align-items: center;
  box-sizing: border-box;
}

.yuan {
  color: #667085;
  font-size: 27rpx;
  padding-left: 10rpx;
}

.price-input {
  flex: 1;
  min-width: 0;
  width: 100%;
  height: 52rpx;
  min-height: 52rpx;
  border: 0;
  background: transparent;
  padding: 0 10rpx 0 6rpx;
  text-align: right;
  box-sizing: border-box;
}

.line-amount {
  display: block;
  color: #172033;
  font-size: 28rpx;
  font-weight: 600;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  width: 70rpx;
  height: 56rpx;
}

.submit-panel {
  order: 2;
  position: sticky;
  top: 0;
  z-index: 5;
  background: #fff;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 14rpx;
  box-shadow: 0 4rpx 14rpx rgba(15, 23, 42, 0.08);
  box-sizing: border-box;
}

.checkout-row {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10rpx;
  min-width: 0;
}

.bill-row {
  display: flex;
  align-items: center;
  min-width: 0;
  margin-bottom: 0;
}

.remark-row {
  flex: 0 0 auto;
  order: 5;
}

.payment-row {
  flex: 0 0 auto;
  order: 2;
}

.split-payment-check {
  order: 2;
}

.split-payment-panel {
  order: 3;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.split-payment-row {
  display: flex;
  align-items: center;
  gap: 6rpx;
  min-width: 0;
}

.split-picker {
  flex: 0 0 112rpx;
  width: 112rpx;
  margin-right: 0;
}

.split-input {
  flex: 0 1 92rpx;
  min-width: 0;
  height: 52rpx;
  font-size: 25rpx;
  padding: 0 8rpx;
}

.split-ref-input {
  flex: 1;
  min-width: 0;
  height: 52rpx;
  font-size: 25rpx;
}

.split-remove-btn {
  flex: 0 0 54rpx;
  width: 54rpx;
  height: 52rpx;
  font-size: 23rpx;
}

.split-payment-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8rpx;
  color: #667085;
  font-size: 23rpx;
}

.split-add-btn {
  flex: 0 0 92rpx;
  width: 92rpx;
  height: 44rpx;
  font-size: 23rpx;
}

.reference-row {
  flex: 0 0 auto;
  order: 4;
}

.bill-label {
  flex: 0 0 auto;
  width: auto;
  color: #475467;
  font-size: 25rpx;
  margin-right: 6rpx;
  white-space: nowrap;
}

.payment-picker {
  flex: 0 0 136rpx;
  width: 136rpx;
  margin-right: 8rpx;
}

.payment-value {
  height: 56rpx;
  line-height: 56rpx;
  border: 1rpx solid #d7dde6;
  border-radius: 8rpx;
  background: #fff;
  color: #172033;
  font-size: 25rpx;
  text-align: center;
}

.payment-input {
  flex: 1;
  min-width: 0;
}

.submit-panel .bill-input,
.submit-panel .payment-input {
  height: 56rpx;
  font-size: 27rpx;
  padding: 0 10rpx;
}

.submit-panel .payment-input {
  height: 70rpx;
  color: #172033;
  background: #fffdf7;
  border-color: #f59e0b;
  font-size: 38rpx;
  font-weight: 700;
  text-align: right;
  padding: 0 16rpx;
}

.submit-panel .payment-input[disabled] {
  background: #f8fafc;
  border-color: #d7dde6;
  color: #475467;
}

.receipt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #475467;
  font-size: 27rpx;
  border-top: 1rpx solid #edf0f4;
  padding-top: 14rpx;
  margin-top: 14rpx;
}

.receipt-section {
  order: 4;
  margin-bottom: 0;
}

.stats-section {
  order: 3;
  margin-bottom: 0;
}

.history-section {
  order: 5;
  margin-bottom: 0;
}

.shift-history-section {
  order: 6;
  margin-bottom: 0;
}

.stats-refresh-btn {
  width: 96rpx;
  height: 46rpx;
  font-size: 25rpx;
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8rpx;
  margin-top: 10rpx;
}

.stats-card {
  min-width: 0;
  background: #f8fafc;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 10rpx;
}

.stats-card.primary {
  background: #ecfdf3;
  border-color: #bbf7d0;
}

.stats-label {
  display: block;
  color: #667085;
  font-size: 23rpx;
  line-height: 1.2;
  margin-bottom: 4rpx;
}

.stats-value {
  display: block;
  color: #172033;
  font-size: 30rpx;
  font-weight: 700;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats-card.primary .stats-value {
  color: #027a48;
}

.stats-value.danger {
  color: #b42318;
}

.stats-payments {
  margin-top: 10rpx;
  border-top: 1rpx solid #edf0f4;
}

.stats-payment-row {
  display: flex;
  justify-content: space-between;
  gap: 8rpx;
  color: #475467;
  font-size: 24rpx;
  line-height: 1.35;
  padding-top: 8rpx;
}

.stats-payment-row text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats-payment-row text:last-child {
  flex: 0 0 auto;
  text-align: right;
}

.receipt-print-btn {
  width: 100%;
  height: 50rpx;
  margin-top: 8rpx;
  font-size: 25rpx;
}

.history-refresh-btn {
  width: 96rpx;
  height: 46rpx;
  font-size: 25rpx;
}

.history-search-row {
  display: flex;
  gap: 8rpx;
  margin-top: 10rpx;
}

.history-input {
  flex: 1;
  min-width: 0;
}

.history-search-btn {
  width: 104rpx;
  height: 56rpx;
  font-size: 25rpx;
}

.history-list {
  margin-top: 10rpx;
}

.history-more-btn {
  width: 100%;
  height: 48rpx;
  margin-top: 8rpx;
  font-size: 25rpx;
}

.history-more-panel {
  margin-top: 8rpx;
  max-height: 360rpx;
  overflow-y: auto;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  background: #fff;
  padding: 0 8rpx;
  box-sizing: border-box;
}

.history-more-panel .history-row:first-child {
  border-top: 0;
}

.history-row,
.shift-history-row {
  display: flex;
  align-items: center;
  gap: 10rpx;
  padding: 12rpx 0;
  border-top: 1rpx solid #edf0f4;
}

.history-main,
.shift-history-main {
  flex: 1;
  min-width: 0;
}

.history-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8rpx;
}

.sale-history-line {
  justify-content: flex-start;
  gap: 14rpx;
  min-width: 0;
  white-space: nowrap;
}

.history-no {
  color: #172033;
  font-size: 28rpx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sale-history-line .history-no {
  flex: 0 1 auto;
  max-width: 250rpx;
}

.history-money,
.history-order-count {
  flex: 0 0 auto;
  color: #667085;
  font-size: 24rpx;
  line-height: 1.35;
  white-space: nowrap;
}

.history-money {
  color: #172033;
  font-weight: 600;
}

.sale-history-line .history-status {
  margin-left: auto;
}

.history-status {
  flex: 0 0 auto;
  font-size: 23rpx;
  font-weight: 700;
}

.history-status.completed {
  color: #0f766e;
}

.history-status.active {
  color: #175cd3;
}

.history-status.voided {
  color: #b42318;
}

.history-meta {
  display: block;
  color: #667085;
  font-size: 24rpx;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-actions {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  flex: 0 0 104rpx;
}

.sale-history-actions {
  flex: 0 0 auto;
  flex-direction: row;
  align-items: center;
  gap: 6rpx;
}

.history-action-btn {
  width: 104rpx;
  height: 44rpx;
  font-size: 23rpx;
}

.sale-history-actions .history-action-btn {
  width: 88rpx;
}

.void-panel {
  margin-top: 10rpx;
  padding: 10rpx;
  background: #fff7ed;
  border: 1rpx solid #fed7aa;
  border-radius: 8rpx;
}

.return-panel {
  background: #fff5f5;
  border-color: #ffd0d0;
}

.return-line {
  display: flex;
  align-items: center;
  gap: 8rpx;
  padding: 8rpx 0;
  border-top: 1rpx solid #ffe4e4;
}

.return-line-main {
  flex: 1;
  min-width: 0;
}

.return-name {
  display: block;
  color: #172033;
  font-size: 25rpx;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.return-qty-input {
  flex: 0 0 132rpx;
  width: 132rpx;
  height: 52rpx;
  background: #fff;
}

.return-refund-row {
  margin-top: 8rpx;
}

.void-title {
  display: block;
  color: #9a3412;
  font-size: 25rpx;
  font-weight: 700;
  margin-bottom: 8rpx;
}

.void-input {
  width: 100%;
  height: 56rpx;
  background: #fff;
}

.void-actions {
  display: flex;
  gap: 8rpx;
  margin-top: 8rpx;
}

.void-action-btn {
  flex: 1;
  height: 48rpx;
  font-size: 25rpx;
}

.receipt-nos {
  flex: 1;
  min-width: 0;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-title {
  display: block;
  color: #b42318;
  font-size: 62rpx;
  font-weight: 700;
  line-height: 1.05;
  text-align: right;
}

.amount-label {
  display: block;
  color: #667085;
  font-size: 25rpx;
  font-weight: 600;
  margin-bottom: 4rpx;
}

.amount-due .amount-label,
.amount-due .summary-title {
  text-align: right;
}

.amount-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8rpx;
}

.amount-box {
  background: #f8fafc;
  border: 1rpx solid #e6eaf0;
  border-radius: 8rpx;
  padding: 10rpx;
}

.amount-value {
  display: block;
  color: #172033;
  font-size: 34rpx;
  font-weight: 700;
}

.change-value {
  color: #b42318;
}

.debt-value,
.history-debt {
  color: #9a3412;
}

.summary-sub {
  display: flex;
  justify-content: space-between;
  gap: 8rpx;
  color: #667085;
  font-size: 25rpx;
  white-space: nowrap;
}

.owner-warning {
  color: #9a3412;
  background: #fff7ed;
  border: 1rpx solid #fed7aa;
  border-radius: 8rpx;
  padding: 8rpx 10rpx;
  font-size: 25rpx;
  line-height: 1.35;
}

.print-check-group {
  display: block;
}

.print-settings {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10rpx;
  flex-wrap: wrap;
}

.print-option {
  display: flex;
  align-items: center;
  color: #475467;
  font-size: 25rpx;
  line-height: 1;
}

.print-option checkbox {
  transform: scale(0.78);
  transform-origin: left center;
  margin-right: 2rpx;
}

.print-mode-control {
  display: flex;
  align-items: center;
  gap: 6rpx;
  min-width: 0;
}

.print-mode-label {
  color: #667085;
  font-size: 25rpx;
  white-space: nowrap;
}

.print-mode-picker {
  flex: 0 0 auto;
}

.receipt-info-control {
  flex: 1 1 100%;
}

.receipt-info-control .print-mode-picker {
  flex: 1;
  min-width: 0;
}

.print-mode-value {
  min-width: 150rpx;
  max-width: 220rpx;
  height: 44rpx;
  line-height: 44rpx;
  padding: 0 14rpx;
  border: 1rpx solid #d0d5dd;
  border-radius: 8rpx;
  color: #344054;
  background: #fff;
  font-size: 25rpx;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  box-sizing: border-box;
}

.receipt-info-control .print-mode-value {
  max-width: none;
  text-align: left;
}

.submit-btn {
  width: 100%;
  height: 78rpx;
  color: #fff;
  background: #0f766e;
  font-weight: 700;
  font-size: 36rpx;
}

.summary-row {
  order: 1;
  flex: 0 0 auto;
  min-width: 0;
  margin-left: 0;
  padding-bottom: 8rpx;
  border-bottom: 1rpx solid #edf0f4;
  flex-direction: column;
  align-items: stretch;
  gap: 12rpx;
}

.submit-btn[disabled],
.primary-btn[disabled] {
  opacity: 0.45;
}
</style>
