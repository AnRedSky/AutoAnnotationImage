<!--
  ClassificationAnnotator.vue (v2.5.8 固定画布 + 边界检测)
  =========================================================================================
  图像分类图片查看器 (只负责图片渲染 + 缩放交互, 无任何标注操作)

  v2.5.8 关键变更 (画布布局优化):
  - 画布尺寸固定为 600×400 (CANVAS_W × CANVAS_H), 不再随图片内容调整
  - 外层 wrap 固定 600×480 (WRAP_W × WRAP_H), 上下各 40px 安全区
  - 图片通过 object-fit: contain 保持长宽比居中渲染 (letterbox)
  - 顶部 40px 安全区: 缩放控制条 / 模式徽章
  - 底部 40px 安全区: 坐标浮标 / 画布尺寸
  - 文案/overlay 元素永远落在预设安全区内, 不与核心图片区域重叠

  v2.5.2 新增组件:
  - 原因: 之前分类任务直接用 <img style="max-width:100%">, 没有缩放功能
         导致用户在标注工作台只能看缩小版图片, 无法看清细节
  - 目标: 统一三种任务类型的图片展示/缩放交互, 体验与目标检测保持一致

  功能 (v2.5.2):
  - 加载原图到 <img> + <canvas> 双层 (canvas 用于绘制加载/缩放状态)
  - 鼠标滚轮缩放 (无需 Ctrl, 与 DetectionAnnotator 一致)
  - 缩放按钮 (放大/缩小/重置), 与 DetectionAnnotator 同款 el-button-group
  - 缩放比例显示 (zoomPercent + scalePercent, 顶部按钮 + 右下角 overlay)
  - 画布坐标浮标 (左下角) - 鼠标位置 (x, y) 实时显示
  - 画布尺寸 (右下角) - 实际尺寸 + 缩放比例 + 显示缩放比例
  - 模式徽章 (左上角) - 「分类查看模式」

  设计原则 (与 DetectionAnnotator 严格对齐):
  - MIN_ZOOM = 0.25, MAX_ZOOM = 8.0, 重置 = 1.0
  - 滚轮缩放系数 1.15, 按钮缩放系数 1.25
  - CSS transform: scale(${zoom}) 实现缩放
  - 零业务耦合: 不直接调 API, 不引 store
  - 单向数据流: 父组件通过 props 传 imageUrl, 子组件无 emit
  - 模板结构对齐: .cls-annotator > .canvas-wrap > .canvas-stage
  - 画布尺寸/安全区常量从 utils/canvasLayout 统一导入

  Props:
    imageUrl:    原图 URL (必填)
    imageId:     当前图 id (用于 overlay 显示)
    imageWidth:  原图实际宽
    imageHeight: 原图实际高
    filename:    文件名 (overlay 可选显示)
-->
<template>
  <div class="cls-annotator">
    <!-- 画布区域 (只渲染, 不带任何操作 UI, 与 DetectionAnnotator 风格一致) -->
    <div ref="wrapRef" class="canvas-wrap" @wheel.prevent="onWheel">
      <div
        class="canvas-stage"
        :style="{ transform: `scale(${zoom})` }"
      >
        <img
          ref="imgEl"
          class="img-layer"
          :src="imageUrl"
          :alt="filename || `image #${imageId}`"
          @load="onImgLoad"
          @error="onImgError"
          @mousemove="onImgMouseMove"
          @mouseleave="onImgMouseLeave"
        />
        <!-- 加载占位 (v2.5.2: 与检测端同款 loading overlay) -->
        <div v-if="!imgLoaded" class="img-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>图片加载中…</span>
        </div>
      </div>

      <!-- v2.5.2: 缩放控制条 (顶部中间, 与 DetectionAnnotator 同款) -->
      <div class="zoom-overlay">
        <el-button-group size="small">
          <el-button @click="zoomOut" :icon="ZoomOut" circle />
          <el-button @click="zoomReset" plain style="min-width: 64px;">{{ zoomPercent }}%</el-button>
          <el-button @click="zoomIn" :icon="ZoomIn" circle />
        </el-button-group>
      </div>

      <!-- v2.5.10: 整合信息面板 (右上角) — 画布坐标 + 画布尺寸
           与检测端/分割端风格统一 -->
      <div class="info-panel">
        <div class="info-row coord-row">
          <span v-if="cursorPos" class="coord-text">
            x: <b>{{ cursorPos.x.toFixed(0) }}</b>
            <span class="info-sep">·</span>
            y: <b>{{ cursorPos.y.toFixed(0) }}</b>
            <span class="coord-unit">px</span>
          </span>
          <span v-else class="coord-placeholder">移入画布查看坐标</span>
        </div>
        <div class="info-row size-row">
          <span><b>{{ canvasSize.w }}</b>×<b>{{ canvasSize.h }}</b>px</span>
          <span class="info-sep">·</span>
          <span>缩放 {{ scalePercent }}%</span>
          <span class="info-sep">·</span>
          <span>显示 {{ zoomPercent }}%</span>
        </div>
      </div>

      <!-- v2.5.2: 模式徽章 (左上角) -->
      <div class="mode-overlay mode-classification">
        分类查看模式
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ZoomIn, ZoomOut, Loading } from '@element-plus/icons-vue'
// v2.5.10: 响应式画布尺寸 (跟随 wrap 容器变化, 与检测/分割端共享)
import { useCanvasSize } from '@/utils/canvasLayout'

