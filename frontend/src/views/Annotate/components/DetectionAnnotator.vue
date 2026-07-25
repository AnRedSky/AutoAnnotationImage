<!--
  DetectionAnnotator.vue (v3.0.0 Phase M 重构为编排层)
  =================================================
  目标检测 bbox 画布组件 (只负责编排, 不含复杂业务)

  v3.0.0 Phase M 重构:
  - 缩放状态 + 滚轮       → composables/useDetectionZoom
  - 撤销/重做/dirty/reset → composables/useDetectionHistory
  - bbox 交互 (画/拖/选/缩放) → composables/useDetectionBBoxInteraction
  - 键盘快捷键             → composables/useDetectionKeyboard
  - canvas 渲染 + 图片加载 → composables/useDetectionDraw
  - X 按钮位置计算         → composables/useDetectionXBtnPos
  - 缩放控制条 UI          → components/DetectionZoomBar
  - 信息面板 UI            → components/DetectionInfoPanel
  - 模式徽章 UI            → components/DetectionModeBadge
  - X 删除按钮 UI          → components/DetectionDeleteOverlay

  本组件只保留:
  - 编排各 composables (串联数据/操作)
  - 模板: canvas 元素 + 4 个 UI overlay
  - 事件转发: 子组件 emit -> 父组件方法
  - 暴露给父组件: undo/redo/save/selectByIndex/setDefaultCategory/removeAt 等

  设计原则:
  - 零业务耦合: 不直接调 API, 不引 store
  - 坐标存储: 归一化 (0-1), 与后端 BBoxAnnotation 一致
  - 渲染坐标系: 实际像素 (固定 canvas size), 由 useDetectionDraw 内部转换
  - 单向数据流: 画布尺寸/安全区常量从 utils/canvasLayout 统一导入
-->
<template>
  <div class="det-annotator">
    <!-- 画布区域 (只渲染, 不带任何操作 UI) -->
    <div ref="wrapRef" class="canvas-wrap" @wheel.prevent="onWheel">
      <div class="canvas-stage" :style="{ transform: `scale(${zoom})` }">
        <canvas
          ref="canvasRef"
          class="canvas"
          :width="canvasSize.w"
          :height="canvasSize.h"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
          @mouseup="onMouseUp"
          @mouseleave="onMouseUp"
        />
      </div>

      <!-- 缩放控制条 (顶部中间) -->
      <DetectionZoomBar
        :zoom-percent="zoomPercent"
        @zoom-in="zoomIn"
        @zoom-out="zoomOut"
        @zoom-reset="zoomReset"
      />

      <!-- 信息面板 (右上角): 坐标 + 尺寸 -->
      <DetectionInfoPanel
        :cursor-pos="cursorPos"
        :canvas-w="canvasSize.w"
        :canvas-h="canvasSize.h"
        :scale-percent="scalePercent"
        :zoom-percent="zoomPercent"
      />

      <!-- 智能标注模式徽章 (左上角) -->
      <DetectionModeBadge />

      <!-- bbox 标签右上角 X 删除按钮 (DOM overlay) -->
      <DetectionDeleteOverlay
        :position="xBtnPos"
        :title="`删除 #${(selectedIndex ?? 0) + 1}「${selectedBBoxLabel}」`"
        @delete="confirmAndRemove(selectedIndex ?? 0)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useCanvasSize } from '@/utils/canvasLayout'

// ============== Composables (v3.0.0 Phase M 拆分) ==============
import { useDetectionZoom } from '@/composables/useDetectionZoom'
import { useDetectionHistory } from '@/composables/useDetectionHistory'
import { useDetectionBBoxInteraction } from '@/composables/useDetectionBBoxInteraction'
import { useDetectionDraw } from '@/composables/useDetectionDraw'
import { useDetectionXBtnPos } from '@/composables/useDetectionXBtnPos'
import { useDetectionKeyboard } from '@/composables/useDetectionKeyboard'
import type { BBox, Category } from '@/composables/useDetectionTypes'

