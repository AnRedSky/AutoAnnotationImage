<script setup lang="ts">
/**
 * Training - 训练任务页面 (v3.0.0 Phase J 重构为编排层)
 *
 * 拆分架构 (子组件 / composables):
 * - composables/useTrainingBaseModels  (基础模型动态加载)
 * - composables/useTrainingJobs        (任务列表/筛选/分页/批量操作)
 * - composables/useTrainingActions     (行操作: 启动/暂停/取消/删除)
 * - composables/useTrainingDetailStream (详情 SSE + 图表, 通过 TrainingDetailDialog 调用)
 * - composables/useTrainingFormatters  (格式化函数)
 * - composables/useTrainingListSSE     (列表级 SSE)
 * - composables/useSilentRefresh       (静默兜底刷新)
 *
 * 业务组件 (本页面私有):
 * - components/TrainingStatsRow       顶部 4 张统计卡
 * - components/TrainingFilterBar      筛选 + 操作 (新建/刷新)
 * - components/TrainingFilterChips    筛选状态条 + 批量操作工具条
 * - components/TrainingJobsTable      任务表格
 * - components/TrainingCreateDialog   新建任务弹窗
 * - components/TrainingRetrainDialog  再训练弹窗
 * - components/TrainingDetailDialog   详情弹窗 (SSE + 图表)
 *
 * Page 仅保留:
 * - 状态层 (弹窗可见性 / 详情行)
 * - view 编排 (模板 + 事件转发)
 * - 业务编排: 把 composables 串起来
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion } from '@element-plus/icons-vue'
import { datasetApi } from '@/api'
import { useTrainingBaseModels } from '@/composables/useTrainingBaseModels'
import { useTrainingJobs } from '@/composables/useTrainingJobs'
import { useTrainingActions } from '@/composables/useTrainingActions'
import { useTrainingFormatters } from '@/composables/useTrainingFormatters'
import { useTrainingListSSE } from '@/composables/useTrainingListSSE'
import { useSilentRefresh } from '@/composables/useSilentRefresh'

import TrainingStatsRow from './components/TrainingStatsRow.vue'
import TrainingFilterBar from './components/TrainingFilterBar.vue'
import TrainingFilterChips from './components/TrainingFilterChips.vue'
import TrainingJobsTable from './components/TrainingJobsTable.vue'
import TrainingCreateDialog from './components/TrainingCreateDialog.vue'
import TrainingRetrainDialog from './components/TrainingRetrainDialog.vue'
import TrainingDetailDialog from './components/TrainingDetailDialog.vue'

// ============== 基础模型加载 (从 /api/auto-annotate/models) ==============
const {
  byTask: baseModelsByTask,
  load: loadBaseModels,
} = useTrainingBaseModels()

// ============== 训练任务列表 / 筛选 / 分页 / 批量操作 ==============
const {
  jobs, total, page, pageSize, loading,
  stateFilter, datasetIdFilter, modelKeywordFilter, taskTypeFilter,
  selectedJobIds, actionPending,
  allOnPageSelected,
  canStart, canPause, canCancel, canDelete,
  loadJobs, onPageChange, onSizeChange,
  onStateFilterChange, onDatasetFilterChange, onTaskTypeFilterChange, onModelKeywordChange,
  resetFilters,
  onStateFilterClear, onDatasetFilterClear, onTaskTypeFilterClear, onModelKeywordClear,
  onSelectionChange, toggleSelectAllJobs, batchDeleteJobs,
  insertPlaceholderJob, updateJobProgressInPlace,
} = useTrainingJobs()

// ============== 行操作 (启动/暂停/取消/删除) ==============
const {
  runBtnLabel, runBtnType, runBtnAction, runBtnDisabled,
  onRunBtnClick, onRowStart, onRowPause, onRowCancel, onRowDelete,
} = useTrainingActions({
  insertPlaceholder: insertPlaceholderJob,
  reload: loadJobs,
  actionPending,
})

// ============== 格式化函数 (时间/时长/设备/状态/进度) ==============
const {
  formatTime, formatDurationSmart,
  deviceTagType, deviceShortLabel, formatDeviceTooltip,
  stateLabel, progressStatus, genDefaultModelName,
} = useTrainingFormatters()

// ============== 数据集列表 (供筛选/新建/再训练使用) ==============
const DATASET_OPTIONS = ref<any[]>([])
const loadDatasets = async () => {
  try {
    const r: any = await datasetApi.list()
    DATASET_OPTIONS.value = r?.items || r || []
  } catch (e: any) {
    ElMessage.warning('加载数据集失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

// 列表筛选行的"数据集"下拉: 按 taskTypeFilter 联动
const filterableDatasetsForFilter = computed(() => {
  if (!taskTypeFilter.value) return DATASET_OPTIONS.value
  return DATASET_OPTIONS.value.filter(
    (d: any) => (d.task_type || 'classification') === taskTypeFilter.value
  )
})

// 顶部统计 (基于 jobs 聚合)
const stats = computed(() => {
  const list = jobs.value
  const totalCount = total.value || list.length
  const cnt: Record<string, number> = { PENDING: 0, PROGRESS: 0, SUCCESS: 0, FAILURE: 0, PAUSED: 0, REVOKED: 0 }
  for (const j of list) cnt[j.state] = (cnt[j.state] || 0) + 1
  return {
    total: totalCount,
    running: cnt.PROGRESS,
    success: cnt.SUCCESS,
    failed: cnt.FAILURE + cnt.REVOKED,
  }
})

// 表格序号: (page - 1) * pageSize + idx + 1
const indexMethod = (idx: number) => (page.value - 1) * pageSize.value + idx + 1

// 数据集名查找 (行内展示用)
const datasetNameOf = (id: number) => {
  const ds = DATASET_OPTIONS.value.find((x: any) => x.id === id)
  return ds ? ds.name : `#${id}`
}

// ============== 弹窗状态 ==============
const createDialogVisible = ref(false)
const retrainDialogVisible = ref(false)
const retrainRow = ref<any>(null)
const detailVisible = ref(false)
const detailRow = ref<any>(null)

const openCreateDialog = () => { createDialogVisible.value = true }
const onCreateSubmit = async (payload: { params: any; newTaskId: string; newJobId?: number }) => {
  // 立即插入占位行 + 刷新列表
  if (payload.newTaskId && payload.params.dataset_id) {
    insertPlaceholderJob(payload.newTaskId, {
      dataset_id: payload.params.dataset_id,
      base_model: payload.params.base_model,
      model_name: payload.params.model_name,
      epochs: payload.params.epochs,
      batch_size: payload.params.batch_size,
      learning_rate: payload.params.learning_rate,
    })
  }
  await loadJobs()
  // v3.0.0: 如果详情弹窗还开着, 关闭它以清理旧任务的 SSE + 5s historyTimer
  // 避免旧 PENDING/PROGRESS 任务的定时请求继续跑 (5s historyTimer + SSE 连接)
  if (detailVisible.value) {
    detailVisible.value = false
  }
}

const openRetrainDialog = (row: any) => {
  retrainRow.value = row
  retrainDialogVisible.value = true
}
const onRetrainSubmit = async (payload: { params: any; newTaskId: string; newJobId?: number }) => {
  if (payload.newTaskId && payload.params.dataset_id) {
    insertPlaceholderJob(payload.newTaskId, {
      dataset_id: payload.params.dataset_id,
      base_model: payload.params.base_model,
      model_name: payload.params.model_name,
      epochs: payload.params.epochs,
      batch_size: payload.params.batch_size,
      learning_rate: payload.params.learning_rate,
    })
  }
  await loadJobs()
  // v3.0.0: 再训练提交后关闭详情弹窗, 触发 cleanup() 清理旧任务的 SSE + 5s historyTimer
  // 旧任务如果是 PENDING/PROGRESS, 其 SSE 连接和 5s 轮询会继续跑直到弹窗关闭
  // 关闭后 useSilentRefresh 恢复 (30s 兜底), useTrainingListSSE 跟踪新任务进度
  if (detailVisible.value) {
    detailVisible.value = false
  }
}

// 详情弹窗: 行点击 → 设置 row, 打开 dialog
const onRowDetail = (row: any) => {
  detailRow.value = row
  detailVisible.value = true
}

// 详情弹窗内按钮 (runBtnClick 在 detail 时也要走列表 onRunBtnClick, 同样逻辑)
const onDetailRun = async (row: any) => {
  await onRunBtnClick(row, openRetrainDialog)
}

// 详情弹窗的"删除"处理: 关闭弹窗 + reload (弹窗内部已 close)
const onDetailDelete = async (row: any) => {
  await onRowDelete(row, (deletedRow) => {
    if (detailRow.value?.id === deletedRow.id) {
      detailRow.value = null
      detailVisible.value = false
    }
  })
  await loadJobs()
}

// ============== 行操作按钮的 click 入口 (表格行) ==============
// - PROGRESS → 暂停
// - PAUSED → 继续 (复用原 job)
// - PENDING/终态 → 弹窗修改参数后启动
const onTableRowRun = (row: any) => onRunBtnClick(row, openRetrainDialog)

// ============== 列表级 SSE: 跟踪活跃任务的进度 ==============
useTrainingListSSE({
  jobs,
  onProgressUpdate: updateJobProgressInPlace,
  onTerminalState: () => loadJobs(),
  onError: () => loadJobs(),
  onComplete: () => loadJobs(),
})

// ============== 静默兜底刷新 (v3.5.0 Phase T6 优化) ==============
// 1) shouldRun: 仅当列表中存在 PENDING/PROGRESS 任务时才启动定时器
//    - 空闲时 (全为终态) 后端不可能有状态变化, 彻底停掉避免空转
// 2) enabledByVisibility: 默认 true, 标签页不可见时自动停, 切回时 evaluate 恢复
// 3) 监听 jobs 变化 → evaluate, 新建任务 / 任务终态变化时立即启停
// 4) 详情打开时走原有 pause/resume (跳过本次 tick, 定时器保留以减少抖动)
const hasActiveJob = computed(() =>
  jobs.value.some((j: any) => j.state === 'PENDING' || j.state === 'PROGRESS'),
)

const {
  start: startSilentRefresh,
  stop: stopSilentRefresh,
  pause: pauseSilentRefresh,
  resume: resumeSilentRefresh,
  evaluate: evaluateSilentRefresh,
} = useSilentRefresh({
  intervalMs: 60000,
  shouldRun: () => hasActiveJob.value,
  skipWhen: () => detailVisible.value,
  onTick: () => loadJobs(),
})

// v3.1.0 Phase T4: 详情打开时主动暂停兜底刷新 (SSE 接管)
watch(detailVisible, (v) => {
  if (v) pauseSilentRefresh()
  else resumeSilentRefresh()
})

// v3.5.0 Phase T6: 列表中"活跃任务存在性"变化时, 重新评估兜底启停
// - 新建任务 / 任务进入 PROGRESS → 立即启动兜底 (双保险)
// - 全部任务进入终态 → 立即停止兜底
// - SSE 接管时 evaluate 也会调, 但 useSilentRefresh 内部已处理 SSE skip, 不冲突
watch(hasActiveJob, () => {
  evaluateSilentRefresh()
})

// ============== 生命周期 ==============
onMounted(async () => {
  await Promise.all([loadDatasets(), loadJobs(), loadBaseModels()])
  // 启动后再 evaluate 一次, 处理"加载完成时 jobs 已有活跃任务"的初始判定
  startSilentRefresh()
  evaluateSilentRefresh()
})

onBeforeUnmount(() => {
  stopSilentRefresh()
})
</script>

<template>
  <div class="training-page">
    <!-- 顶部统计条 -->
    <TrainingStatsRow
      :total="stats.total"
      :running="stats.running"
      :success="stats.success"
      :failed="stats.failed"
    />

    <!-- 顶部标题 -->
    <div class="page-header">
      <div>
        <h2 class="page-title">
          <el-icon class="page-title__icon"><Promotion /></el-icon>
          <span>训练任务</span>
          <span class="subtitle">Training</span>
        </h2>
        <p class="page-desc text-soft">
          选择数据集和基础模型, 启动微调训练; 训练完成后激活即可用于 AI 预标注
        </p>
      </div>
    </div>

    <!-- 筛选 + 操作 -->
    <TrainingFilterBar
      v-model:task-type="taskTypeFilter"
      v-model:state="stateFilter"
      v-model:dataset-id="datasetIdFilter"
      v-model:keyword="modelKeywordFilter"
      :datasets="filterableDatasetsForFilter"
      :state-options="[
        { value: 'PENDING', label: '等待中' },
        { value: 'PROGRESS', label: '训练中' },
        { value: 'SUCCESS', label: '已完成' },
        { value: 'PAUSED', label: '已暂停' },
        { value: 'REVOKED', label: '已取消' },
        { value: 'FAILURE', label: '失败' },
      ]"
      @change:filter="onTaskTypeFilterChange"
      @change:keyword="(v: string) => onModelKeywordChange(v)"
      @reset="resetFilters"
      @create="openCreateDialog"
      @refresh="loadJobs"
    />

    <!-- 筛选状态条 + 批量操作工具条 -->
    <TrainingFilterChips
      :task-type="taskTypeFilter"
      :state="stateFilter"
      :dataset-id="datasetIdFilter"
      :keyword="modelKeywordFilter"
      :total="total"
      :task-type-options="[
        { value: 'classification', label: '图片分类' },
        { value: 'detection',      label: '目标检测' },
        { value: 'segmentation',   label: '图片分割' },
      ]"
      :state-options="[
        { value: 'PENDING', label: '等待中' },
        { value: 'PROGRESS', label: '训练中' },
        { value: 'SUCCESS', label: '已完成' },
        { value: 'PAUSED', label: '已暂停' },
        { value: 'REVOKED', label: '已取消' },
        { value: 'FAILURE', label: '失败' },
      ]"
      :selected-count="selectedJobIds.length"
      :all-selected="allOnPageSelected"
      :action-pending-count="Object.keys(actionPending).length"
      :dataset-name="datasetNameOf"
      @clear:taskType="onTaskTypeFilterClear"
      @clear:state="onStateFilterClear"
      @clear:dataset="onDatasetFilterClear"
      @clear:keyword="onModelKeywordClear"
      @batch-delete="batchDeleteJobs"
      @toggle-select-all="toggleSelectAllJobs"
      @clear-selection="selectedJobIds = []"
    />

    <!-- 任务列表 -->
    <TrainingJobsTable
      :data="jobs"
      :loading="loading"
      :action-pending="actionPending"
      :index-method="indexMethod"
      :dataset-name-of="datasetNameOf"
      :device-tag-type="deviceTagType"
      :device-short-label="deviceShortLabel"
      :device-tooltip="formatDeviceTooltip"
      :format-time="formatTime"
      :progress-status="progressStatus"
      :run-btn-label="runBtnLabel"
      :run-btn-type="runBtnType"
      :run-btn-action="runBtnAction"
      :run-btn-disabled="runBtnDisabled"
      :can-cancel="canCancel"
      :can-delete="canDelete"
      :can-start="canStart"
      @selection-change="(rows: any[]) => onSelectionChange(rows)"
      @row-run="onTableRowRun"
      @row-cancel="onRowCancel"
      @row-detail="onRowDetail"
      @row-delete="onRowDelete"
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

    <!-- 新建任务对话框 -->
    <TrainingCreateDialog
      v-model="createDialogVisible"
      :datasets="DATASET_OPTIONS"
      :base-models-by-task="baseModelsByTask"
      :gen-default-model-name="genDefaultModelName"
      @submit="onCreateSubmit"
    />

    <!-- 再训练对话框 -->
    <TrainingRetrainDialog
      v-model="retrainDialogVisible"
      :row="retrainRow"
      :datasets="DATASET_OPTIONS"
      :base-models-by-task="baseModelsByTask"
      @submit="onRetrainSubmit"
    />

    <!-- 详情对话框 (SSE 实时 + 历史曲线) -->
    <TrainingDetailDialog
      v-model:visible="detailVisible"
      :row="detailRow"
      :dataset-name-of="datasetNameOf"
      :action-pending="actionPending"
      :can-start="canStart"
      :can-cancel="canCancel"
      :can-delete="canDelete"
      :run-btn-label="runBtnLabel"
      :run-btn-type="runBtnType"
      :run-btn-action="runBtnAction"
      :format-time="formatTime"
      :format-duration="formatDurationSmart"
      :device-tag-type="deviceTagType"
      :device-short-label="deviceShortLabel"
      :device-tooltip="formatDeviceTooltip"
      :progress-status="progressStatus"
      :state-label="stateLabel"
      @run="onDetailRun"
      @cancel="onRowCancel"
      @delete="onDetailDelete"
      @reload="loadJobs"
    />
  </div>
</template>

<style scoped>
.training-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* 顶部标题 */
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

/* 分页栏: 固定在页面底部, 不会被表格滚动条挡住 */
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