// ============== Props ==============
const props = defineProps<{
  imageUrl: string
  imageId: number
  imageWidth: number
  imageHeight: number
  filename?: string
}>()

// ============== State ==============
const imgEl = ref<HTMLImageElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
// v2.5.10: 画布尺寸响应式 — 由 useCanvasSize 监听 wrap 容器变化
// - 容器尺寸变化时自动同步
// - 图片通过 CSS object-fit: contain 保持长宽比, 自动 letterbox 居中
const { size: canvasSize } = useCanvasSize({ containerRef: wrapRef })
const imgLoaded = ref(false)
const cursorPos = ref<{ x: number; y: number } | null>(null)

// v2.5.2: 缩放状态 (与 DetectionAnnotator 严格一致)
const zoom = ref(1.0)
const MIN_ZOOM = 0.25
const MAX_ZOOM = 8.0
const zoomPercent = computed(() => Math.round(zoom.value * 100))
const scalePercent = computed(() => {
  const dw = props.imageWidth || 0
  if (!dw || !canvasSize.value.w) return '100'
  return ((canvasSize.value.w / dw) * 100).toFixed(0)
})

// v2.5.2: 滚轮缩放 (与 DetectionAnnotator 完全一致, 不需要 Ctrl)
function onWheel(e: WheelEvent) {
  e.preventDefault()
  const rect = wrapRef.value?.getBoundingClientRect()
  if (!rect) return
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  // 缩放: deltaY < 0 放大 (与检测端同款 1.15 系数)
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
  const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom.value * factor))
  if (newZoom === zoom.value) return
  zoom.value = newZoom
  void cx; void cy  // 暂未做 pan, wrapRef 已有 overflow: auto
}

// v2.5.2: 缩放按钮 (与 DetectionAnnotator 同款 1.25 系数)
function zoomIn()  { zoom.value = Math.min(MAX_ZOOM, zoom.value * 1.25) }
function zoomOut() { zoom.value = Math.max(MIN_ZOOM, zoom.value / 1.25) }
function zoomReset() { zoom.value = 1.0 }

// v2.5.2: 画布坐标浮标 (鼠标位置)
// v2.5.8: 鼠标坐标 (考虑 object-fit: contain 的 letterbox 区域)
// - 图片按原始长宽比在 600x400 画布内 letterbox 居中
// - 计算鼠标位置在"图片实际显示区域"内的坐标
function onImgMouseMove(e: MouseEvent) {
  if (!imgEl.value) return
  const rect = imgEl.value.getBoundingClientRect()
  // v2.5.8: 鼠标相对于 img 元素的视觉位置
  const visX = e.clientX - rect.left
  const visY = e.clientY - rect.top
  // 转换为图片实际显示区域内的比例 (考虑 letterbox 黑边)
  // img 元素本身 width/height = 600/400, 但 object-fit: contain 让图片保持比例居中
  // 实际渲染的图片子区域:
  //   imgRatio = canvasW/canvasH = 600/400 = 1.5
  //   imgElRatio = rect.width/rect.height (考虑 transform: scale(zoom))
  // 这里简单做法: 按 rect 内坐标比例 (0~1) 映射回图片原始尺寸
  if (rect.width === 0 || rect.height === 0) return
  const xRatio = visX / rect.width
  const yRatio = visY / rect.height
  const iw = props.imageWidth || imgEl.value.naturalWidth || 0
  const ih = props.imageHeight || imgEl.value.naturalHeight || 0
  if (!iw || !ih) return
  cursorPos.value = { x: Math.round(xRatio * iw), y: Math.round(yRatio * ih) }
}
function onImgMouseLeave() {
  cursorPos.value = null
}

