<!--
  AICorrectionCategoryDialog.vue (v3.6.0 新增)
  ====================================================
  AI 修正类别选择弹窗 (标注工作台 - 业务通用组件)

  用途:
  - 标注工作台「AI 已标」状态图片点「确认修正」时弹出
  - 顶部显示 AI 当前预测的「标注类型/类别信息」(top1 + 置信度 + 候选列表)
  - 下方 el-select 列出「项目所有类别」供用户下拉选择「具体类别」确认修正
  - 适用 3 种任务:
    · 分类: 选择确认的具体类别 (label 单一)
    · 检测: 选择「主类别」作为审计 log 记录 (实际 bbox 仍按用户当前已画的保存)
    · 分割: 选择 mask 类别 (单类分割)

  Props:
    modelValue        : 双向绑定显示状态
    taskType          : 'classification' | 'detection' | 'segmentation'
    aiTop1Label       : AI top1 类别名 (e.g. "猫")
    aiTop1Confidence  : AI top1 置信度 (0-1)
    aiCandidates      : AI Top-5 候选 [{ label, confidence }]
    categories        : 项目所有类别 [{ id, name }]
    defaultCategoryId : 默认选中的类别 (e.g. AI top1 对应的项目类别 id)
    existingSummary   : 检测/分割任务: 用户已有的标注汇总 (e.g. "车辆×2, 行人×1" 或 "mask 已加载, 类别: 道路")

  Emits:
    update:modelValue : 关闭弹窗
    confirm           : 确认选中的类别 (categoryId, labelName)
-->
<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    title="AI 修正 - 选择具体类别"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="!confirming"
    :show-close="!confirming"
  >
    <!-- 1. AI 当前预测信息 -->
    <div class="ai-dialog__ai-info">
      <div class="ai-dialog__section-title">
        <el-icon><InfoFilled /></el-icon>
        <span>AI 当前预测信息</span>
      </div>
      <div class="ai-dialog__ai-body">
        <div v-if="aiTop1Label" class="ai-dialog__ai-top1">
          <span class="ai-dialog__ai-top1-label">类型</span>
          <el-tag type="primary" effect="dark" size="large">
            {{ aiTop1Label }}
          </el-tag>
          <span v-if="aiTop1Confidence != null" class="ai-dialog__ai-top1-conf">
            置信度 <strong>{{ (aiTop1Confidence * 100).toFixed(1) }}%</strong>
          </span>
        </div>
        <div v-else class="ai-dialog__ai-top1 ai-dialog__ai-top1--none">
          <el-tag type="info" effect="plain">无 AI 预测</el-tag>
        </div>
        <!-- 任务类型 + 已存在标注摘要 -->
        <div class="ai-dialog__ai-meta">
          <el-tag size="small" type="info" effect="plain">
            {{ taskTypeLabel }}
          </el-tag>
          <span v-if="existingSummary" class="ai-dialog__ai-existing">
            {{ existingSummary }}
          </span>
        </div>
        <!-- Top-K 候选 (仅分类任务展示) -->
        <div v-if="taskType === 'classification' && aiCandidates && aiCandidates.length > 0" class="ai-dialog__ai-candidates">
          <div class="ai-dialog__candidates-title">AI Top-{{ aiCandidates.length }} 候选</div>
          <div class="ai-dialog__candidates-list">
            <span
              v-for="(c, idx) in aiCandidates" :key="`${c.label}-${idx}`"
              class="ai-dialog__candidate"
              :class="{ 'ai-dialog__candidate--top1': idx === 0 }"
            >
              #{{ idx + 1 }} {{ c.label }}
              <span class="ai-dialog__candidate-conf">{{ (c.confidence * 100).toFixed(1) }}%</span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 2. 选择具体类别 (核心交互) -->
    <el-divider />
    <div class="ai-dialog__select-section">
      <div class="ai-dialog__section-title">
        <el-icon><Select /></el-icon>
        <span>选择具体类别进行确认修正</span>
      </div>
      <el-select
        v-model="selectedCategoryId"
        :placeholder="categories.length === 0 ? '该数据集尚未配置类别' : '请选择类别'"
        :disabled="categories.length === 0 || confirming"
        filterable
        style="width: 100%; margin-top: 8px;"
      >
        <el-option
          v-for="c in sortedCategories" :key="c.id"
          :label="c.name" :value="c.id"
        >
          <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
            <span>{{ c.name }}</span>
            <el-tag
              v-if="c.id === aiTop1CategoryId" size="small" type="success" effect="plain"
              style="margin-left: 4px;"
            >AI 推荐</el-tag>
          </span>
        </el-option>
      </el-select>
      <div class="ai-dialog__hint">
        <el-icon><Warning /></el-icon>
        <span v-if="taskType === 'classification'">
          分类任务: 选中的类别将作为最终标注保存, 状态升级为「已人工确认」
        </span>
        <span v-else-if="taskType === 'detection'">
          检测任务: 选中的类别将作为「主类别」记入审计日志 (实际 bbox 仍按当前画布内容保存)
        </span>
        <span v-else>
          分割任务: 选中的类别将作为最终 mask 类别保存, 状态升级为「已人工确认」
        </span>
      </div>
    </div>

    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px;">
        <el-button :disabled="confirming" @click="emit('update:modelValue', false)">
          取消
        </el-button>
        <el-button
          type="success" :icon="Check"
          :disabled="selectedCategoryId == null || confirming"
          :loading="confirming"
          @click="onConfirm"
        >确认修正</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled, Select, Warning, Check } from '@element-plus/icons-vue'