// ============== 子组件 (v3.0.0 Phase M 抽离) ==============
import DetectionZoomBar from './DetectionZoomBar.vue'
import DetectionInfoPanel from './DetectionInfoPanel.vue'
import DetectionModeBadge from './DetectionModeBadge.vue'
import DetectionDeleteOverlay from './DetectionDeleteOverlay.vue'

// ============== Props / Emits ==============
const props = defineProps<{
  imageUrl: string
  imageId: number
  imageWidth: number
  imageHeight: number
  categories: Category[]
  modelValue: BBox[]
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: BBox[]): void
  (e: 'save', bboxes: BBox[]): void
  (e: 'cancel'): void
  (e: 'next'): void
  (e: 'prev'): void
  // dirty 状态变化通知 (父组件保存按钮 disabled 用)
  (e: 'dirty-change', dirty: boolean): void
}>()

// ============== Refs (v-model + canvas/wrap) ==============
// modelValue 双向绑定 (转 ref 方便 composables 内部使用)
const modelValue = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
// categories 转为 ref 供 composable 使用
const categoriesRef = computed(() => props.categories)
// imageUrl 转为 ref
const imageUrlRef = computed(() => props.imageUrl)

const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)

// ============== 画布尺寸 (useCanvasSize 响应式) ==============
const { size: canvasSize } = useCanvasSize({ containerRef: wrapRef })

// ============== 缩放 (useDetectionZoom) ==============
const { zoom, zoomPercent, onWheel, zoomIn, zoomOut, zoomReset } = useDetectionZoom({
  wrapRef,
})

// 缩放比 (imageWidth -> canvasW 的比例, 百分比)
const scalePercent = computed(() => {
  const dw = props.imageWidth || 0
  if (!dw || !canvasSize.value.w) return '100'
  return ((canvasSize.value.w / dw) * 100).toFixed(0)
})

// ============== 历史管理 (useDetectionHistory) ==============
const historyApi = useDetectionHistory({
  modelValue,
  resetOnChange: imageUrlRef,  // 切图时自动重置
  onDirtyChange: (v) => emit('dirty-change', v),
  immediate: true,
})

// ============== bbox 交互 (useDetectionBBoxInteraction) ==============
const bboxApi = useDetectionBBoxInteraction({
  canvasRef,
  canvasSize,
  modelValue,
  categories: categoriesRef,
  requestRedraw: () => draw(),  // 循环引用, 在下方定义
  snapshot: historyApi.snapshot,
  undo: historyApi.undo,
  redo: historyApi.redo,
  dirty: historyApi.dirty,
  resetInitial: historyApi.resetInitial,
})

const {
  selectedIndex, defaultCategoryId,
  drawing, hoverHandle,
  cursorPos,
  selectedCategoryId, selectedBBoxLabel,
  colorOf, catName,
  ensureDefaultCategory, setDefaultCategory,
  getHandles, hitTestHandle,
  selectByIndex, removeAt, removeSelected,
  changeSelectedCategory, clearAllWithConfirm, undoAll,
  onMouseDown, onMouseMove, onMouseUp,
  doUndo, doRedo,
  confirmAndRemove,
} = bboxApi

// ============== 渲染 (useDetectionDraw) ==============
const { draw } = useDetectionDraw({
  canvasRef,
  canvasSize,
  imageUrl: imageUrlRef,
  modelValue,
  selectedIndex,
  hoverHandle,
  drawing,
  colorOf, catName, getHandles,
})

// ============== X 按钮位置 (useDetectionXBtnPos) ==============
const { xBtnPos } = useDetectionXBtnPos({
  canvasRef, wrapRef, canvasSize, zoom,
  selectedIndex, modelValue,
})

// ============== 键盘快捷键 (useDetectionKeyboard) ==============
useDetectionKeyboard({
  onUndo: doUndo,
  onRedo: doRedo,
  onRemoveSelected: removeSelected,
  hasSelection: () => selectedIndex.value !== null,
  onNext: () => emit('next'),
  onPrev: () => emit('prev'),
})

