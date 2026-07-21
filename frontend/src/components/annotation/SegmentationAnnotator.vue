<!--
  SegmentationAnnotator.vue
  =========================
  图像分割 mask 画布组件 (v2.1.0 → v2.5.0)

  职责:
  - 加载原图 + 已存在的 mask (来自后端 /api/segmentation/masks/{image_id})
  - 渲染: 原图为底, mask 用半透明彩色叠层 (调色板按 category_id 分配)
  - 画刷模式 (brush): 鼠标按住画当前类别, 释放停止
  - 橡皮模式 (erase): 鼠标按住擦除
  - 平移模式 (pan): 鼠标按住拖动
  - 调色板: 用户在工具栏选当前画刷类别, 颜色从 PALETTE 取
  - 通过 emit('save', maskBlob) 抛出 PNG 文件, 由父组件负责上传到后端

  v2.5.0 增强 (S12.3a):
  - 画布缩放 (滚轮 + 100% 控制条, 0.25x-8x)
  - 画布坐标浮标 (左下: 像素坐标 + 当前类别颜色; 右下: 画布尺寸)
  - 全局快捷键: B=画刷 E=橡皮 V=查看 Space=按住临时平移
  - defineExpose 暴露给父组件 Annotate.vue 右侧 8 sections 面板调用

  Props:
    imageUrl:    原图 URL
    imageId:     当前图 id
    imageWidth:  原图宽
    imageHeight: 原图高
    categories:  [{id, name}] 类别 (用于调色板 + 颜色)
    initialMaskUrl: 已有 mask URL (后端 /api/segmentation/masks/{id}?download=true)

  Emits:
    save(maskFile: File)   用户点保存, 抛出 PNG File 给父组件上传
    cancel()               撤销
    clear()                清空当前画布
-->
<template>
  <div class="seg-annotator">
    <!-- 工具栏 (保留 v2.1 模式切换 + 笔刷大小 + 清空) -->
    <div class="toolbar">
      <el-button-group size="small">
        <el-button :type="mode === 'brush' ? 'primary' : 'default'" @click="mode = 'brush'">
          <el-icon><Brush /></el-icon>画刷 (B)
        </el-button>
        <el-button :type="mode === 'erase' ? 'primary' : 'default'" @click="mode = 'erase'">
          <el-icon><Delete /></el-icon>橡皮 (E)
        </el-button>
        <el-button :type="mode === 'pan' ? 'primary' : 'default'" @click="mode = 'pan'">
          <el-icon><View /></el-icon>查看 (V)
        </el-button>
      </el-button-group>
      <span class="brush-size">
        笔刷:
        <el-slider v-model="brushSize" :min="2" :max="40" :step="1" style="width: 120px;" />
        <span class="size-num">{{ brushSize }}px</span>
      </span>
      <!-- v2.5.0: 缩放控制 (v2.3.2 检测端同款) -->
      <el-button-group size="small">
        <el-button :icon="ZoomOut" @click="zoomOut">缩小</el-button>
        <el-button @click="resetZoom">{{ Math.round(zoom * 100) }}%</el-button>
        <el-button :icon="ZoomIn" @click="zoomIn">放大</el-button>
      </el-button-group>
      <el-button size="small" @click="clearMask" :icon="Refresh">清空</el-button>
    </div>

    <!-- 类别调色板 (画刷 / 橡皮 当前作用的类别) -->
    <div class="palette" v-if="mode === 'brush'">
      <span>当前类别:</span>
      <div
        v-for="c in categories" :key="c.id"
        class="palette-item"
        :class="{ active: brushCategoryId === c.id }"
        :style="{ borderColor: colorOf(c.id) }"
        @click="brushCategoryId = c.id"
      >
        <span class="cat-dot" :style="{ background: colorOf(c.id) }"></span>
        {{ c.name }}
      </div>
    </div>

    <!-- 双 canvas 叠层: 底层原图, 上层 mask 半透明 -->
    <div
      ref="wrapRef"
      class="canvas-wrap"
      @wheel.prevent="onWheel"
    >
      <canvas ref="imgCanvasRef" class="layer" :width="canvasSize.w" :height="canvasSize.h" />
      <canvas
        ref="maskCanvasRef"
        class="layer interactive"
        :width="canvasSize.w" :height="canvasSize.h"
        :style="{ transform: `translate(-50%, -50%) scale(${zoom})` }"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
      />
      <!-- v2.5.0: 画布坐标浮标 -->
      <div class="coord-overlay coord-bl">
        <span v-if="mousePos" class="coord-mouse">
          x={{ mousePos.x }}, y={{ mousePos.y }} px
        </span>
        <span v-else class="coord-mouse idle">—</span>
        <span v-if="brushCategoryId != null" class="coord-cat" :style="{ color: colorOf(brushCategoryId) }">
          ● {{ catName(brushCategoryId) }}
        </span>
      </div>
      <div class="coord-overlay coord-br">
        画布 {{ canvasSize.w }}×{{ canvasSize.h }}px · 缩放 {{ Math.round(zoom * 100) }}%
      </div>
      <div class="coord-overlay coord-tl">
        <el-tag size="small" :type="modeTagType">{{ modeLabel }}</el-tag>
      </div>
    </div>

    <!-- 操作按钮 (v2.5.0 仍保留内部按钮, 但父组件右侧面板也会暴露 save; 不冲突) -->
    <div class="actions">
      <el-button type="primary" :icon="Check" :disabled="!dirty" @click="onSave">
        保存 mask
      </el-button>
      <el-button @click="$emit('cancel')">取消</el-button>
    </div>

    <div class="meta" v-if="maskStats">
      mask 尺寸: {{ canvasSize.w }}×{{ canvasSize.h }} |
      已标像素: {{ maskStats.painted }} / {{ maskStats.total }}
      ({{ (maskStats.painted / maskStats.total * 100).toFixed(1) }}%)
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Check, Brush, Delete, View, Refresh,
  ZoomIn, ZoomOut,
} from '@element-plus/icons-vue'

