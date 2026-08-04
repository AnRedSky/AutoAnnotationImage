<!--
  SegmentationPanel.vue (v2.5.7 拆分自 Annotate.vue, v2.5.11 精简文本, v3.5.0 AI 修正)
  ===================================================
  图像分割任务右侧操作面板 (4 sections, 仅保留操作, + AI 修正区)

  v3.5.0: AI 已标注图片专用「确认修正 / 重新标注」操作区
  - 仅在 image.status === 'ai_labeled' && initialMaskUrl 存在时显示
  - 「确认修正」: 把当前 AI 预测 mask 作为确认结果保存 (走 save 流程, 触发后端 status -> human_confirmed)
  - 「重新标注」: 清空 mask 让用户重画

  设计:
  - 4 sections: 1 工具模式 / 2 类别 / 3 笔刷大小 / 4 图片导航 / 5 提交
  - 已移除: 工具模式下方说明文字、图片导航的 meta 信息、整个 mask 状态 section、未保存提示
    (这些信息在画布底部 meta 条 + 左侧操作指导已有, 重复占用空间)
  - 接收 segAnnotRef (模板 ref, 用 ?.value?.xx 访问内部状态)
  - 通过 emit 抛出操作

  Props (业务通用, 弱耦合):
    segAnnotRef:        ref<any> 子组件模板 ref
    segDirty:           dirty 状态
    annotatorSaving:    保存中 loading
    categories:         类别列表
    segMode:            当前模式 (brush/erase/pan)
    canGoPrev, noMore, historyCursor, historyIds, image
    initialMaskUrl:     已有 mask 的 blob URL (v3.5.0: 用于判断 AI 是否有 mask)

  Emits:
    mode-change, category-change, brush-size-change
    prev, next, view-dataset
    save, cancel
    confirm-correction, re-annotate   // v3.5.0
-->
<template>
  <el-card class="op-card" title="分割操作面板">
    <!-- v3.5.0: AI 已标图片专用「确认修正 / 重新标注」操作区
         - 仅在 image.status === 'ai_labeled' && initialMaskUrl 存在时显示
         - 分割场景: AI 预测 mask 已加载到画布, 用户可一键确认或清空重画 -->
    <div v-if="image && image.status === 'ai_labeled' && initialMaskUrl" class="ai-correction-bar">
      <el-alert
        type="info" :closable="false" show-icon
        title="该图已由 AI 预标注 (mask 已加载), 请选择下一步:"
        style="margin-bottom: 8px;"
      />
      <div style="display: flex; gap: 8px;">
        <el-button
          type="success" size="default" :icon="Check"
          style="flex: 1;"
          :disabled="annotatorSaving"
          @click="emit('confirm-correction')"
        >
          确认修正
        </el-button>
        <el-button
          type="warning" size="default" plain :icon="Refresh"
          style="flex: 1;"
          @click="emit('re-annotate')"
        >
          重新标注
        </el-button>
      </div>
    </div>

    <!-- 1. 工具模式 -->
    <div class="op-section">
      <div class="op-section-title">1. 工具模式</div>
      <el-radio-group :model-value="segMode" size="small" @change="(v: any) => emit('mode-change', v)">
        <el-radio-button value="brush">画刷 (B)</el-radio-button>
        <el-radio-button value="erase">橡皮 (E)</el-radio-button>
        <el-radio-button value="pan">查看 (V)</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 2. 当前画刷类别
         v2.5.13 修复: 移除 ?.value
         · segAnnotRef.brushCategoryId 在 Vue 3 defineExpose 已被自动解包, 不能再加 .value
         · 原写法拿不到值, 下拉只显示占位符 -->
    <div class="op-section" v-if="categories.length">
      <div class="op-section-title">2. 当前画刷类别</div>
      <el-select
        :model-value="segAnnotRef?.brushCategoryId ?? null"
        placeholder="选择类别"
        size="small"
        style="width: 100%; margin-top: 6px;"
        filterable
        @change="(v: number) => emit('category-change', v)"
      >
        <el-option
          v-for="c in categories" :key="c.id"
          :label="c.name" :value="c.id"
        >
          <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
          {{ c.name }}
        </el-option>
      </el-select>
    </div>

    <!-- 3. 当前画刷大小
         v2.5.13: 同样移除 ?.value, 笔刷大小需正确反映 segAnnotRef.brushSize 的当前值 -->
    <div class="op-section">
      <div class="op-section-title">3. 笔刷大小: {{ segAnnotRef?.brushSize ?? 12 }}px</div>
      <el-slider
        :model-value="segAnnotRef?.brushSize ?? 12"
        :min="2" :max="40" :step="1"
        style="margin-top: 6px;"
        @input="(v: number) => emit('brush-size-change', v)"
      />
    </div>

    <!-- 4. 图片导航 -->
    <div class="op-section">
      <div class="op-section-title">4. 图片导航</div>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <el-button
          style="flex: 1;" :icon="ArrowLeft"
          :disabled="!canGoPrev"
          @click="emit('prev')"
        >上一张 (P)</el-button>
        <el-button
          style="flex: 1;"
          :icon="ArrowRight"
          :type="noMore ? 'info' : 'danger'"
          :plain="!noMore"
          :disabled="noMore"
          @click="emit('next')"
        >{{ noMore ? '已是最后一张' : '下一张 (N)' }}</el-button>
      </div>
    </div>

    <!-- 5. 提交 -->
    <div class="op-section">
      <div class="op-section-title">5. 提交</div>
      <div style="display: flex; gap: 8px; margin-top: 6px;">
        <el-button
          type="primary"
          :icon="Check"
          :disabled="!segDirty || annotatorSaving"
          :loading="annotatorSaving"
          style="flex: 1;"
          @click="emit('save')"
        >保存 mask</el-button>
        <el-button
          :icon="Close" style="flex: 1;"
          :disabled="!segDirty || annotatorSaving"
          @click="emit('cancel')"
        >取消</el-button>
      </div>
    </div>

    <!-- 6. 不合格标记 (v3.0.0) -->
    <div class="op-section">
      <div class="op-section-title">6. 不合格标记</div>
      <template v-if="image">
        <template v-if="isUnqualified">
          <div style="display: flex; align-items: center; gap: 6px; margin-top: 6px;">
            <el-tag type="danger" size="small" effect="dark">已标记不合格</el-tag>
            <el-tag type="danger" size="small" effect="plain">
              {{ getRejectReasonLabel(rejectReason) }}
            </el-tag>
          </div>
          <el-button
            size="small" type="warning" :icon="RefreshLeft"
            style="width: 100%; margin-top: 6px;"
            @click="emit('unmark-unqualified')"
          >撤销不合格标记</el-button>
        </template>
        <template v-else>
          <el-select
            v-model="localRejectReason"
            placeholder="选择不合格原因"
            size="small" style="width: 100%; margin-top: 6px;"
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
      </template>
    </div>

    <div class="op-section">
      <el-link type="primary" :icon="View" @click="emit('view-dataset')">
        去数据集详情浏览全部图片
      </el-link>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Check, Close, ArrowLeft, View, RefreshLeft, Warning, ArrowRight, Refresh } from '@element-plus/icons-vue'
