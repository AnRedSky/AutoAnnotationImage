<!--
  ClassificationAnnotator.vue (v2.5.2 极简版, 对齐 DetectionAnnotator/SegmentationAnnotator)
  =========================================================================================
  图像分类图片查看器 (只负责图片渲染 + 缩放交互, 无任何标注操作)

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
        :style="{
          width: canvasSize.w + 'px',
          height: canvasSize.h + 'px',
          transform: `scale(${zoom})`,
        }"
      >
        <img
          ref="imgEl"
          class="img-layer"
          :src="imageUrl"
          :alt="filename || `image #${imageId}`"
          :width="canvasSize.w"
          :height="canvasSize.h"
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

      <!-- v2.5.2: 画布坐标浮标 (左下角) -->
      <div class="coord-overlay">
        <span v-if="cursorPos">
          x: <b>{{ cursorPos.x.toFixed(0) }}</b>
          &nbsp;y: <b>{{ cursorPos.y.toFixed(0) }}</b> px
        </span>
        <span v-else>移入画布查看坐标</span>
      </div>

      <!-- v2.5.2: 画布尺寸 (右下角, 与检测端同款) -->
      <div class="size-overlay">
        {{ canvasSize.w }} × {{ canvasSize.h }}px · 缩放 {{ scalePercent }}% · 显示 {{ zoomPercent }}%
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
const canvasSize = ref<{ w: number; h: number }>({ w: 0, h: 0 })
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
function onImgMouseMove(e: MouseEvent) {
  if (!imgEl.value) return
  const rect = imgEl.value.getBoundingClientRect()
  // 视觉坐标 (考虑缩放后实际像素位置)
  const x = (e.clientX - rect.left) * (imgEl.value.naturalWidth / rect.width)
  const y = (e.clientY - rect.top) * (imgEl.value.naturalHeight / rect.height)
  cursorPos.value = { x, y }
}
function onImgMouseLeave() {
  cursorPos.value = null
}

// v2.5.2: 图片加载
function onImgLoad() {
  imgLoaded.value = true
  // 根据 maxW=600 自适应缩放 (与 SegmentationAnnotator 同款, 避免图片过大)
  const maxW = 600
  const iw = props.imageWidth || imgEl.value?.naturalWidth || 0
  const ih = props.imageHeight || imgEl.value?.naturalHeight || 0
  if (!iw || !ih) return
  const scale = Math.min(1, maxW / iw)
  canvasSize.value = {
    w: Math.round(iw * scale),
    h: Math.round(ih * scale),
  }
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
  // 初始 canvasSize (等 onImgLoad 会覆盖)
  const iw = props.imageWidth || 0
  const ih = props.imageHeight || 0
  if (iw && ih) {
    const maxW = 600
    const scale = Math.min(1, maxW / iw)
    canvasSize.value = { w: Math.round(iw * scale), h: Math.round(ih * scale) }
  }
})
onBeforeUnmount(() => {
  // 无需清理全局事件
})
</script>

<style scoped>
/* v2.5.2: 极简版, 与 DetectionAnnotator/SegmentationAnnotator 风格严格对齐
   - .cls-annotator: 顶层容器
   - .canvas-wrap:  滚动容器 (overflow: auto), 承载所有 overlay
   - .canvas-stage: 缩放层 (CSS transform: scale)
   - .img-layer:    实际的 <img> 元素
*/
.cls-annotator {
  width: 100%;
  display: flex;
  justify-content: center;
}
.canvas-wrap {
  position: relative;
  width: 100%;
  max-height: 520px;
  overflow: auto;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 8px;
  /* 居中显示 */
  display: flex;
  align-items: center;
  justify-content: center;
}
.canvas-stage {
  position: relative;
  /* 缩放通过 transform: scale(${zoom}) */
  transform-origin: top left;
  /* flex 居中失效: transform 会脱离文档流, 这里改用 display: inline-block */
  flex-shrink: 0;
}
.img-layer {
  display: block;
  user-select: none;
  -webkit-user-drag: none;
  background: #fff;
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

/* v2.5.2: overlay 共用样式 (与 DetectionAnnotator 严格一致) */
.zoom-overlay {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 5;
}
.coord-overlay {
  position: absolute;
  left: 12px;
  bottom: 12px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  z-index: 5;
  pointer-events: none;
}
.coord-overlay b {
  color: #67c23a;
  font-weight: 600;
}
.size-overlay {
  position: absolute;
  right: 12px;
  bottom: 12px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  z-index: 5;
  pointer-events: none;
}
.mode-overlay {
  position: absolute;
  top: 12px;
  left: 12px;
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
