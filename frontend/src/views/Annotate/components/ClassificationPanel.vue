<!--
  ClassificationPanel.vue (v2.5.7 拆分自 Annotate.vue, v2.5.11 精简文本)
  ====================================================
  图像分类任务右侧 AI 候选面板 (仅保留操作)

  v2.5.11 精简:
  - 简化 allUnknown 块: 只保留 "未知" 标签, 删除 2 行解释文字
  - 简化未知候选 tag: 只显示 "未知", 删除括号说明
  - 删除 "所有待标注图片已加载完毕" 提示行
    (此提示在 el-empty 已有 "暂无 AI 预测" 提示, 重复占用空间)

  包含: AI Top-5 候选 + 修正下拉 + 上一张/下一张 + 去数据集详情

  Props (业务通用, 弱耦合):
    image:            当前图 (用于 file_url, filename)
    candidates:       AI Top-5 候选
    allUnknown:       全部未知 (基础模型 ImageNet 输出场景)
    categories:       项目类别列表
    sortedCategories: 排序后类别 (按 id 升序)
    canGoPrev, noMore

  Emits:
    submit(categoryId, label, isFromAI): 用户采纳/强制采用某标签
    prev, next, view-dataset
-->
<template>
  <el-card class="op-card" title="AI 候选标签（Top-5）">
    <!-- v3.6.1: 防御性守卫 - 无图时只显示空状态, 不渲染候选区
         - 修复场景: 切到「已人工标注」但无图时, 旧逻辑因 candidates 非空而渲染 Top-K
         - 此时 image=null, candidates 来自上一张缓存, 渲染没意义 -->
    <el-empty v-if="!image" description="当前数据集该状态下没有图片, 请切换其他状态或数据集" :image-size="80" />
    <template v-else>
    <el-empty v-if="candidates.length === 0" description="该图无 AI 预测, 请直接选择其他类别" :image-size="60" />
    <!-- 关键简化: 基础模型 (ImageNet 预训练) 输出 = 全部 Top-5 都不在项目类目
         -> 整组归一为「未知」, 不再分 5 个候选 + 各自置信度 -->
    <div v-else-if="allUnknown" class="model-confidence-bar"
      style="text-align: center; padding: 24px 12px; border: 1px dashed #f56c6c; border-radius: 6px; background: #fef0f0;">
      <el-tag type="danger" size="large" effect="dark">未知</el-tag>
    </div>
    <!-- v3.6.1: AI 已标图片专用提示区
         - 仅在 image.status === 'ai_labeled' 时显示
         - 仅保留文本提示 (展示 AI top1 类别 + 置信度), 不再提供按钮
         - 类别确认由下方候选行的「确认此标签」入口承担
         - 修正入口由「或选择其他类别」下拉承担 -->
    <div v-if="image && image.status === 'ai_labeled' && !allUnknown" class="ai-correction-bar">
      <el-alert
        type="info" :closable="false" show-icon
        :title="aiCorrectionAlertTitle"
      />
    </div>

    <div v-for="(c, idx) in candidates" v-show="!allUnknown" :key="`${c.label}-${idx}`" class="model-confidence-bar">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span>
          <el-tag size="small" type="info">#{{ idx + 1 }}</el-tag>
          <strong style="margin-left: 6px;" :class="{ 'unknown-label': !findCategory(c.label) }">
            {{ findCategory(c.label) ? c.label : '未知' }}
          </strong>
        </span>
        <el-tag :type="c.confidence > 0.8 ? 'success' : c.confidence > 0.5 ? 'warning' : 'info'">
          {{ (c.confidence * 100).toFixed(1) }}%
        </el-tag>
      </div>
      <el-progress :percentage="Math.round(c.confidence * 100)" :show-text="false"
        :color="c.confidence > 0.8 ? '#67c23a' : c.confidence > 0.5 ? '#e6a23c' : '#909399'" />
      <div style="margin-top: 4px;">
        <template v-if="findCategory(c.label)">
          <el-button size="small" type="primary" :icon="Check"
            @click="submitClick(findCategory(c.label)!.id, c.label, true)">
            确认此标签
          </el-button>
        </template>
        <el-tag v-else type="danger" size="small">未知</el-tag>
      </div>
    </div>
    <el-divider v-if="categories.length > 0">或选择其他类别</el-divider>
    <el-select
      v-if="categories.length > 0"
      v-model="otherCategoryId"
      placeholder="选择其他类别（修正）" style="width: 100%;"
      filterable
      @change="onCategoryChange"
    >
      <el-option v-for="c in sortedCategories" :key="c.id" :label="c.name" :value="c.id" />
    </el-select>
    <!-- v3.4.0: AI vs 当前 diff 徽章 + 修正原因 (仅在选择「其他类别」即修正场景下显示) -->
    <div v-if="otherCategoryId && correctionMode" style="margin-top: 8px;">
      <CorrectionDiffBadge
        :ai-top1="aiTop1Label"
        :current-label-name="otherCategoryName"
      />
      <div style="margin-top: 6px;">
        <CorrectionCommentInput v-model="commentText" />
      </div>
    </div>
    <div style="margin-top: 8px; display: flex; gap: 8px;">
      <el-button
        style="flex: 1;"
        :icon="ArrowLeft"
        :disabled="!canGoPrev"
        @click="emit('prev')"
      >上一张</el-button>
      <el-button
        style="flex: 1;"
        :type="noMore ? 'info' : 'danger'"
        :plain="!noMore"
        :icon="ArrowRight"
        :disabled="noMore"
        @click="emit('next')"
      >{{ noMore ? '已是最后一张' : '下一张' }}</el-button>
    </div>
    <!-- v3.0.0: 不合格标记区域 -->
    <el-divider v-if="image">
      <span style="font-size: 12px; color: #f56c6c;">不合格标记</span>
    </el-divider>
    <div v-if="image" style="margin-top: 8px;">
      <template v-if="isUnqualified">
        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 6px;">
          <el-tag type="danger" size="small" effect="dark">已标记不合格</el-tag>
          <el-tag type="danger" size="small" effect="plain">
            {{ getRejectReasonLabel(rejectReason) }}
          </el-tag>
        </div>
        <el-button
          size="small" type="warning" :icon="RefreshLeft"
          style="width: 100%;"
          @click="emit('unmark-unqualified')"
        >撤销不合格标记</el-button>
      </template>
      <template v-else>
        <el-select
          v-model="localRejectReason"
          placeholder="选择不合格原因"
          size="small" style="width: 100%;"
        >
          <el-option
            v-for="opt in REJECT_REASON_OPTIONS" :key="opt.value"
            :label="opt.label" :value="opt.value"
          />
        </el-select>
        <el-input
          v-if="localRejectReason === 'other'"
          v-model="localCustomText"
          placeholder="请输入具体原因"
          size="small" style="width: 100%; margin-top: 6px;"
        />
        <el-button
          size="small" type="danger" :icon="Warning"
          :disabled="!localRejectReason"
          style="width: 100%; margin-top: 6px;"
          @click="handleMarkUnqualified"
        >标记为不合格</el-button>
      </template>
    </div>
    <div v-if="image" style="margin-top: 8px; text-align: center;">
      <el-link type="primary" :icon="View" @click="emit('view-dataset')">
        去数据集详情浏览全部图片
      </el-link>
    </div>

    <!-- v3.6.1: 顶部仅保留 AI 预标注类型提示, 不再渲染弹窗
         - 类别确认由下方候选行「确认此标签」承担
         - 修正入口由「或选择其他类别」下拉承担 -->
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { Check, ArrowLeft, View, RefreshLeft, Warning, ArrowRight } from '@element-plus/icons-vue'
import { REJECT_REASON_OPTIONS, getRejectReasonLabel } from '@/utils/rejectReason'
// v3.4.0: 人工修正方案 - 复用 common/annotation-business 公共组件
import CorrectionDiffBadge from '@/components/common/CorrectionDiffBadge.vue'
import CorrectionCommentInput from '@/components/annotation-business/CorrectionCommentInput.vue'

