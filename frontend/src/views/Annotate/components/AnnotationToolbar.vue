<!--
  AnnotationToolbar.vue (v2.5.7 拆分自 Annotate.vue, v2.5.15 +已人工标注卡)
  ===================================================
  标注工作台顶部工具栏:
  - 6 个统计卡片 (待标注 / 已人工标注 / AI 已标 / 本轮已标 / 本轮总耗时 / 总累计耗时)
    · 已人工标注: 包含已确认 + 已修正, 与 DatasetDetail 统计卡口径一致
  - 数据集选择 + 任务类型徽章 (popover 详解)
  - 模型选择 (fine-tune / 基础模型)
  - 置信度阈值 + IoU 阈值 + AI 模型
  - 启动 AI 预标注 按钮 (与其它控件同一行)

  Props (页面私有子组件, 接收父组件状态):
    datasets, datasetId, currentTaskTypeRaw
    modelName, threshold, iouThreshold, detectionModelName
    models, finetuneModels, selectedModelId, activeModel, useFinetune
    stats, pendingCount, aiLabeledCount,
    humanConfirmedCount, humanCorrectedCount,
    sessionStats
    autoLabeling

  Emits:
    dataset-change, task-type-filter-change
    threshold-change, iou-threshold-change
    detection-model-change, selected-model-change
    model-name-change, use-finetune-change
    ai-start
