<script setup lang="ts">
/**
 * DatasetFilterBar - 过滤 + AI 选项 + 操作工具条
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 包含 4 组 (flex + flex-wrap 紧凑布局):
 *   1. 过滤组: 状态 / 搜索
 *   2. AI 组:   模型下拉 / 置信度阈值 / 测评
 *   3. 操作组: 去标注
 *   4. 辅助组: 全选 / 批量清除 / 删除 / 视图切换
 * 下方: AI 配置实时提示 (模型/阈值/待标数量联动)
 */
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Search, Lightning, DataAnalysis, EditPen, RefreshLeft, Delete,
  Check, Minus, Grid, List, InfoFilled, WarningFilled, Promotion,
} from '@element-plus/icons-vue'

const props = defineProps<{
  // 状态
  statusOptions: any[]
  statusFilter: string
  keyword: string
  // AI
  finetuneModels: any[]
  selectedFinetuneId: number | null
  activeModel: any
  threshold: number
  hasFinetuneModel: boolean
  displayModel: string
  pendingCount: number
  previewTooltipText: string
  previewing: boolean
  hasImages: boolean
  // 操作
  selectedCount: number
  allOnPageSelected: boolean
  // 视图
  viewMode: 'grid' | 'list'
  // 任务类型 (用于 AI 配置提示判断无模型引导)
  taskType: 'classification' | 'detection' | 'segmentation'
}>()

const emit = defineEmits<{
  (e: 'update:statusFilter', v: string): void
  (e: 'update:keyword', v: string): void
  (e: 'update:selectedFinetuneId', v: number | null): void
  (e: 'update:threshold', v: number): void
  (e: 'update:viewMode', v: 'grid' | 'list'): void
  (e: 'preview'): void
  (e: 'goAnnotate'): void
  (e: 'batchClear'): void
  (e: 'batchDelete'): void
  (e: 'toggleSelectAll'): void
}>()

const router = useRouter()

// 双向绑定
const localStatusFilter = computed({
  get: () => props.statusFilter,
  set: (v: string) => emit('update:statusFilter', v),
})
const localKeyword = computed({
  get: () => props.keyword,
  set: (v: string) => emit('update:keyword', v),
})
const localSelectedFinetuneId = computed({
  get: () => props.selectedFinetuneId,
  set: (v: number | null) => emit('update:selectedFinetuneId', v),
})
const localThreshold = computed({
  get: () => props.threshold,
  set: (v: number) => emit('update:threshold', v),
})
const localViewMode = computed({
  get: () => props.viewMode,
  set: (v: 'grid' | 'list') => emit('update:viewMode', v),
})

const goTraining = () => router.push('/training')
</script>

