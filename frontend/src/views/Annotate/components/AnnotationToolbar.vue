<!--
  AnnotationToolbar.vue (v2.5.7 拆分自 Annotate.vue, v2.5.15 +已人工标注卡, v2.5.48 +类别卡)
  ===================================================
  标注工作台顶部工具栏:
  - 5 个统计卡片 (待标注 / 已人工标注 / AI 已标 / 类别 / 不合格) 同一行 flex 等分
    · 已人工标注: 包含已确认 + 已修正, 与 DatasetDetail 统计卡口径一致
    · 类别: 显示数据集总类数; hover 展示每类的总样本 / 已人工标 / AI 已标
      (类目级覆盖度, 反映任务复杂度 + 各类的标注进度)
    · 不合格 (v3.0.0): 来源 stats.unqualified_count, 副标题显示占比
      正交于 status 状态机, 由标注员 / AI 自动检测写入 quality_flag
  - v2.5.48: 移除 3 个低价值指标 ——
    · 本会话已标: 与「已人工标注」语义重复 (差在 session scope, 切换 dataset 即重置)
    · 本会话耗时: 孤立秒数无意义, 标注效率 = 已标/耗时 已在 DatasetDetail 体现
    · 估算 AI 节省: 后端硬编码 ai_labeled * 3 (拍脑袋), 不可验证 / 不可解释
  - 数据集选择 + 任务类型徽章 (popover 详解)
  - 模型选择 (项目模型 / 基础模型): 三任务统一交互
    · classification: 项目模型 → fine-tune 下拉; 基础模型 → timm ImageNet 下拉
    · detection:      项目模型 → fine-tune 下拉; 基础模型 → yolov8* 预训练下拉
    · segmentation:   项目模型 → fine-tune 下拉; 基础模型 → 暂不支持 (禁用提示)
  - 置信度阈值 + IoU 阈值 (检测专属)
  - 启动 AI 预标注 按钮 (与其它控件同一行)

  v3.0.0 不合格指标增强:
  - 布局从 el-row/el-col (24 栅格无法 5 等分) 改为 flex 等分, 与 DatasetStatsRow 风格一致
  - 新增第 5 张「不合格」卡 (红色), 副标题显示不合格占总图片百分比
  - 数据源: stats.unqualified_count (后端 /api/stats/dataset/{id} 已返回)

  Props (页面私有子组件, 接收父组件状态):
    datasets, datasetId, currentTaskTypeRaw
    modelName, threshold, iouThreshold, detectionModelName
    models, finetuneModels, selectedModelId, activeModel, useFinetune
    stats, pendingCount, aiLabeledCount,
    humanConfirmedCount, humanCorrectedCount,
    categories                          // v2.5.48 新增: 类目列表, 供「类别」卡 + hover 详情
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
    <!-- 顶部统计 (v3.0.0: 5 卡同一行 flex 等分, 替代 el-row/el-col)
         · 待标注 (蓝)        来源: status_counts.pending
         · 已人工标注 (绿)    来源: status_counts.human_confirmed + human_corrected
                             · 副标题: 已确认 N · 已修正 N
         · AI 已标 (灰)       来源: status_counts.ai_labeled
         · 类别 (紫) v2.5.48  来源: categories.length
                             · hover tooltip: 每类的总样本 / 已人工标 / AI 已标
         · 不合格 (红) v3.0.0 来源: stats.unqualified_count
                             · 副标题: 占比 N% (不合格 / 图片总数)

         v2.5.48 移除的 3 个低价值指标:
         · 本会话已标: 与「已人工标注」语义重叠 (差在 session scope, 切换 dataset 即重置)
         · 本会话耗时: 孤立秒数无意义, 标注效率 = 已标/耗时 已在 DatasetDetail 体现
         · 估算 AI 节省: 后端硬编码 ai_labeled * 3 (拍脑袋), 不可验证 / 不可解释
    -->
    <div v-if="stats" class="annotate-stats">
      <el-card shadow="hover" class="stat-card">
        <el-statistic title="待标注" :value="pendingCount" suffix="张"
          :value-style="{ color: '#409eff' }" />
      </el-card>
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
      <el-card shadow="hover" class="stat-card">
        <el-statistic title="AI 已标" :value="aiLabeledCount" suffix="张"
          :value-style="{ color: '#909399' }" />
      </el-card>
      <!-- v2.5.48: 类别卡 — 显示总类数, hover 看每类详情
           - el-tooltip 触发 hover 弹出详细面板
           - 面板内: 类目名 + 3 列计数 (总样本 / 已人工 / AI 已标)
           - 数据来源: datasetApi.categories 已在父组件加载到 categories.value
           - 空态: 0 个类目时显示「-」 + tooltip 提示「数据集未配置类目」 -->
      <el-tooltip
        placement="top"
        :disabled="categories.length === 0"
        :show-after="200"
      >
        <template #content>
          <div v-if="categories.length === 0" style="padding: 4px 8px;">
            当前数据集未配置类目
          </div>
          <div v-else class="category-tooltip">
            <div class="category-tooltip__header">各类目已标进度</div>
            <table class="category-tooltip__table">
              <thead>
                <tr>
                  <th class="ct-name">类目</th>
                  <th class="ct-num">总样本</th>
                  <th class="ct-num">已人工</th>
                  <th class="ct-num">AI 已标</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in categories" :key="c.id">
                  <td class="ct-name">
                    <span class="ct-dot" :style="{ background: c.color || '#409eff' }"></span>
                    {{ c.name }}
                  </td>
                  <td class="ct-num">{{ c.sample_count ?? 0 }}</td>
                  <td class="ct-num ct-human">{{ c.human_labeled_count ?? 0 }}</td>
                  <td class="ct-num ct-ai">{{ c.ai_labeled_count ?? 0 }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <el-card shadow="hover" class="stat-card stat-card--clickable">
          <el-statistic :title="categories.length === 0 ? '类别' : `类别 (共 ${categories.length} 类)`"
            :value="categories.length" suffix="类"
            :value-style="{ color: categories.length > 0 ? '#722ed1' : '#c0c4cc' }" />
        </el-card>
      </el-tooltip>
      <!-- v3.0.0: 不合格卡 — 与上述 4 卡同一行 flex 等分
           - 数据源: stats.unqualified_count (后端 /api/stats/dataset/{id} 已返回)
           - 副标题: 占比 N% (不合格 / 图片总数)
           - 视觉风格: 红色, 与 DatasetStatsRow 不合格卡一致 -->
      <el-card shadow="hover" class="stat-card stat-card--unqualified">
        <el-statistic title="不合格" :value="unqualifiedCount" suffix="张"
          :value-style="{ color: '#f56c6c' }" />
        <div class="stat-meta">
          占比 {{ unqualifiedRatioPct }}%
        </div>
      </el-card>
    </div>

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
        <!-- 「模型」form-item: 三任务统一交互 (v2.5.24 对齐图像分类)
             · classification: 项目模型 → fine-tune 下拉; 基础模型 → timm ImageNet 下拉
             · detection:      项目模型 → fine-tune 下拉; 基础模型 → yolov8* 预训练下拉 (COCO 80 类)
             · segmentation:   项目模型 → fine-tune 下拉; 基础模型 → 暂不支持 (禁用提示)
             · 共享 state: useFinetune / selectedModelId / finetuneModels, 由父组件按 dataset 自动隔离 -->
        <el-form-item label="模型">
          <el-switch
            :model-value="useFinetune"
            @update:model-value="(v: boolean) => emit('use-finetune-change', v)"
            active-text="项目模型" inactive-text="基础模型"
            inline-prompt style="--el-switch-on-color: #67c23a; margin-right: 8px;"
          />
          <!-- 项目模型分支: fine-tune 模型下拉 (三任务共用同一份 finetuneModels) -->
          <el-tooltip
            v-if="useFinetune"
            :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
            placement="top">
            <el-select
              :model-value="selectedModelId"
              @update:model-value="(v: number | null) => emit('selected-model-change', v)"
              class="app-select" style="width: 260px;"
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
          <!-- 基础模型分支: 按任务类型显示对应预训练模型列表
               v2.5.47: 统一改为从 models prop 过滤, 替代原 detection 任务硬编码 DETECTION_MODELS
               models 由父组件 Annotate/index.vue 在 onMounted 调 autoAnnotateApi.models() 拉取
               与 /api/auto-annotate/models 单一权威源对齐, 包含 description / recommended 字段 -->
          <el-tooltip
            v-else-if="currentTaskTypeRaw === 'classification'"
            content="基础模型输出会被归一为「未知」, 请谨慎使用" placement="top"
          >
            <el-select
              :model-value="modelName"
              @update:model-value="(v: string) => emit('model-name-change', v)"
              class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option
                v-for="m in baseModelsByTask.classification" :key="m.name"
                :value="m.name" :label="`${m.name} (${m.params || ''})`"
              >
                <div class="annotate-base-option">
                  <el-tag v-if="m.recommended" size="small" type="success" effect="dark"
                    style="font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
                  >推荐</el-tag>
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span class="annotate-base-option__name">{{ m.name }}</span>
                  <span class="annotate-base-option__params">({{ m.params }})</span>
                </div>
                <div v-if="m.description" class="annotate-base-option__desc">适用: {{ m.description }}</div>
              </el-option>
            </el-select>
          </el-tooltip>
          <el-tooltip
            v-else-if="currentTaskTypeRaw === 'detection'"
            content="预训练 YOLO 仅识别 COCO 80 类, 仅与项目类目重合的部分会写入 BBox" placement="top"
          >
            <el-select
              :model-value="detectionModelName"
              @update:model-value="(v: string) => emit('detection-model-change', v)"
              placeholder="选择 YOLO 模型" class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option
                v-for="m in baseModelsByTask.detection" :key="m.name"
                :value="m.name" :label="`${m.name} (${m.params || ''})`"
              >
                <div class="annotate-base-option">
                  <el-tag v-if="m.recommended" size="small" type="success" effect="dark"
                    style="font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
                  >推荐</el-tag>
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span class="annotate-base-option__name">{{ m.name }}</span>
                  <span class="annotate-base-option__params">({{ m.params }})</span>
                </div>
                <div v-if="m.description" class="annotate-base-option__desc">适用: {{ m.description }}</div>
              </el-option>
            </el-select>
          </el-tooltip>
          <!-- v2.5.46: 分割预训练下拉 (torchvision COCO 21 类)
               v2.5.47: 同样改为从 baseModelsByTask.segmentation 过滤, 含 description
               由 useAutoAnnotate 同步调 /api/auto-annotate/run-segmentation-pretrained -->
          <el-tooltip
            v-else-if="currentTaskTypeRaw === 'segmentation'"
            content="torchvision 预训练 (COCO 21 类), 输出仅与项目类目重合时落标" placement="top"
          >
            <el-select
              :model-value="segmentationModelName"
              @update:model-value="(v: string) => emit('segmentation-model-change', v)"
              placeholder="选择 torchvision 分割模型" class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option
                v-for="m in baseModelsByTask.segmentation" :key="m.name"
                :value="m.name" :label="`${m.name} (${m.params || ''})`"
              >
                <div class="annotate-base-option">
                  <el-tag v-if="m.recommended" size="small" type="success" effect="dark"
                    style="font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
                  >推荐</el-tag>
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span class="annotate-base-option__name">{{ m.name }}</span>
                  <span class="annotate-base-option__params">({{ m.params }})</span>
                </div>
                <div v-if="m.description" class="annotate-base-option__desc">适用: {{ m.description }}</div>
              </el-option>
            </el-select>
          </el-tooltip>
          <el-tooltip
            v-else
            content="未知任务类型, 请刷新页面" placement="top"
          >
            <el-select
              :model-value="null"
              disabled
              placeholder="未知任务"
              class="app-select" style="width: 260px;"
            />
          </el-tooltip>
        </el-form-item>
        <!-- 任务专属参数 (检测的 IoU 阈值; 分类/分割无任务专属参数) -->
        <el-form-item v-if="currentTaskTypeRaw === 'detection'" label="IoU 阈值 (NMS)">
          <el-slider
            :model-value="iouThreshold"
            @update:model-value="(v: number) => emit('iou-threshold-change', v)"
            :min="0.1" :max="0.95" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => v.toFixed(2)"
          />
        </el-form-item>
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
import type { PropType } from 'vue'
import { MagicStick, InfoFilled } from '@element-plus/icons-vue'

/**
 * v2.5.47: 移除原硬编码 DETECTION_MODELS, 改为从 models prop 过滤
 * - 与 /api/auto-annotate/models 单一权威源对齐
 * - 三个任务类型都通过 baseModelsByTask 取数, UI 行为一致
 */
const baseModelsByTask = computed<Record<string, any[]>>(() => {
  const grouped: Record<string, any[]> = {
    classification: [],
    detection: [],
    segmentation: [],
  }
  for (const m of (props.models as any[]) || []) {
    if (m?.task_type && grouped[m.task_type]) grouped[m.task_type].push(m)
  }
  return grouped
})

/**
 * 任务类型筛选下拉选项 (v2.5.20 调整: 移除"全部"项)
 * - 固定排序: 图片分类 / 目标检测 / 图片分割
 * - 与 utils/taskType.ts 的 TASK_TYPE_OPTIONS 保持一致
 */
const TASK_TYPE_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'classification', label: '图片分类' },
  { value: 'detection',      label: '目标检测' },
  { value: 'segmentation',   label: '图片分割' },
]

