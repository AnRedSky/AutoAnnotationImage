<script setup lang="ts">
/**
 * PreviewDialog.vue - 置信度测评结果弹窗
 * ======================================
 *
 * 抽离动机 (v3.0.0 Phase H):
 * - DatasetDetail/index.vue 1956 行, 预览弹窗模板 80+ 行
 * - 弹窗自身包含: 顶部 3 张统计卡 + 模型信息 + 3 个 Tab (会标/需人工/无匹配)
 * - 抽离后 page 只剩 <PreviewDialog v-model ... /> 一行
 *
 * 数据流:
 * - 父组件负责调用后端拿到 previewResult, 弹窗仅展示
 * - 父组件通过 taskType prop 透传给 PreviewList
 * - 弹窗底部「应用并启动预标注」通过 emit('apply') 通知父组件
 */
import { computed, ref } from 'vue'
import { Lightning, Check, InfoFilled, CircleClose } from '@element-plus/icons-vue'
import PreviewList from './PreviewList.vue'

interface PreviewItem {
  image_id: number
  filename: string
  reason: string
  would_label: boolean
  top1?: string
  top1_conf?: number
  bbox_count?: number
  max_softmax?: number
  category_summary?: { id: number; name: string; count: number }[]
}

interface PreviewResult {
  total: number
  would_label: number
  need_human: number
  no_match: number
  threshold: number
  used_finetune: boolean
  finetune_name?: string
  model_name?: string
  base_model?: string
  warning?: string
  items: PreviewItem[]
}

const props = defineProps<{
  modelValue: boolean
  result: PreviewResult | null
  taskType: 'classification' | 'detection' | 'segmentation'
  autoLabeling: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  'apply': []
}>()

const activeTab = ref('would')

const groups = computed(() => {
  const r = props.result
  if (!r) return { would: [] as PreviewItem[], human: [] as PreviewItem[], none: [] as PreviewItem[] }
  return {
    would: r.items.filter((x) => x.would_label),
    human: r.items.filter((x) => !x.would_label && x.reason !== 'no_match'),
    none:  r.items.filter((x) => x.reason === 'no_match'),
  }
})

const onClose = () => emit('update:modelValue', false)
const onApply = () => emit('apply')
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="置信度测评结果"
    width="980px"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="result" class="preview-summary">
      <el-alert
        v-if="result.warning"
        :title="result.warning"
        type="warning" :closable="false" show-icon
        style="margin-bottom: 12px;"
      />
      <div class="preview-summary__cards">
        <div class="preview-card preview-card--success">
          <div class="preview-card__num">{{ result.would_label }}</div>
          <div class="preview-card__label">将被自动标注</div>
          <div class="preview-card__hint">≥ 阈值 {{ (result.threshold * 100).toFixed(0) }}% 且在项目类目内</div>
        </div>
        <div class="preview-card preview-card--warning">
          <div class="preview-card__num">{{ result.need_human }}</div>
          <div class="preview-card__label">需人工复核</div>
          <div class="preview-card__hint">低于阈值 / top-1 不在项目类目</div>
        </div>
        <div class="preview-card preview-card--danger">
          <div class="preview-card__num">{{ result.no_match }}</div>
          <div class="preview-card__label">无匹配</div>
          <div class="preview-card__hint">模型输出与项目类目无交集</div>
        </div>
      </div>
      <div class="preview-summary__model">
        测评模型:
        <b v-if="result.used_finetune && result.finetune_name">
          {{ result.finetune_name }}
          <span style="color: #909399; font-weight: normal; font-size: 12px;">
            (基础模型 {{ result.base_model || result.model_name }})
          </span>
        </b>
        <b v-else>{{ result.model_name || '(空)' }}</b>
        <el-tag
          v-if="result.used_finetune" type="success" size="small" effect="plain"
          style="margin-left: 8px;"
        >fine-tune</el-tag>
        <el-tag
          v-else type="info" size="small" effect="plain"
          style="margin-left: 8px;"
        >timm 预训练</el-tag>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="preview-tabs">
      <el-tab-pane :name="'would'">
        <template #label>
          <span><el-icon><Check /></el-icon> 会被标注 ({{ groups.would.length }})</span>
        </template>
        <PreviewList :items="groups.would" :task-type="taskType" />
      </el-tab-pane>
      <el-tab-pane :name="'human'">
        <template #label>
          <span><el-icon><InfoFilled /></el-icon> 需人工复核 ({{ groups.human.length }})</span>
        </template>
        <PreviewList :items="groups.human" :task-type="taskType" />
      </el-tab-pane>
      <el-tab-pane :name="'none'">
        <template #label>
          <span><el-icon><CircleClose /></el-icon> 无匹配 ({{ groups.none.length }})</span>
        </template>
        <PreviewList :items="groups.none" :task-type="taskType" />
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <el-button @click="onClose">关闭</el-button>
      <el-button
        type="primary" :icon="Lightning"
        :loading="autoLabeling"
        @click="onApply"
      >应用并启动预标注</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.preview-summary { margin-bottom: 18px; }
.preview-summary__cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}
.preview-card {
  padding: 14px;
  border-radius: 6px;
  border: 1px solid #ebeef5;
  background: #fafafa;
  text-align: center;
}
.preview-card--success { border-color: #67c23a; background: #f0f9eb; }
.preview-card--warning { border-color: #e6a23c; background: #fdf6ec; }
.preview-card--danger { border-color: #f56c6c; background: #fef0f0; }
.preview-card__num { font-size: 28px; font-weight: 700; line-height: 1.1; }
.preview-card__label { font-size: 13px; color: #303133; margin-top: 4px; }
.preview-card__hint { font-size: 12px; color: #909399; margin-top: 2px; }
.preview-summary__model {
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
}
.preview-tabs { margin-top: 8px; }
</style>
