<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

// Mirrors server lead times (days) per warehouse
const LEAD_TIMES = {
  'San Francisco': 5,
  London: 10,
  Tokyo: 14
}
const WAREHOUSES = Object.keys(LEAD_TIMES)
const BUDGET_STEP = 1000

const { t, currentLocale, currentCurrency, translateWarehouse } = useI18n()
const { selectedLocation } = useFilters()

const loading = ref(true)
const error = ref(null)
const forecasts = ref([])

const warehouse = ref(WAREHOUSES.includes(selectedLocation.value) ? selectedLocation.value : 'San Francisco')
const budget = ref(0)
const quantities = reactive({})
const selected = reactive({})

const submitting = ref(false)
const submitError = ref(null)
const placedOrderId = ref(null)

const suggestedQty = (item) => Math.max(item.forecasted_demand - item.current_demand, 0)
const gapOf = (item) => item.forecasted_demand - item.current_demand
const qtyOf = (item) => quantities[item.item_sku] || 0
const lineTotal = (item) => qtyOf(item) * item.unit_cost

const maxBudget = computed(() => {
  const sum = forecasts.value.reduce((acc, item) => acc + suggestedQty(item) * item.unit_cost, 0)
  return Math.ceil(sum / BUDGET_STEP) * BUDGET_STEP
})

const selectedItems = computed(() =>
  forecasts.value.filter(item => selected[item.item_sku] && qtyOf(item) > 0)
)
const selectedTotal = computed(() =>
  selectedItems.value.reduce((acc, item) => acc + lineTotal(item), 0)
)
const remaining = computed(() => budget.value - selectedTotal.value)
const overBudget = computed(() => selectedTotal.value > budget.value)

const leadTimeDays = computed(() => LEAD_TIMES[warehouse.value])

const formatDate = (date) => {
  if (!(date instanceof Date) || isNaN(date.getTime())) return '-'
  const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
  return date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })
}

const expectedDelivery = computed(() => {
  const days = leadTimeDays.value
  if (typeof days !== 'number') return '-'
  const d = new Date()
  d.setDate(d.getDate() + days)
  return formatDate(d)
})

const canPlaceOrder = computed(() =>
  selectedItems.value.length > 0 &&
  selectedTotal.value > 0 &&
  !overBudget.value &&
  !submitting.value
)

const money = (amount) => (amount < 0 ? '-' : '') + formatCurrency(Math.abs(amount), currentCurrency.value)
const unitPrice = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)

// Clear ticks, then greedily tick ranked items that fit the remaining budget
const preselect = () => {
  forecasts.value.forEach(item => { selected[item.item_sku] = false })
  const ranked = [...forecasts.value].sort((a, b) => {
    const aInc = a.trend === 'increasing' ? 0 : 1
    const bInc = b.trend === 'increasing' ? 0 : 1
    if (aInc !== bInc) return aInc - bInc
    return gapOf(b) - gapOf(a)
  })
  let left = budget.value
  for (const item of ranked) {
    const cost = lineTotal(item)
    if (qtyOf(item) > 0 && cost <= left) {
      selected[item.item_sku] = true
      left -= cost
    }
  }
}

watch(budget, preselect)

const onQtyInput = (item, event) => {
  const n = Math.floor(Number(event.target.value))
  const qty = Number.isFinite(n) && n > 0 ? n : 0
  quantities[item.item_sku] = qty
  if (qty === 0) selected[item.item_sku] = false
  event.target.value = qty
}

const loadData = async () => {
  loading.value = true
  error.value = null
  try {
    const data = await api.getDemandForecasts()
    forecasts.value = data
    data.forEach(item => {
      quantities[item.item_sku] = suggestedQty(item)
      selected[item.item_sku] = false
    })
    const max = maxBudget.value
    if (budget.value === max) {
      preselect()
    } else {
      budget.value = max // watcher runs preselect
    }
  } catch (err) {
    error.value = t('restocking.loadFailed') + ': ' + err.message
    console.error(err)
  } finally {
    loading.value = false
  }
}