import { REJECT_REASON_OPTIONS, getRejectReasonLabel } from '@/utils/rejectReason'

interface Category { id: number; name: string }
interface Image {
  id: number
  filename: string
  width: number
  height: number
  status?: string
}

const props = defineProps<{
  segAnnotRef: any
  segDirty: boolean
  annotatorSaving: boolean
  categories: Category[]
  segMode: 'brush' | 'erase' | 'pan'
  canGoPrev: boolean
  noMore: boolean
  historyCursor: number
  historyIds: number[]
  image: Image | null
  // v3.0.0: 不合格标记状态 (从父组件 image 派生)
  isUnqualified: boolean
  rejectReason: string | null
  // v3.5.0: 已有 mask 的 blob URL, 用于 AI 修正区显示判断
  initialMaskUrl: string | null
}>()

const emit = defineEmits<{
  (e: 'mode-change', m: 'brush' | 'erase' | 'pan'): void
  (e: 'category-change', catId: number): void
  (e: 'brush-size-change', size: number): void
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'view-dataset'): void
  (e: 'save'): void
  (e: 'cancel'): void
  // v3.0.0: 不合格标记事件 (单向数据流, 由父组件处理 API 调用)
  (e: 'mark-unqualified', reason: string, customText: string): void
  (e: 'unmark-unqualified'): void
  // v3.5.0: AI 已标图片「确认修正」/「重新标注」双路径
  (e: 'confirm-correction'): void
  (e: 're-annotate'): void
}>()

// 调色板 (与 SegmentationAnnotator 一致)
const DET_PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function catColor(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return DET_PALETTE[Math.abs(Number(catId)) % DET_PALETTE.length]
}

// ============== v3.0.0: 不合格标记本地状态 ==============
const localRejectReason = ref<string>('')
const localCustomText = ref<string>('')

function handleMarkUnqualified() {
  if (!localRejectReason.value) return
  emit('mark-unqualified', localRejectReason.value, localCustomText.value)
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

.op-section {
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px dashed #ebeef5;
}
.op-section:last-child {
  margin-bottom: 0;
  padding-bottom: 0;
  border-bottom: none;
}
.op-section-title {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  letter-spacing: 0.5px;
  display: flex;
  align-items: center;
}
.op-section-title::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  background: #67c23a;
  margin-right: 6px;
  border-radius: 2px;
}
.cat-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

/* v3.5.0: AI 已标图片「确认修正/重新标注」操作区
   - 与 ClassificationPanel / DetectionPanel 风格保持一致 */
.ai-correction-bar {
  background: #f5f7fa;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  padding: 10px 12px;
  margin: 0 0 12px;
}
.ai-correction-bar__hint {
  font-weight: 400;
  font-size: 12px;
  opacity: 0.9;
  margin-left: 2px;
}
</style>