interface Category { id: number; name: string }
interface Candidate { label: string; confidence: number }
interface Image { id: number; filename: string; status?: string }

const props = defineProps<{
  image: Image | null
  candidates: Candidate[]
  allUnknown: boolean
  categories: Category[]
  sortedCategories: Category[]
  canGoPrev: boolean
  noMore: boolean
  // v3.0.0: 不合格标记状态 (从父组件 image 派生)
  isUnqualified: boolean
  rejectReason: string | null
}>()

const emit = defineEmits<{
  // v3.4.0: 扩展 submit 事件, 透传 comment (供后端 payload.comment 写入)
  (e: 'submit', categoryId: number, label: string, isFromAI: boolean, comment?: string): void
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'view-dataset'): void
  // v3.0.0: 不合格标记事件 (单向数据流, 由父组件处理 API 调用)
  (e: 'mark-unqualified', reason: string, customText: string): void
  (e: 'unmark-unqualified'): void
}>()

function findCategory(label: string): Category | undefined {
  // 注意: Category 接口只有 id/name, 没有 label 字段
  return props.categories.find((c) => c.name === label)
}

function submitClick(categoryId: number, label: string, isFromAI: boolean) {
  // AI 候选的"确认" / "强制采用"按钮, 不带 comment
  emit('submit', categoryId, label, isFromAI, undefined)
}