<template>
  <el-card shadow="never" class="filter-card">
    <div class="filter-row filter-row--single">
      <!-- ===== 过滤组 ===== -->
      <div class="filter-group filter-group--filter">
        <el-select v-model="localStatusFilter" size="default" placeholder="状态"
          class="filter-cell filter-cell--select app-select app-select--medium filter-cell--status">
          <el-option
            v-for="opt in statusOptions" :key="opt.value"
            :label="opt.label" :value="opt.value"
          />
        </el-select>
        <el-input v-model="localKeyword" :prefix-icon="Search" placeholder="搜索文件名/类别"
          clearable size="default" class="filter-cell filter-cell--search" />
      </div>

      <!-- ===== AI 组 (核心) ===== -->
      <div class="filter-group filter-group--ai">
        <el-tooltip
          placement="top" :show-after="200"
          :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
        >
          <el-select
            v-model="localSelectedFinetuneId"
            :placeholder="finetuneModels.length === 0 ? '选择 fine-tune 模型 (仅本数据集已激活)' : '选择 fine-tune 模型'"
            size="default"
            :fit-input-width="false"
            popper-class="app-select-dropdown model-select-dropdown"
            class="app-select"
            :disabled="finetuneModels.length === 0"
            filterable
            :empty-text="finetuneModels.length === 0 ? '该数据集暂无训练模型' : '无可用模型'"
          >
            <el-option
              v-for="m in finetuneModels" :key="m.id" :value="m.id"
              :label="`${m.name} · ${m.base_model}`"
            >
              <div class="model-option-row">
                <span>{{ m.name }}</span>
                <span class="model-option-base">· {{ m.base_model }}</span>
                <span class="model-option-acc">{{ (m.accuracy * 100).toFixed(1) }}%</span>
              </div>
            </el-option>
          </el-select>
        </el-tooltip>

        <el-tooltip
          placement="top" :show-after="200"
          :content="`置信度阈值: 决定一张图被自动标注的最低可信度。>= ${(threshold * 100).toFixed(0)}% 将直接标注, 其余保留为「待标注」由人工复核`"
        >
          <div class="threshold-row threshold-row--inline">
            <span class="threshold-label">置信度阈值</span>
            <el-slider v-model="localThreshold" :min="0.1" :max="1.0" :step="0.05" :show-tooltip="true"
              :format-tooltip="(v: number) => `阈值 ${(v * 100).toFixed(0)}%`"
              class="threshold-slider threshold-slider--inline" />
            <el-tag type="primary" effect="dark" class="threshold-value">
              {{ (threshold * 100).toFixed(0) }}%
            </el-tag>
          </div>
        </el-tooltip>

        <el-tooltip
          placement="top" :show-after="200"
          :content="previewTooltipText"
        >
          <el-button
            plain :icon="DataAnalysis" :loading="previewing"
            :disabled="!hasImages || !hasFinetuneModel"
            @click="emit('preview')"
            class="filter-cell filter-cell--btn"
          >测评</el-button>
        </el-tooltip>
      </div>

      <!-- ===== 操作组 ===== -->
      <div class="filter-group filter-group--ops">
        <el-tooltip
          placement="top" :show-after="200"
          :content="selectedCount > 0
            ? '跳转到标注工作台, 默认显示选中图片中的第一张待标注图'
            : '跳转到标注工作台, 继续人工确认/修正'"
        >
          <el-button :icon="EditPen" @click="emit('goAnnotate')" class="filter-cell filter-cell--btn">去标注</el-button>
        </el-tooltip>
      </div>

      <!-- ===== 辅助组 (批量 + 视图) ===== -->
      <div class="filter-group filter-group--aux">
        <el-button
          plain
          size="small"
          :type="allOnPageSelected ? 'primary' : 'default'"
          :icon="allOnPageSelected ? Minus : Check"
          @click="emit('toggleSelectAll')"
          class="filter-cell filter-cell--btn"
        >{{ allOnPageSelected ? '取消' : '全选' }}</el-button>
        <el-tag v-if="selectedCount > 0" type="warning" effect="dark" size="small" class="batch-count">
          {{ selectedCount }}
        </el-tag>
        <el-button size="small" :disabled="selectedCount === 0" type="warning"
          :icon="RefreshLeft" @click="emit('batchClear')" class="filter-cell filter-cell--btn">清除标注</el-button>
        <el-button size="small" :disabled="selectedCount === 0" type="danger"
          :icon="Delete" @click="emit('batchDelete')" class="filter-cell filter-cell--btn">删除</el-button>
        <div class="view-mode-switch" :title="viewMode === 'grid' ? '网格视图' : '列表视图'">
          <button
            class="mode-btn" :class="{ active: localViewMode === 'grid' }"
            :title="'网格视图'" @click="localViewMode = 'grid'"
          >
            <el-icon><Grid /></el-icon>
          </button>
          <button
            class="mode-btn" :class="{ active: localViewMode === 'list' }"
            :title="'列表视图'" @click="localViewMode = 'list'"
          >
            <el-icon><List /></el-icon>
          </button>
        </div>
      </div>
    </div>

    <!-- AI 配置实时提示: 告知用户当前模型/阈值将如何作用于待标图片 -->
    <div class="ai-config-hint" :class="{ 'ai-config-hint--warn': !hasFinetuneModel }">
      <el-icon class="ai-config-hint__icon">
        <component :is="hasFinetuneModel ? InfoFilled : WarningFilled" />
      </el-icon>
      <span v-if="hasFinetuneModel">
        当前将用
        <b class="ai-config-hint__model">{{ displayModel }}</b>
        对 <b>{{ pendingCount }}</b> 张「待标注」图片进行预标注,
        置信度 ≥ <b>{{ (threshold * 100).toFixed(0) }}%</b> 的图片会自动落标,
        其余保留为「待标注」由人工复核。
      </span>
      <span v-else>
        当前数据集<b>暂无训练模型</b>, <b>测评</b>功能不可用。
        请先到「训练任务」页选定本数据集启动训练, 训练完成后再来预标注和测评。
      </span>
      <div class="ai-config-hint__actions">
        <el-button v-if="!hasFinetuneModel" type="warning" size="small" round
          :icon="Promotion" @click="goTraining">
          前往训练任务
        </el-button>
        <el-tag v-else-if="pendingCount === 0" type="success" size="small" effect="plain">
          暂无待标注
        </el-tag>
        <el-tag v-else size="small" type="info" effect="plain">
          待标 {{ pendingCount }}
        </el-tag>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.filter-card {
  border-radius: var(--radius-md) !important;
  margin-bottom: 12px;
}
.filter-card :deep(.el-card__body) { padding: 12px 16px; }