const placeOrder = async () => {
  if (!canPlaceOrder.value) return
  submitting.value = true
  submitError.value = null
  placedOrderId.value = null
  try {
    const order = await api.createRestockOrder({
      warehouse: warehouse.value,
      budget: budget.value,
      items: selectedItems.value.map(item => ({ item_sku: item.item_sku, quantity: qtyOf(item) }))
    })
    placedOrderId.value = order.id
    preselect()
  } catch (err) {
    const detail = err.response?.data?.detail
    submitError.value = typeof detail === 'string' ? detail : t('restocking.orderFailed')
    console.error(err)
  } finally {
    submitting.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card controls">
        <div class="control">
          <label class="control-label" for="restock-warehouse">{{ t('restocking.warehouse') }}</label>
          <select id="restock-warehouse" v-model="warehouse" class="select">
            <option v-for="w in WAREHOUSES" :key="w" :value="w">{{ translateWarehouse(w) }}</option>
          </select>
        </div>
        <div class="control control-budget">
          <label class="control-label" for="restock-budget">
            {{ t('restocking.budget') }}: <strong>{{ money(budget) }}</strong>
          </label>
          <input
            id="restock-budget"
            v-model.number="budget"
            type="range"
            class="slider"
            min="0"
            :max="maxBudget"
            :step="BUDGET_STEP"
            :disabled="maxBudget === 0"
          />
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ money(budget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.selectedTotal') }}</div>
          <div class="stat-value">{{ money(selectedTotal) }}</div>
        </div>
        <div :class="['stat-card', remaining < 0 ? 'danger' : 'success']">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ money(remaining) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.leadTime') }}</div>
          <div class="stat-value">{{ t('restocking.leadTimeDays', { n: leadTimeDays }) }}</div>
          <div class="stat-note">{{ t('restocking.expectedDelivery', { date: expectedDelivery }) }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">
            {{ t('restocking.itemsToRestock') }}
            ({{ t('restocking.selectedCount', { count: selectedItems.length }) }})
          </h3>
        </div>

        <div v-if="forecasts.length === 0" class="empty">{{ t('restocking.noItems') }}</div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.select') }}</th>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.current') }}</th>
                <th>{{ t('restocking.table.forecast') }}</th>
                <th>{{ t('restocking.table.gap') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in forecasts" :key="item.item_sku">
                <td>
                  <input
                    v-model="selected[item.item_sku]"
                    type="checkbox"
                    :disabled="qtyOf(item) === 0"
                    :aria-label="t('restocking.table.select') + ' ' + item.item_sku"
                  />
                </td>
                <td><strong>{{ item.item_sku }}</strong></td>
                <td>{{ item.item_name }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ t('trends.' + item.trend) }}</span>
                </td>
                <td>{{ item.current_demand }}</td>
                <td>{{ item.forecasted_demand }}</td>
                <td>{{ gapOf(item) }}</td>
                <td>{{ unitPrice(item.unit_cost) }}</td>
                <td>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    class="qty-input"
                    :value="qtyOf(item)"
                    :aria-label="t('restocking.table.quantity') + ' ' + item.item_sku"
                    @input="onQtyInput(item, $event)"
                  />
                </td>
                <td><strong>{{ money(lineTotal(item)) }}</strong></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="actions">
          <button type="button" class="btn-primary" :disabled="!canPlaceOrder" @click="placeOrder">
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
          <span v-if="overBudget" class="hint hint-danger">{{ t('restocking.overBudget') }}</span>
        </div>

        <div v-if="placedOrderId" class="message message-success" role="status">
          {{ t('restocking.orderPlaced', { id: placedOrderId }) }}
          <router-link to="/orders">{{ t('restocking.viewOrders') }}</router-link>
        </div>
        <div v-if="submitError" class="message message-error" role="alert">{{ submitError }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 2rem;
  align-items: flex-end;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}

.control {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.control-budget {
  flex: 1;
  min-width: 280px;
}

.control-label {
  color: #64748b;
  font-size: 0.875rem;
  font-weight: 600;
}

.control-label strong {
  color: #0f172a;
}

.select {
  padding: 0.5rem 0.75rem;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: white;
  color: #0f172a;
  font-size: 0.938rem;
}

.slider {
  width: 100%;
  height: 6px;
  accent-color: #2563eb;
  cursor: pointer;
}

.slider:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.stat-note {
  margin-top: 0.375rem;
  color: #64748b;
  font-size: 0.875rem;
}

.qty-input {
  width: 90px;
  padding: 0.375rem 0.5rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  color: #0f172a;
  font-size: 0.875rem;
}

.qty-input:focus,
.select:focus {
  outline: 2px solid #bfdbfe;
  border-color: #2563eb;
}

.empty {
  padding: 1.5rem;
  color: #64748b;
}

.actions {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1.25rem;
}

.btn-primary {
  padding: 0.625rem 1.25rem;
  border: none;
  border-radius: 8px;
  background: #2563eb;
  color: white;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-primary:disabled {
  background: #cbd5e1;
  color: #64748b;
  cursor: not-allowed;
}

.hint-danger {
  color: #dc2626;
  font-size: 0.875rem;
}

.message {
  margin: 0 1.25rem 1.25rem;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.938rem;
}

.message-success {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
}

.message-success a {
  margin-left: 0.5rem;
  color: #2563eb;
  font-weight: 600;
}

.message-error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
}
</style>
