<script setup lang="ts">
/**
 * Models - 模型版本管理 (v3.0.0 Phase I 重构)
 *
 * 本页面已拆分为以下子组件/composable:
 * - composables/useModelList.ts     (列表加载 / 激活 / 批量操作)
 * - components/ModelStatsRow.vue    (顶部 4 张统计卡)
 * - components/ModelFilterBar.vue   (筛选 + 批量操作按钮组)
 * - components/ModelTable.vue       (表格 + 行内操作)
 * - components/ModelDetailDialog.vue (详情弹窗)
 * - components/ModelCompareDialog.vue (对比弹窗)
 *
 * Page 仅保留: 状态层 (筛选/分页/弹窗) + view 编排 + 触发 handlers
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Grid } from '@element-plus/icons-vue'
import { modelApi } from '@/api'

// v3.0.0 Phase I: 列表/操作逻辑全部抽离为 composable
import { useModelList } from '@/composables/useModelList'

// v3.0.0 Phase I: 视图层拆分为子组件
import ModelStatsRow from './components/ModelStatsRow.vue'
import ModelFilterBar from './components/ModelFilterBar.vue'
import ModelTable from './components/ModelTable.vue'
import ModelDetailDialog from './components/ModelDetailDialog.vue'
import ModelCompareDialog from './components/ModelCompareDialog.vue'

// ============== 列表数据 + 操作 (委托 useModelList) ==============
const data = ref<any[]>([])
const loading = ref(false)
const {
  selectedRows,
  selectedIds,
  batchActivating,
  batchDeleting,
  load,
  onActivate,
  onDeactivate,
  onBatchSetActive,
  onDelete,
  onBatchDelete,
  onSelectionChange,
} = useModelList({ data, loading })

// ============== 详情/对比 弹窗 state ==============
const detail = ref<any>(null)
const compare = ref<any>(null)
const detailOpen = ref(false)
const compareOpen = ref(false)

// ============== 筛选条件 (即时生效) ==============
const filterKeyword = ref('')
const filterDatasetId = ref<number | ''>('')
const filterTaskType = ref<string>('')

// ============== 分页 (client-side) ==============
const page = ref(1)
const pageSize = ref(10)
const total = computed(() => filteredData.value.length)
const pagedData = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredData.value.slice(start, start + pageSize.value)
})

// 筛选条件变更时自动回到第 1 页
const onFilterChange = () => { page.value = 1 }
const onPageChange = (p: number) => { page.value = p }
const onSizeChange = (s: number) => { pageSize.value = s; page.value = 1 }
const indexMethod = (idx: number) => (page.value - 1) * pageSize.value + idx + 1

// 任务类型变更: 联动重置数据集下拉
const onTaskTypeChange = () => {
  if (filterDatasetId.value !== '' && filterTaskType.value) {
    const stillValid = filterableDatasetsForFilter.value.some(
      (d) => d.id === filterDatasetId.value
    )
    if (!stillValid) filterDatasetId.value = ''
  }
  page.value = 1
}

// ============== 数据集下拉选项 (从 data 推导) ==============
const datasetsWithTaskType = computed(() => {
  const seen = new Map<number, { id: number; name: string; task_type: string }>()
  for (const m of data.value) {
    if (m.dataset_id != null && !seen.has(m.dataset_id)) {
      seen.set(m.dataset_id, {
        id: m.dataset_id,
        name: m.dataset_name || '未命名数据集',
        task_type: m.task_type || 'classification',
      })
    }
  }
  return Array.from(seen.values()).sort((a, b) => a.id - b.id)
})
const filterableDatasetsForFilter = computed(() => {
  if (!filterTaskType.value) return datasetsWithTaskType.value
  return datasetsWithTaskType.value.filter(
    (d) => d.task_type === filterTaskType.value
  )
})

// ============== 筛选后数据 ==============
const filteredData = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  return data.value.filter((m: any) => {
    if (kw) {
      const hay = `${m.name || ''} ${m.base_model || ''}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    if (filterDatasetId.value !== '' && m.dataset_id !== filterDatasetId.value) {
      return false
    }
    if (filterTaskType.value) {
      const t = m.task_type || 'classification'
      if (t !== filterTaskType.value) return false
    }
    return true
  })
})

// ============== 顶部统计 ==============
const stats = computed(() => {
  const total = data.value.length
  const active = data.value.filter((m: any) => m.is_active).length
  let bestAcc = 0
  let bestName = '-'
  for (const m of data.value) {
    const a = Number(m.accuracy || 0)
    if (a > bestAcc) {
      bestAcc = a
      bestName = m.name
    }
  }
  const bases = new Set(data.value.map((m: any) => m.base_model).filter(Boolean))
  return { total, active, bestAcc, bestName, baseCount: bases.size }
})

// ============== 详情/对比 触发 ==============
const onDetail = async (id: number) => {
  try {
    const d: any = await modelApi.detail(id)
    detail.value = d
    detailOpen.value = true
  } catch {
    ElMessage.error('详情加载失败')
  }
}

const onCompare = async () => {
  if (selectedIds.value.length !== 2) {
    ElMessage.warning('请选择恰好 2 个版本进行对比')
    return
  }
  try {
    const c: any = await modelApi.compare(selectedIds.value[0], selectedIds.value[1])
    compare.value = c
    compareOpen.value = true
  } catch (e: any) {
    ElMessage.error('对比失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// ============== 批量激活/取消激活/对比 (page 编排) ==============
const onBatchActivate = () => onBatchSetActive(true)
const onBatchDeactivate = () => onBatchSetActive(false)

// ============== 筛选重置 ==============
const resetFilters = () => {
  filterKeyword.value = ''
  filterDatasetId.value = ''
  filterTaskType.value = ''
  page.value = 1
}

onMounted(load)
</script>

<template>
  <div class="page-flex">
    <!-- 顶部统计 (拆分) -->
    <ModelStatsRow
      :total="stats.total"
      :active="stats.active"
      :best-acc="stats.bestAcc"
      :best-name="stats.bestName"
      :base-count="stats.baseCount"
    />

    <!-- 顶部标题 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">
          <el-icon class="page-title__icon"><Grid /></el-icon>
          <span>模型版本管理</span>
          <span class="subtitle">Models</span>
        </h2>
        <p class="page-desc text-soft">
          浏览、激活、对比各次训练产出的模型版本; 激活后即可用于 AI 预标注
        </p>
      </div>
    </div>

    <!-- 筛选 + 批量操作 (拆分) -->
    <ModelFilterBar
      v-model:task-type="filterTaskType"
      v-model:dataset-id="filterDatasetId"
      v-model:keyword="filterKeyword"
      :datasets="datasetsWithTaskType"
      :selected-count="selectedIds.length"
      :batch-activating="batchActivating"
      :batch-deleting="batchDeleting"
      :compare-disabled="selectedIds.length !== 2"
      @change:task-type="onTaskTypeChange"
      @change:filter="onFilterChange"
      @batch-activate="onBatchActivate"
      @batch-deactivate="onBatchDeactivate"
      @batch-delete="onBatchDelete"
      @compare="onCompare"
    />

    <!-- 表格 (拆分) -->
    <ModelTable
      :data="pagedData"
      :loading="loading"
      :filter-keyword="filterKeyword"
      :filter-task-type="filterTaskType"
      :filter-dataset-id="filterDatasetId"
      :index-method="indexMethod"
      @selection-change="onSelectionChange"
      @activate="onActivate"
      @deactivate="onDeactivate"
      @detail="onDetail"
      @delete="onDelete"
    />

    <!-- 分页栏 -->
    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>

    <!-- 详情弹窗 (拆分) -->
    <ModelDetailDialog v-model="detailOpen" :model="detail" />

    <!-- 对比弹窗 (拆分) -->
    <ModelCompareDialog v-model="compareOpen" :compare="compare" />
  </div>
</template>

<style scoped>
/* 页面: 撑满 el-main, flex column 布局, 表格 flex:1, 分页栏钉在底部 */
.page-flex {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px;
}

/* ============== 顶部标题 ============== */
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-shrink: 0;
}
.page-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
}
.page-title__icon {
  font-size: 22px;
  color: var(--brand-primary);
}
.page-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}
.page-title .subtitle {
  color: var(--text-placeholder);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

/* ============== 分页栏 ============== */
.pager {
  flex-shrink: 0;
  margin-top: 12px;
  padding: 8px 0;
  display: flex;
  justify-content: flex-end;
  background: #fff;
  border-top: 1px solid var(--border-soft);
  position: sticky;
  bottom: 0;
  z-index: 5;
}
</style>