interface Category { id: number; name: string }
const props = defineProps<{
  imageUrl: string
  imageId: number
  imageWidth: number
  imageHeight: number
  categories: Category[]
  initialMaskUrl?: string | null
}>()
const emit = defineEmits<{
  (e: 'save', file: File): void
  (e: 'cancel'): void
  (e: 'clear'): void
  (e: 'dirty-change', dirty: boolean): void
}>()

// ============== State ==============
const mode = ref<'brush' | 'erase' | 'pan'>('brush')
const brushSize = ref(12)
const brushCategoryId = ref<number | null>(null)
const canvasSize = ref<{ w: number; h: number }>({ w: 0, h: 0 })
const imgCanvasRef = ref<HTMLCanvasElement | null>(null)
const maskCanvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const imgEl = new Image()
const maskEl = new Image()
const dirty = ref(false)
const initial = ref<string>('')
// v2.5.0 增强
const zoom = ref(1)
const mousePos = ref<{ x: number; y: number } | null>(null)
const mouseDown = ref(false)  // 平移模式拖动中
const panOffset = ref<{ x: number; y: number }>({ x: 0, y: 0 })
const spaceDown = ref(false)  // Space 临时平移

const PALETTE = [
  'rgba(245,108,108,0.85)',  // 红
  'rgba(103,194,58,0.85)',   // 绿
  'rgba(64,158,255,0.85)',   // 蓝
  'rgba(230,162,60,0.85)',   // 黄
  'rgba(155,89,182,0.85)',   // 紫
  'rgba(26,188,156,0.85)',   // 青
  'rgba(255,87,34,0.85)',    // 橙
  'rgba(144,147,153,0.85)',  // 灰
]
function colorOf(catId: number | null | undefined): string {
  if (catId == null) return PALETTE[0]
  return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
}

// v2.5.0: 当前类别名 (右侧 8 sections 需要显示)
function catName(id: number | null | undefined): string {
  if (id == null) return '未选'
  const c = props.categories.find((x: Category) => x.id === id)
  return c ? c.name : `#${id}`
}

// v2.5.0: 模式徽章
const modeTagType = computed(() => {
  if (mode.value === 'brush') return 'primary'
  if (mode.value === 'erase') return 'danger'
  return 'info'
})
const modeLabel = computed(() => {
  if (mode.value === 'brush') return '画刷'
  if (mode.value === 'erase') return '橡皮'
  return '查看'
})

// 监听 categories, 默认选第一个
watch(
  () => props.categories,
  (cats) => {
    if (brushCategoryId.value == null && cats.length > 0) {
      brushCategoryId.value = cats[0].id
    }
  },
  { immediate: true }
)