/* 单行紧凑布局: flex + flex-wrap (内容超出时换行) */
.filter-row--single {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.filter-group:not(:last-child) {
  border-right: 1px solid var(--border-soft);
  padding-right: 8px;
}
.filter-group--filter { flex: 0 1 260px; min-width: 260px; }
.filter-group--ai     { flex: 0 1 auto; min-width: 0; }
.filter-group--ops    { flex: 0 0 auto; }
.filter-group--aux    { flex: 0 0 auto; margin-left: auto; gap: 4px; }

.filter-cell { flex: 0 0 auto; }
.filter-cell--status { flex: 0 0 88px; min-width: 88px; }
.filter-cell--search { flex: 1 1 140px; min-width: 120px; max-width: 180px; }

/* AI 组内: 模型下拉允许收缩, 阈值行紧凑 */
.filter-group--ai .app-select { min-width: 0; flex: 0 0 200px; }
.filter-group--ai > .el-tooltip:first-child { flex: 0 0 200px; min-width: 0; }

/* 模型选项行内三段: 名称 + 灰字基础模型 + 绿字准确率 */
.model-option-row { display: flex; align-items: center; gap: 6px; }
.model-option-base { color: #909399; font-size: 12px; }
.model-option-acc { margin-left: auto; color: #67c23a; font-size: 12px; }

/* 阈值行内联: 紧凑模式 */
.threshold-row--inline { display: flex; align-items: center; gap: 4px; flex: 0 0 auto; min-width: 0; }
.threshold-label { font-size: 12px; color: var(--text-secondary); white-space: nowrap; flex: 0 0 auto; }
.threshold-slider--inline { margin: 0 4px; flex: 0 0 120px; min-width: 120px; }
.threshold-slider--inline :deep(.el-slider__runway) { margin: 0 6px; }
.threshold-value { font-weight: 600; min-width: 40px; text-align: center; flex: 0 0 auto; font-size: 12px; }

.batch-count { font-weight: 600; }

/* 视图切换单元 */
.view-mode-switch {
  display: inline-flex;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.mode-btn {
  background: transparent;
  border: 0;
  padding: 4px 6px;
  cursor: pointer;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.mode-btn.active { background: var(--brand-primary); color: #fff; }
.mode-btn:hover:not(.active) { background: var(--bg-soft); }

/* AI 配置实时提示 */
.ai-config-hint {
  margin-top: 12px;
  padding: 10px 14px;
  background: linear-gradient(90deg, rgba(64, 158, 255, 0.04) 0%, rgba(64, 158, 255, 0.01) 100%);
  border-left: 3px solid var(--color-primary, #409eff);
  border-radius: 4px;
  font-size: 12.5px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 1.5;
  flex-wrap: wrap;
}
.ai-config-hint--warn {
  background: linear-gradient(90deg, rgba(255, 168, 64, 0.08) 0%, rgba(255, 168, 64, 0.02) 100%);
  border-left-color: var(--brand-warning, #ffa940);
}
.ai-config-hint--warn .ai-config-hint__icon { color: var(--brand-warning, #ffa940); }
.ai-config-hint__icon {
  color: var(--color-primary, #409eff);
  font-size: 14px;
  flex: 0 0 auto;
}
.ai-config-hint__model { color: var(--color-primary, #409eff); }
.ai-config-hint b { color: var(--text-primary); font-weight: 600; }
.ai-config-hint__actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}

/* 响应式: 窄屏组内换行, 组不再用 | 分隔 */
@media (max-width: 1100px) {
  .filter-group:not(:last-child) {
    border-right: none;
    padding-right: 0;
  }
  .filter-group--aux { margin-left: 0; flex-basis: 100%; justify-content: flex-end; }
  .filter-group { flex-basis: 100%; }
  .filter-group--ai { flex-basis: 100%; }
  .filter-cell--search { width: 100%; flex: 1 1 100%; }
  .threshold-row--inline { flex: 1 1 100%; min-width: 0; }
  .threshold-slider--inline { flex: 1 1 auto; min-width: 100px; }
}
</style>