interface Category { id: number; name: string }
interface Candidate { label: string; confidence: number }

const props = defineProps<{
  modelValue: boolean
  taskType: 'classification' | 'detection' | 'segmentation'
  aiTop1Label: string | null
  aiTop1Confidence: number | null
  aiCandidates: Candidate[]
  categories: Category[]
  defaultCategoryId: number | null
  /** 检测/分割任务: 用户当前已有标注的摘要描述 */
  existingSummary: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 父组件收到 categoryId/labelName 后, 调 submit 走 save 流程 */
  (e: 'confirm', categoryId: number, labelName: string): void
}>()

const PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function catColor(catId: number): string {
  return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
}

const sortedCategories = computed(() => {
  return [...props.categories].sort((a, b) => Number(a.id) - Number(b.id))
})

// AI top1 对应的项目类别 id (可能为 null, 表示 top1 不在项目类目)
const aiTop1CategoryId = computed<number | null>(() => {
  if (!props.aiTop1Label) return null
  const c = sortedCategories.value.find((x) => x.name === props.aiTop1Label)
  return c ? c.id : null
})

const taskTypeLabel = computed(() => {
  if (props.taskType === 'classification') return '分类任务'
  if (props.taskType === 'detection') return '目标检测'
  if (props.taskType === 'segmentation') return '图像分割'
  return ''
})

// 选中的类别 id (本地状态, 弹窗打开时初始化)
const selectedCategoryId = ref<number | null>(null)
const confirming = ref(false)

/**
 * 弹窗打开时初始化选中值
 * - 优先级: 父组件传入的 defaultCategoryId > AI top1 匹配的类别 id
 * - 若都为空, 留空让用户必选
 */
watch(() => props.modelValue, (v) => {
  if (v) {
    confirming.value = false
    selectedCategoryId.value = props.defaultCategoryId ?? aiTop1CategoryId.value ?? null
  }
})

function onConfirm() {
  if (selectedCategoryId.value == null) {
    ElMessage.warning('请先选择具体类别')
    return
  }
  const cat = sortedCategories.value.find((c) => c.id === selectedCategoryId.value)
  if (!cat) {
    ElMessage.warning('所选类别无效')
    return
  }
  emit('confirm', cat.id, cat.name)
}
</script>

<style scoped>
.ai-dialog__ai-info {
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px 14px;
}
.ai-dialog__section-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}
.ai-dialog__ai-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-dialog__ai-top1 {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ai-dialog__ai-top1-label {
  font-size: 12px;
  color: #909399;
  letter-spacing: 0.3px;
}
.ai-dialog__ai-top1-conf {
  font-size: 12px;
  color: #606266;
}
.ai-dialog__ai-top1--none {
  color: #c0c4cc;
}
.ai-dialog__ai-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.ai-dialog__ai-existing {
  font-size: 12px;
  color: #606266;
}
.ai-dialog__candidates-title {
  font-size: 11px;
  color: #909399;
  margin-bottom: 4px;
}
.ai-dialog__candidates-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.ai-dialog__candidate {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 3px;
  background: #ecf5ff;
  color: #409eff;
  border: 1px solid #d9ecff;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ai-dialog__candidate--top1 {
  background: #f0f9eb;
  color: #67c23a;
  border-color: #e1f3d8;
  font-weight: 600;
}
.ai-dialog__candidate-conf {
  color: #909399;
  font-variant-numeric: tabular-nums;
}
.ai-dialog__select-section {
  padding: 0 2px;
}
.ai-dialog__hint {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
.cat-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
</style>