// v2.5.10: 图片加载 (画布尺寸由 useCanvasSize 响应式管理, 不再手动设置)
function onImgLoad() {
  imgLoaded.value = true
}
function onImgError() {
  imgLoaded.value = false
  ElMessage.error('图片加载失败')
}

// v2.5.2: 监听 imageUrl 变化, 重新加载
watch(
  () => props.imageUrl,
  () => {
    imgLoaded.value = false
    zoom.value = 1.0
    cursorPos.value = null
  }
)

onMounted(() => {
  // v2.5.10: 画布尺寸由 useCanvasSize 自动管理, 无需手动设置
})
onBeforeUnmount(() => {
  // 无需清理全局事件
})
</script>

<style scoped>
/* v2.5.2 + v2.5.10: 极简版, 与 DetectionAnnotator/SegmentationAnnotator 风格严格对齐
   - .cls-annotator: 顶层容器
   - .canvas-wrap:  响应式容器 (100% 填充父容器), 承载所有 overlay
   - .canvas-stage: 100% 填充 wrap, 缩放层 (CSS transform: scale)
   - .img-layer:    实际的 <img> 元素, object-fit: contain 自动 letterbox 居中
*/
.cls-annotator {
  width: 100%;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
/* v2.5.10: wrap 容器响应式填充父容器
   v2.5.12: 移除 min-height: 480px
   · 父级 annotate-main-row 高度已锁, 此处不能再设 min-height
   · height: 100% + flex: 1 1 auto + min-height: 0 让 wrap 正确填充并允许内部滚动 */
.canvas-wrap {
  position: relative;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: auto;
  width: 100%;
  flex: 1 1 auto;
  min-height: 0;
}
/* v2.5.10: canvas-stage 完全填充 wrap (1:1 同步) */
.canvas-stage {
  position: relative;
  transform-origin: top left;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
/* v2.5.10: img-layer 用 object-fit: contain 自动 letterbox 居中 */
.img-layer {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
  -webkit-user-drag: none;
  background: #f5f5f5;
}
.img-loading {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.85);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #909399;
  font-size: 13px;
  pointer-events: none;
}

/* v2.5.10: overlay 共用样式
   - 顶部 8px 区域: 缩放控制条 (中间) + 模式徽章 (左) + 信息面板 (右)
   - 不再使用底部安全区, 画布完全填充 wrap */
.zoom-overlay {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  padding: 2px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
}

/* v2.5.10: 整合信息面板 (右上角) — 与检测/分割端风格完全一致 */
.info-panel {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 10;
  background: rgba(0, 0, 0, 0.62);
  color: #fff;
  padding: 5px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  pointer-events: none;
  line-height: 1.5;
  display: flex;
  flex-direction: column;
  gap: 1px;
  text-align: right;
  max-width: calc(100% - 16px);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}
.info-panel .info-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  white-space: nowrap;
}
.info-panel .info-row.coord-row {
  font-weight: 500;
}
.info-panel .info-row.size-row {
  opacity: 0.85;
  font-size: 10.5px;
}
.info-panel b {
  color: #67c23a;
  font-weight: 600;
  margin: 0 1px;
}
.info-panel .coord-unit {
  color: #c0c4cc;
  font-size: 10px;
  margin-left: 2px;
}
.info-panel .coord-placeholder {
  color: #c0c4cc;
  font-style: italic;
  font-size: 10.5px;
}
.info-panel .info-sep {
  color: #6b7280;
  margin: 0 1px;
  user-select: none;
}

.mode-overlay {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  z-index: 5;
  pointer-events: none;
  background: rgba(64, 158, 255, 0.85);
  color: #fff;
}
.mode-overlay.mode-classification {
  background: rgba(64, 158, 255, 0.85);
}
</style>