const props = defineProps({
  datasets: { type: Array as PropType<{ id: number; name: string; task_type?: string; image_count?: number }[]>, required: true },
  datasetId: { type: Number as PropType<number | null>, default: null },
  /** v2.5.19: 任务类型筛选值, 'classification' / 'detection' / 'segmentation' */
  taskTypeFilter: { type: String, required: true },
  currentTaskTypeRaw: { type: String, required: true },
  modelName: { type: String, required: true },
  threshold: { type: Number, required: true },
  iouThreshold: { type: Number, required: true },
  detectionModelName: { type: String, required: true },
  /** v2.5.46: 分割预训练模型名 (torchvision COCO 21 类) */
  segmentationModelName: { type: String, required: true },
  models: { type: Array as PropType<{ id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string }[]>, required: true },
  finetuneModels: { type: Array as PropType<{ id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string }[]>, required: true },
  selectedModelId: { type: Number as PropType<number | null>, default: null },
  activeModel: { type: Object as PropType<{ id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string } | null>, default: null },
  useFinetune: { type: Boolean, required: true },
  stats: { type: Object as PropType<any>, default: null },
  pendingCount: { type: Number, required: true },
  aiLabeledCount: { type: Number, required: true },
  /** v2.5.15: 已确认人工标注数 (status_counts.human_confirmed) */
  humanConfirmedCount: { type: Number, required: true },
  /** v2.5.15: 已修正人工标注数 (status_counts.human_corrected) */
  humanCorrectedCount: { type: Number, required: true },
  /**
   * v2.5.48: 数据集类目列表 (供「类别」卡 + hover 详情)
   * - 来源: datasetApi.categories (父组件 Annotate/index.vue onMounted 拉取)
   * - 字段: id, name, color?, sample_count, human_labeled_count, ai_labeled_count
   * - 用途: 顶部「类别」统计卡总数显示, hover tooltip 展示每类进度
   */
  categories: {
    type: Array as PropType<Array<{
      id: number
      name: string
      color?: string
      sample_count?: number
      human_labeled_count?: number
      ai_labeled_count?: number
    }>>,
    default: () => [],
  },
  autoLabeling: { type: Boolean, required: true },
  /** v2.5.36: AI 预标注实时进度 (0-100) */
  autoLabelProgress: { type: Number, default: 0 },
  /** v2.5.36: AI 预标注最近一帧 message */
  autoLabelProgressMessage: { type: String, default: '' },
})