// ============== 监听 categories 变化 (设默认类别) ==============
watch(
  categoriesRef,
  () => ensureDefaultCategory(),
  { immediate: true }
)

// ============== 切图时重置选中 (左侧「去标注」跳过来时) ==============
watch(imageUrlRef, (newUrl, oldUrl) => {
  if (newUrl && newUrl !== oldUrl) {
    selectedIndex.value = null
    // 历史管理会自动重置 (由 resetOnChange 配置)
  }
})

// ============== 保存 (暴露给父组件) ==============
/**
 * v2.5.38 修复: 允许保存空列表 (用于「清空全部」后保存以删除后端标注)
 * - 之前: props.modelValue.length === 0 时直接 ElMessage.warning 拦截
 * - 现在: 走标准 save 流程, 父组件的 saveDetectionBBoxes 会先调 clearBBoxes
 *         (DELETE /api/detection/annotations/clear/{imageId}) 删后端全部 bbox,
 *         再用空列表循环 0 次, 效果等于删除全部已保存标注
 */
function onSave() {
  // 重置 dirty (历史管理内部清空 undo/redo 栈)
  historyApi.resetInitial()
  emit('save', props.modelValue || [])
}

// ============== 当前选中 bbox (expose 出去供父组件算 overlay 位置) ==============
const selectedBBox = computed(() => {
  const i = selectedIndex.value
  if (i === null) return null
  return props.modelValue?.[i] || null
})

// ============== Expose (v3.0.0 Phase M 全部暴露, 与拆分前兼容) ==============
defineExpose({
  // 历史
  undo: doUndo,
  redo: doRedo,
  undoAll,
  canUndo: historyApi.canUndo,
  canRedo: historyApi.canRedo,
  resetInitial: historyApi.resetInitial,
  dirty: historyApi.dirty,
  // bbox 操作
  removeSelected,
  removeAt,
  removeAtWithConfirm: confirmAndRemove,
  removeBBoxAt: removeAt,
  confirmAndRemove,
  changeSelectedCategory,
  selectByIndex,
  setDefaultCategory,
  // 状态
  selectedIndex,
  selectedCategoryId,
  selectedBBoxLabel,
  selectedBBox,
  defaultCategoryId,
  categories: categoriesRef,
  // 工具
  colorOf,
  catName,
  // 缩放
  zoomIn,
  zoomOut,
  zoomReset,
  zoomPercent,
  // 清空
  clearAllWithConfirm,
  // 保存
  save: onSave,
})
</script>

<style scoped>
.det-annotator {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  /* v2.5.10: 父容器由 flex 主导, 画布可按比例自适应 */
  flex: 1 1 auto;
  min-height: 0;
}
/* v2.5.10: wrap 容器响应式填充父容器 — 不再写死 600×480
   - 父容器 (AnnotationCanvas 内的 .annotate-canvas) 提供宽高
   - wrap 用 100% × 100% 填满父容器
   v2.5.12: 移除 min-height: 480px
   · 父级 annotate-main-row 高度已锁 (calc(100vh - 360px))
   · 此处不能再设 min-height, 否则突破父级固定高度, 撑大整行
   · 画布实际像素由 useCanvasSize 监听父容器宽度计算
   · 极小容器场景由父级 min-height: 500px 兜底, 此处无需再设
   - overflow: auto 允许缩放后滚动查看 */
.canvas-wrap {
  position: relative;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: auto;
  width: 100%;
  flex: 1 1 auto;
  min-height: 0;
  /* 父容器可能给到 0, 这里保证有合理的最小高度 */
}
/* v2.5.10: canvas-stage 完全填充 wrap (无 flex, 1:1 同步)
   - width/height 100% 保证画布与 wrap 边界无缝贴合
   - transform-origin: top left 让缩放从左上角展开 */
.canvas-stage {
  position: relative;
  transform-origin: top left;
  width: 100%;
  height: 100%;
  /* 关键: stage 高度与 wrap 一致, 画布内的 letterbox 由 canvas 自身处理 */
}
.canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: crosshair;
  user-select: none;
}
</style>