-->
<template>
  <div>
    <!-- 顶部统计 (6 卡片, 6×4=24 列; 参考 DatasetDetail 统计卡布局)
         v2.5.15: 新增"已人工标注"卡 (已确认 + 已修正), 数据来源 status_counts
         · 修复: 之前工作台只显示待标注/AI已标/本会话指标, 缺少数据集级人工标注累计数
         · 现在与 DatasetDetail 顶部统计卡口径保持一致, 用户切换 dataset 时数据同步 -->
    <el-row v-if="stats" :gutter="12" style="margin-bottom: 16px;">
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="待标注" :value="pendingCount" suffix="张"
            :value-style="{ color: '#409eff' }" />
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="已人工标注"
            :value="humanConfirmedCount + humanCorrectedCount" suffix="张"
            :value-style="{ color: '#67c23a' }" />
          <div class="stat-meta">
            <span style="color: #67c23a;">已确认 {{ humanConfirmedCount }}</span>
            <span class="stat-meta-sep">·</span>
            <span style="color: #e6a23c;">已修正 {{ humanCorrectedCount }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="AI 已标" :value="aiLabeledCount" suffix="张"
            :value-style="{ color: '#909399' }" />
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="本会话已标" :value="sessionStats.confirmed + sessionStats.corrected" suffix="张" />
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="本会话耗时"
            :value="Number((sessionStats.total_time_ms / 1000).toFixed(1))" :precision="1" suffix="秒" />
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="估算 AI 节省" :value="stats.estimated_saved_seconds || 0"
            suffix="秒" :value-style="{ color: '#e6a23c' }" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 顶部控制条 -->
    <el-card style="margin-bottom: 16px;">
      <el-form inline>
        <!-- 任务类型筛选 (v2.5.20: 移除"全部任务类型"选项)
             - 固定排序: 图片分类 / 目标检测 / 图片分割 (3 项, 不含"全部")
             - 联动: 选了某 task type 后, 数据集下拉只显示同类型数据集
             - 若当前选中数据集不匹配新 task type, 由父组件负责重置 datasetId -->
        <el-form-item label="任务类型">
          <el-select
            :model-value="taskTypeFilter"
            @update:model-value="(v: string) => emit('task-type-filter-change', v)"
            class="app-select"
            style="width: 160px;"
          >
            <el-option
              v-for="opt in TASK_TYPE_FILTER_OPTIONS"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="数据集">
          <el-select
            :model-value="datasetId"
            @update:model-value="(v: number) => emit('dataset-change', v)"
            placeholder="请选择" class="app-select" filterable
            :disabled="filteredDatasets.length === 0"
            style="min-width: 220px;"
          >
            <el-option v-for="d in filteredDatasets" :key="d.id" :label="d.name" :value="d.id" />
            <!-- 空态文案 -->
            <template #empty>
              <div style="padding: 8px 12px; color: #909399; font-size: 12px;">
                当前任务类型下没有数据集, 请切换任务类型或新建数据集
              </div>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider
            :model-value="threshold"
            @update:model-value="(v: number) => emit('threshold-change', v)"
            :min="0.1" :max="1.0" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => `${(v * 100).toFixed(0)}%`"
          />
        </el-form-item>
        <!-- 任务专属控件 (v2.5.22: 统一风格 — 都有 label / 控件宽度 160px / 位置对齐)
             · 分类: fine-tune 开关 + 模型下拉 (合并到 1 个 form-item)
             · 检测: IoU 阈值滑块 + AI 模型下拉 (2 个 form-item)
             · 分割: 无任务专属参数 (保持空白)
             · 之前问题: 分类的"模型选择"无 label, 且与"使用项目训练模型"开关隔着置信度阈值, 视觉跳 -->
        <template v-if="currentTaskTypeRaw === 'classification'">
          <el-form-item label="模型">
            <el-switch
              :model-value="useFinetune"
              @update:model-value="(v: boolean) => emit('use-finetune-change', v)"
              active-text="项目模型" inactive-text="基础模型"
              inline-prompt style="--el-switch-on-color: #67c23a; margin-right: 8px;"
            />
            <el-tooltip
              v-if="useFinetune"
              :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
              placement="top">
              <el-select
                :model-value="selectedModelId"
                @update:model-value="(v: number | null) => emit('selected-model-change', v)"
                class="app-select" style="width: 160px;"
                :fit-input-width="false" popper-class="app-select-dropdown"
                :disabled="finetuneModels.length === 0"
                :placeholder="finetuneModels.length === 0 ? '选择 fine-tune 模型 (仅本数据集已激活)' : '选择 fine-tune 模型'"
              >
                <el-option
                  v-for="m in finetuneModels" :key="m.id"
                  :value="m.id"
                  :label="`${m.name} · ${m.base_model}`"
                >
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span>{{ m.name }}</span>
                    <span style="color: #909399; font-size: 12px;">· {{ m.base_model }}</span>
                    <span style="margin-left: auto; color: #67c23a; font-size: 12px;">{{ ((m.accuracy ?? 0) * 100).toFixed(1) }}%</span>
                  </div>
                </el-option>
              </el-select>
            </el-tooltip>
            <el-tooltip v-else content="基础模型输出会被归一为「未知」, 请谨慎使用" placement="top">
              <el-select
                :model-value="modelName"
                @update:model-value="(v: string) => emit('model-name-change', v)"
                class="app-select" style="width: 160px;"
                :fit-input-width="false" popper-class="app-select-dropdown"
              >
                <el-option v-for="m in models" :key="m.name" :label="`${m.name} (${m.params})`" :value="m.name">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                    <span>{{ m.name }}</span>
                    <span style="color: #909399; font-size: 12px;">({{ m.params }})</span>
                  </div>
                </el-option>
              </el-select>
            </el-tooltip>
          </el-form-item>
        </template>
        <template v-else-if="currentTaskTypeRaw === 'detection'">
          <el-form-item label="IoU 阈值 (NMS)">
            <el-slider
              :model-value="iouThreshold"
              @update:model-value="(v: number) => emit('iou-threshold-change', v)"
              :min="0.1" :max="0.95" :step="0.05" style="width: 160px;"
              :format-tooltip="(v: number) => v.toFixed(2)"
            />
          </el-form-item>
          <el-form-item label="AI 模型">
            <el-select
              :model-value="detectionModelName"
              @update:model-value="(v: string) => emit('detection-model-change', v)"
              placeholder="选择 YOLO 模型" class="app-select" style="width: 160px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option v-for="m in DETECTION_MODELS" :key="m" :value="m" :label="m" />
            </el-select>
          </el-form-item>
        </template>
        <!-- 启动 AI 预标注 (与上方控件同一行)
             v2.5.23: 移除"当前激活"tag — 切换 task_type 时分类/检测的 form-item
             进出导致"当前激活: xxx"文本闪出, 信息本身也跟模型下拉语义重复
             (用户在分类下拉里选哪个, 跟后端激活哪个是同步的) -->
        <el-form-item>
          <el-button
            type="primary" :icon="MagicStick"
            :loading="autoLabeling"
            @click="emit('ai-start')"
          >启动 AI 预标注</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { MagicStick, Lightning } from '@element-plus/icons-vue'

const DETECTION_MODELS = ['yolov8n', 'yolov8s', 'yolov8m', 'yolov8l', 'yolov8x']

/**
 * 任务类型筛选下拉选项 (v2.5.20 调整: 移除"全部"项)
 * - 固定排序: 图片分类 / 目标检测 / 图片分割
 * - 与 utils/taskType.ts 的 TASK_TYPE_OPTIONS 保持一致
 */
const TASK_TYPE_FILTER_OPTIONS = [
  { value: 'classification', label: '图片分类' },
  { value: 'detection',      label: '目标检测' },
  { value: 'segmentation',   label: '图片分割' },
] as const

interface Dataset { id: number; name: string; task_type?: string }
interface Model { id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string }

const props = defineProps<{
  datasets: Dataset[]
  datasetId: number | null
  /** v2.5.19: 任务类型筛选值, 'classification' / 'detection' / 'segmentation' */
  taskTypeFilter: string
  currentTaskTypeRaw: string
  modelName: string
  threshold: number
  iouThreshold: number
  detectionModelName: string
  models: Model[]
  finetuneModels: Model[]
  selectedModelId: number | null
  activeModel: Model | null
  useFinetune: boolean
  stats: any
  pendingCount: number
  aiLabeledCount: number
  /** v2.5.15: 已确认人工标注数 (status_counts.human_confirmed) */
  humanConfirmedCount: number
  /** v2.5.15: 已修正人工标注数 (status_counts.human_corrected) */
  humanCorrectedCount: number
  sessionStats: { confirmed: number; corrected: number; total_time_ms: number }
  autoLabeling: boolean
}>()

const emit = defineEmits<{
  (e: 'dataset-change', v: number): void
  /** v2.5.19: 任务类型筛选变更 */
  (e: 'task-type-filter-change', v: string): void
  (e: 'threshold-change', v: number): void
  (e: 'iou-threshold-change', v: number): void
  (e: 'detection-model-change', v: string): void
  (e: 'selected-model-change', v: number | null): void
  (e: 'model-name-change', v: string): void
  (e: 'use-finetune-change', v: boolean): void
  (e: 'ai-start'): void
}>()

/**
 * 按 taskTypeFilter 过滤后的数据集列表 (v2.5.20)
 * - 父组件保证 taskTypeFilter 永远是非空且属于 3 个 task_type 之一
 * - 始终按 task_type 过滤, 保留父组件传入的原始顺序
 */
const filteredDatasets = computed(() => {
  const f = props.taskTypeFilter
  return props.datasets.filter((d) => (d.task_type || 'classification') === f)
})
</script>

<style scoped>
.stat-card { text-align: center; }
/* v2.5.15: 统计卡副标题 (已人工标注卡下方的"已确认 N · 已修正 N")
   类名 .stat-meta 与 DatasetDetail/index.vue 保持一致, 便于跨页面维护 */
.stat-meta {
  font-size: 12px;
  margin-top: 4px;
  color: #606266;
  letter-spacing: 0.3px;
}
.stat-meta-sep {
  margin: 0 4px;
  color: #c0c4cc;
}
</style>