const emit = defineEmits<{
  (e: 'dataset-change', v: number): void
  /** v2.5.19: 任务类型筛选变更 */
  (e: 'task-type-filter-change', v: string): void
  (e: 'threshold-change', v: number): void
  (e: 'iou-threshold-change', v: number): void
  (e: 'detection-model-change', v: string): void
  /** v2.5.46: 分割预训练模型变更 */
  (e: 'segmentation-model-change', v: string): void
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

/**
 * v3.0.0: 不合格图片数 (来源 stats.unqualified_count)
 * - 后端 /api/stats/dataset/{id} 已返回 (正交于 status_counts, 单独字段)
 * - 用于顶部「不合格」统计卡, 与其它 4 张卡同一行 flex 等分
 */
const unqualifiedCount = computed(() => {
  return Number(props.stats?.unqualified_count || 0)
})

/**
 * v3.0.0: 不合格占总图片百分比 (用于「不合格」卡副标题)
 * - 分母: 数据集图片总数 (datasets prop 中匹配当前 datasetId 的 image_count)
 * - 0 张时显示 0%, 避免除零
 */
const unqualifiedRatioPct = computed(() => {
  const total = Number(props.datasets.find((d: any) => d.id === props.datasetId)?.image_count || 0)
  if (total <= 0) return 0
  return Math.round((unqualifiedCount.value / total) * 100)
})
</script>

<style scoped>
/* v3.0.0: flex 等分布局 (替代 el-row/el-col, 支持 5 张卡同一行)
 * - 大屏: 5 列等分 (flex: 1 1 0)
 * - 窄屏 (<=768px): 自动换行为 2 列 (min-width 触发 wrap)
 * - 与 DatasetStatsRow.vue 的 .ds-stats 风格保持一致, 便于跨页面维护 */
.annotate-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.annotate-stats :deep(.el-card) {
  flex: 1 1 0;
  min-width: 150px;
  width: auto;
  margin-bottom: 0;
}

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
/* v2.5.47: 基础模型下拉项样式 (与 BaseModelSelect 保持视觉一致)
   - 名称 + framework 标签 + 推荐标签 + params 同行
   - 描述行 (适用: ...) 在下方单独一行, 长文本省略 */
.annotate-base-option {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
}
.annotate-base-option__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.annotate-base-option__params {
  color: #909399;
  font-size: 12px;
  flex: 0 0 auto;
}
.annotate-base-option__desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: #94a3b8;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* v2.5.48: 类别卡的「悬停查看」提示 (放在 stat-meta 下面, 浅紫引导色) */
.stat-meta--hint {
  color: #722ed1;
  font-size: 11px;
  margin-top: 2px;
  opacity: 0.85;
}
/* v2.5.48: 类别卡 hover 状态 (区别于普通卡, 提示用户可悬停) */
.stat-card--clickable {
  cursor: help;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.stat-card--clickable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(114, 46, 209, 0.15);
}

/* v3.0.0: 不合格卡 (红色, 与 DatasetStatsRow 不合格卡视觉一致) */
.stat-card--unqualified {
  border-color: rgba(245, 108, 108, 0.3) !important;
}
.stat-card--unqualified:hover {
  border-color: #f56c6c !important;
}

/* 响应式: 窄屏 2 列 (flex-basis 50% 减去 gap) */
@media (max-width: 768px) {
  .annotate-stats :deep(.el-card) {
    flex: 1 1 calc(50% - 12px);
    min-width: 0;
  }
}
</style>

<!--
  v2.5.48: 类别 tooltip 表格样式 (全局, 不带 scoped)
  - el-tooltip 的 content slot 渲染在 popper 容器里, scoped 的 [data-v-xxx] 选择器匹配不上
  - 命名空间 .category-tooltip-* 避免污染其它组件
  - 表格风格: 浅色细线, 类目名带颜色圆点, 数字列右对齐 + 等宽数字
-->
<style>
.category-tooltip {
  font-size: 12px;
  line-height: 1.5;
  min-width: 240px;
}
.category-tooltip__header {
  font-weight: 600;
  color: #303133;
  padding-bottom: 6px;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 6px;
}
.category-tooltip__table {
  width: 100%;
  border-collapse: collapse;
}
.category-tooltip__table th,
.category-tooltip__table td {
  padding: 4px 6px;
  text-align: left;
}
.category-tooltip__table th {
  color: #909399;
  font-weight: 500;
  font-size: 11px;
  border-bottom: 1px solid #ebeef5;
}
.category-tooltip__table .ct-name {
  min-width: 90px;
}
.category-tooltip__table .ct-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  width: 56px;
}
.category-tooltip__table .ct-human {
  color: #67c23a;
  font-weight: 600;
}
.category-tooltip__table .ct-ai {
  color: #909399;
}
.category-tooltip__table .ct-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: 1px;
}
</style>