// v2.5.0: dirty 变化时通知父组件 (右侧 8 sections 用)
watch(dirty, (v) => emit('dirty-change', v))

// v2.5.0: 缩放
function zoomIn() { zoom.value = Math.min(8, +(zoom.value * 1.2).toFixed(3)) }
function zoomOut() { zoom.value = Math.max(0.25, +(zoom.value / 1.2).toFixed(3)) }
function resetZoom() { zoom.value = 1; panOffset.value = { x: 0, y: 0 } }
function onWheel(e: WheelEvent) {
  if (e.ctrlKey || e.metaKey) {
    // Ctrl+滚轮 缩放 (检测端同款)
    if (e.deltaY < 0) zoomIn(); else zoomOut()
  }
  // 普通滚轮 留给浏览器 (本身被 prevent 避免外层滚动)
}

// v2.5.0: 全局快捷键
function onKeyDown(e: KeyboardEvent) {
  // 在输入框里不响应
  const tag = (e.target as HTMLElement | null)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA') return
  if (e.code === 'KeyB') { mode.value = 'brush'; e.preventDefault() }
  else if (e.code === 'KeyE') { mode.value = 'erase'; e.preventDefault() }
  else if (e.code === 'KeyV') { mode.value = 'pan'; e.preventDefault() }
  else if (e.code === 'Space') { spaceDown.value = true; e.preventDefault() }
}
function onKeyUp(e: KeyboardEvent) {
  if (e.code === 'Space') { spaceDown.value = false }
}

// ============== 加载原图 + 已有 mask ==============
watch(
  () => [props.imageUrl, props.initialMaskUrl],
  () => loadAll()
)
onMounted(() => {
  loadAll()
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  imgEl.onload = null
  imgEl.onerror = null
  maskEl.onload = null
  maskEl.onerror = null
})

function loadAll() {
  if (!props.imageUrl) return
  imgEl.crossOrigin = 'anonymous'
  imgEl.onload = () => {
    const maxW = 600
    const scale = Math.min(1, maxW / (imgEl.naturalWidth || props.imageWidth || 1))
    canvasSize.value = {
      w: Math.round((imgEl.naturalWidth || props.imageWidth) * scale),
      h: Math.round((imgEl.naturalHeight || props.imageHeight) * scale),
    }
    nextTick(() => {
      drawImage()
      loadMask()
    })
  }
  imgEl.onerror = () => ElMessage.error('图片加载失败')
  imgEl.src = props.imageUrl
}

function loadMask() {
  const c = maskCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  if (!props.initialMaskUrl) {
    initial.value = ''
    dirty.value = false
    return
  }
  maskEl.crossOrigin = 'anonymous'
  maskEl.onload = () => {
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.drawImage(maskEl, 0, 0, c.width, c.height)
    initial.value = canvasToDataUrl(c)
    dirty.value = false
  }
  maskEl.onerror = () => {
    initial.value = ''
    dirty.value = false
  }
  maskEl.src = props.initialMaskUrl
}

function drawImage() {
  const c = imgCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  if (imgEl.complete && imgEl.naturalWidth > 0) {
    ctx.drawImage(imgEl, 0, 0, c.width, c.height)
  } else {
    ctx.fillStyle = '#f5f5f5'
    ctx.fillRect(0, 0, c.width, c.height)
  }
}

// ============== 鼠标事件: 画刷 / 橡皮 / 平移 ==============
const painting = ref(false)
function eventToCanvas(e: MouseEvent): { x: number; y: number } {
  if (!maskCanvasRef.value) return { x: 0, y: 0 }
  const rect = maskCanvasRef.value.getBoundingClientRect()
  return {
    x: Math.round((e.clientX - rect.left) * (maskCanvasRef.value.width / rect.width)),
    y: Math.round((e.clientY - rect.top) * (maskCanvasRef.value.height / rect.height)),
  }
}
function onMouseDown(e: MouseEvent) {
  const isPan = mode.value === 'pan' || spaceDown.value
  if (isPan) {
    mouseDown.value = true
    return
  }
  if (mode.value === 'brush' && brushCategoryId.value == null) {
    ElMessage.warning('请先在调色板选一个类别')
    return
  }
  painting.value = true
  paintAt(eventToCanvas(e))
}
function onMouseMove(e: MouseEvent) {
  // v2.5.0: 实时更新坐标浮标
  mousePos.value = eventToCanvas(e)
  if (mouseDown.value) return
  if (!painting.value) return
  paintAt(eventToCanvas(e))
}
function onMouseUp() { painting.value = false; mouseDown.value = false }