// ============== v3.4.0: 「其他类别」修正模式 ==============
// 用户从下拉选非 AI top1 类别时, 弹 diff 徽章 + 修正原因输入
const otherCategoryId = ref<number | null>(null)
const commentText = ref<string>('')
const correctionMode = computed(() => !!otherCategoryId.value)
const aiTop1Label = computed(() => {
  // candidates 里第一个 label 即 AI top1
  return props.candidates?.[0]?.label || null
})
// v3.6.0: AI top1 置信度 (单独 computed, 供弹窗显示)
const aiTop1Confidence = computed(() => {
  return props.candidates?.[0]?.confidence ?? null
})
// v3.6.0: AI top1 在项目类目里的 id (可能为 null, 表示 top1 不在项目类目)
const aiTop1CategoryId = computed<number | null>(() => {
  if (!aiTop1Label.value) return null
  const c = props.categories.find((x) => x.name === aiTop1Label.value)
  return c ? c.id : null
})
const otherCategoryName = computed(() => {
  const c = props.categories.find((x) => x.id === otherCategoryId.value)
  return c?.name || null
})

function onCategoryChange(id: number) {
  const cat = props.categories.find((c) => c.id === id)
  if (cat) {
    // v3.4.0: 修正模式, 把 comment 一并透传给父
    emit('submit', cat.id, cat.name, false, commentText.value || undefined)
    // 提交后清空本地状态
    otherCategoryId.value = null
    commentText.value = ''
  }
}

// ============== v3.6.1: AI 已标图片顶部提示 ==============
// 仅显示当前 AI 预标注的 top1 类别 + 置信度
// 类别确认由下方候选行的「确认此标签」承担
// 修正入口由「或选择其他类别」下拉承担
const aiCorrectionAlertTitle = computed(() => {
  if (aiTop1Label.value) {
    const confStr = aiTop1Confidence.value != null
      ? ` ${(aiTop1Confidence.value * 100).toFixed(1)}%`
      : ''
    return `当前 AI 预标注: ${aiTop1Label.value}${confStr}`
  }
  return '当前图已由 AI 预标注'
})

// ============== v3.0.0: 不合格标记本地状态 ==============
const localRejectReason = ref<string>('')
const localCustomText = ref<string>('')

function handleMarkUnqualified() {
  if (!localRejectReason.value) return
  emit('mark-unqualified', localRejectReason.value, localCustomText.value)
  // 提交后清空本地状态
  localRejectReason.value = ''
  localCustomText.value = ''
}

// 切换图片时清空本地状态 (避免上一张的选择残留)
watch(() => props.image?.id, () => {
  localRejectReason.value = ''
  localCustomText.value = ''
})
</script>

<style scoped>
/* 跟随父 el-col 高度, 与左侧侧栏/中间画布三列同高
   - el-card 本体 100% 填充 el-col
   - body 内部 flex 1 + auto overflow, 内容过长时本卡片内部滚动 */
.op-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.op-card :deep(.el-card__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.model-confidence-bar {
  padding: 8px 0;
  border-bottom: 1px dashed #ebeef5;
}
.model-confidence-bar:last-of-type {
  border-bottom: none;
}
.unknown-label {
  color: #f56c6c;
}

/* v3.6.1: AI 已标图片顶部提示区
   - 仅显示 AI 预标注的 top1 类别 + 置信度, 不再提供按钮
   - 浅灰背景与下方候选列表做视觉分层 */
.ai-correction-bar {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 0;
  margin: 0 -8px 8px;
}
</style>