function paintAt(p: { x: number; y: number }) {
  const c = maskCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  if (mode.value === 'erase') {
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = 'rgba(0,0,0,1)'
    ctx.beginPath()
    ctx.arc(p.x, p.y, brushSize.value, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalCompositeOperation = 'source-over'
  } else {
    ctx.fillStyle = colorOf(brushCategoryId.value)
    ctx.beginPath()
    ctx.arc(p.x, p.y, brushSize.value, 0, Math.PI * 2)
    ctx.fill()
  }
  if (!dirty.value) {
    dirty.value = true
  }
}

// ============== mask 统计 / 保存 ==============
const maskStats = computed(() => {
  const c = maskCanvasRef.value
  if (!c || c.width === 0) return null
  const ctx = c.getContext('2d')
  if (!ctx) return null
  const data = ctx.getImageData(0, 0, c.width, c.height).data
  let painted = 0
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] > 0) painted++
  }
  return { painted, total: c.width * c.height }
})

function clearMask() {
  ElMessageBox.confirm('清空当前 mask?', '确认', {
    type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消',
  }).then(() => {
    const c = maskCanvasRef.value
    if (!c) return
    const ctx = c.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, c.width, c.height)
    dirty.value = true
    emit('clear')
  }).catch(() => {})
}

function canvasToDataUrl(c: HTMLCanvasElement): string {
  return c.toDataURL('image/png')
}

function onSave() {
  const c = maskCanvasRef.value
  if (!c) return
  c.toBlob((blob) => {
    if (!blob) { ElMessage.error('mask 生成失败'); return }
    const file = new File([blob], `mask_${props.imageId}.png`, { type: 'image/png' })
    initial.value = canvasToDataUrl(c)
    dirty.value = false
    emit('save', file)
  }, 'image/png')
}

// v2.5.0: 重置 dirty (切图时父组件调用)
function resetInitial() {
  const c = maskCanvasRef.value
  initial.value = c ? canvasToDataUrl(c) : ''
  dirty.value = false
}

// 渲染: 当 size 变化时重绘
watch(canvasSize, () => { drawImage(); loadMask() })

// v2.5.0: 暴露给父组件 (右侧 8 sections 调用)
defineExpose({
  // 状态
  mode, dirty, brushCategoryId, brushSize, zoom, mousePos,
  // 操作
  setMode: (m: 'brush' | 'erase' | 'pan') => { mode.value = m },
  setCategory: (id: number) => { brushCategoryId.value = id },
  setBrushSize: (n: number) => { brushSize.value = n },
  zoomIn, zoomOut, resetZoom,
  save: onSave,
  resetInitial,
})
</script>

<style scoped>
.seg-annotator {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.brush-size {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #606266;
}
.size-num {
  width: 40px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #909399;
  font-size: 12px;
}
.palette {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 13px;
  color: #606266;
}
.palette-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 2px solid #ebeef5;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  background: #fff;
  user-select: none;
}
.palette-item.active {
  border-width: 2px;
  background: #f0f9ff;
}
.cat-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  vertical-align: middle;
}
.canvas-wrap {
  position: relative;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 360px;
}
.layer {
  display: block;
  max-width: 100%;
}
.layer.interactive {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  cursor: crosshair;
  transform-origin: center center;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.meta {
  font-size: 12px;
  color: #909399;
}

/* v2.5.0: 画布坐标浮标 (与检测端 DetectionAnnotator 同款风格) */
.coord-overlay {
  position: absolute;
  font-size: 11px;
  font-family: ui-monospace, 'Cascadia Mono', 'Consolas', monospace;
  background: rgba(255, 255, 255, 0.92);
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  pointer-events: none;
  z-index: 10;
  white-space: nowrap;
}
.coord-bl {
  left: 8px;
  bottom: 8px;
  display: flex;
  gap: 12px;
  align-items: center;
}
.coord-br {
  right: 8px;
  bottom: 8px;
  color: #909399;
}
.coord-tl {
  left: 8px;
  top: 8px;
}
.coord-mouse {
  color: #67c23a;
  font-weight: 600;
}
.coord-mouse.idle {
  color: #c0c4cc;
  font-weight: normal;
}
.coord-cat {
  font-weight: 600;
}
</style>
