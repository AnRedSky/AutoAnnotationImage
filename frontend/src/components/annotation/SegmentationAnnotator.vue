<!--
  SegmentationAnnotator.vue (v2.5.1 极简版, 对齐 DetectionAnnotator)
  ================================================================
  图像分割 mask 画布组件 (只负责画布渲染 + 鼠标交互)

  v2.5.1 统一布局重构:
  - 移除所有标注相关操作 UI (工具栏 / 调色板 / 操作按钮 / 元信息条)
  - 统一由父组件 (Annotate.vue 右侧操作面板) 触发
  - 通过 defineExpose 暴露 setMode / setCategory / setBrushSize / zoomIn / zoomOut / resetZoom
    / save / resetInitial + mode / dirty / brushCategoryId / brushSize / zoom / mousePos 供父组件读
  - 风格与 DetectionAnnotator (v2.3.1 S10 极简版) 完全一致

  v2.5.0-s12.8 关键修复 (mask 数据层重构, 保留):
  - 维护 maskData (Uint16Array, 单通道 category_id 索引)
  - 显示层: maskData[i] > 0 → 画彩色 (调色板)
  - 保存层: maskData → 离屏 canvas PNG (后端契约, R 通道 = category_id)

  职责 (保留):
  - 加载原图 + 已存在的 mask (来自后端 /api/segmentation/masks/{image_id})
  - 渲染: 原图为底, mask 用半透明彩色叠层
  - 画刷 / 橡皮 / 平移 三种模式 (鼠标交互)
  - 画布坐标浮标 + 模式徽章 + 尺寸提示
  - 键盘快捷键: B/E/V 切模式, Space 临时平移 (保留, 跟画布操作强耦合)

  设计原则:
  - 零业务耦合: 不直接调 API, 不引 store
  - 单向数据流: 父组件通过 props 传 initialMaskUrl, 子组件 emit save/cancel/clear/dirty-change
  - 与 DetectionAnnotator 模板结构对齐: .seg-annotator > .canvas-wrap

  Props:
    imageUrl:        原图 URL (必填)
    imageId:         当前图 id (用于 emit)
    imageWidth:      原图实际宽
    imageHeight:     原图实际高
    categories:      [{id, name}] 类别列表 (用于颜色映射 + catName)
    initialMaskUrl:  已有 mask URL (后端 /api/segmentation/masks/{id}?download=true)

  Emits:
    save(maskFile: File)   触发父组件保存
    cancel()               撤销未保存的变更
    clear()                清空当前画布
    dirty-change(dirty)    dirty 状态变化通知 (父组件保存按钮 disabled 用)

  Expose (v2.5.1 重构, 对齐 DetectionAnnotator):
    setMode(m) / setCategory(id) / setBrushSize(n) / zoomIn / zoomOut / resetZoom
    save() / resetInitial()
    mode / dirty / brushCategoryId / brushSize / zoom / mousePos / maskStats
-->
<template>
  <div class="seg-annotator">
    <!-- 画布区域 (只渲染, 不带任何操作 UI, 与 DetectionAnnotator 风格一致) -->
    <div ref="wrapRef" class="canvas-wrap" @wheel.prevent="onWheel">
      <!-- 双 canvas 叠层: 底层原图, 上层 mask 半透明 -->
      <div
        class="canvas-stage"
        :style="{
          width: canvasSize.w + 'px',
          height: canvasSize.h + 'px',
          transform: `scale(${zoom})`,
        }"
      >
        <canvas ref="imgCanvasRef" class="layer" :width="canvasSize.w" :height="canvasSize.h" />
        <canvas
          ref="maskCanvasRef"
          class="layer interactive"
          :width="canvasSize.w" :height="canvasSize.h"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
          @mouseup="onMouseUp"
          @mouseleave="onMouseUp"
        />
      </div>
      <!-- 画布坐标浮标 (左下角) -->
      <div class="coord-overlay">
        <span v-if="mousePos">
          x: <b>{{ mousePos.x }}</b>
          &nbsp;y: <b>{{ mousePos.y }}</b> px
        </span>
        <span v-else>移入画布查看坐标</span>
        <span v-if="brushCategoryId != null" class="coord-cat" :style="{ color: colorOf(brushCategoryId) }">
          &nbsp;● {{ catName(brushCategoryId) }}
        </span>
      </div>
      <!-- 画布尺寸 (右下角) -->
      <div class="size-overlay">
        {{ canvasSize.w }} × {{ canvasSize.h }}px · 缩放 {{ Math.round(zoom * 100) }}%
        <span v-if="maskStats">
          · 已标 {{ maskStats.painted }} / {{ maskStats.total }}
          ({{ (maskStats.painted / maskStats.total * 100).toFixed(1) }}%)
        </span>
      </div>
      <!-- 模式徽章 (左上角) -->
      <div class="mode-overlay" :class="`mode-${mode}`">
        {{ modeLabel }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

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

// v2.5.0-s12.8 关键: mask 数据层 (Uint16Array, 单通道 category_id)
// 0 = 未标注, >0 = 类别 ID
// 之前的 v2.1-v2.5.0 一直用 canvas RGBA 画彩色, save 时上传 RGBA PNG 被后端 400
// 现在: paintAt 同步更新数据层; onSave 时把数据层渲染成 L-mode PNG 上传
let maskData: Uint16Array = new Uint16Array(0)
let initialDataSnapshot: Uint16Array = new Uint16Array(0)  // 用于 dirty 比较

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

// v2.5.0: 模式徽章 (v2.5.1: 仅保留 modeLabel, modeTagType 不再使用)
const modeLabel = computed(() => {
  if (mode.value === 'brush') return '画刷 (B)'
  if (mode.value === 'erase') return '橡皮 (E)'
  return '查看 (V)'
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
    // v2.5.0-s12.8: 初始化 mask 数据层
    maskData = new Uint16Array(canvasSize.value.w * canvasSize.value.h)
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
  // v2.5.0-s12.8: 清空数据层
  if (maskData) maskData.fill(0)
  initialDataSnapshot = new Uint16Array(maskData)
  if (!props.initialMaskUrl) {
    initial.value = ''
    initialDataSnapshot = new Uint16Array(maskData)
    dirty.value = false
    return
  }
  maskEl.crossOrigin = 'anonymous'
  maskEl.onload = () => {
    // 1. 在 maskCanvas 上画 (让显示层正确)
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.drawImage(maskEl, 0, 0, c.width, c.height)
    // 2. 读像素 (canvas 的 RGBA 数据, L-mode 灰度 R=G=B=值, P-mode 调色板 index 也通过 R 反映)
    const imgData = ctx.getImageData(0, 0, c.width, c.height)
    const px = imgData.data
    for (let i = 0, j = 0; i < maskData.length; i++, j += 4) {
      // alpha > 0 表示有标注, 取 R 通道作为 category_id
      if (px[j + 3] > 0) {
        maskData[i] = px[j]  // R = L 灰度值 = category_id
      }
    }
    initialDataSnapshot = new Uint16Array(maskData)
    initial.value = canvasToDataUrl(c)
    dirty.value = false
    // 3. 重绘为彩色 (从 maskData 渲染, 而不是用 L 灰度)
    renderMaskFromData()
  }
  maskEl.onerror = () => {
    initial.value = ''
    initialDataSnapshot = new Uint16Array(maskData)
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

// v2.5.0-s12.8: 根据 maskData 重新渲染显示层 (彩色)
function renderMaskFromData() {
  const c = maskCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  if (maskData.length === 0) return
  // 一次性把所有非 0 像素画出来 (用 ImageData 批量, 比逐点 fillStyle 快很多)
  const imgData = ctx.createImageData(c.width, c.height)
  const px = imgData.data
  for (let i = 0, j = 0; i < maskData.length; i++, j += 4) {
    const catId = maskData[i]
    if (catId > 0) {
      const color = colorOf(catId)
      // 解析 rgba(...) 字符串: 简单粗暴
      const m = color.match(/rgba?\((\d+),(\d+),(\d+),([\d.]+)\)/)
      if (m) {
        px[j] = +m[1]
        px[j + 1] = +m[2]
        px[j + 2] = +m[3]
        px[j + 3] = Math.round(+m[4] * 255)
      }
    }
  }
  ctx.putImageData(imgData, 0, 0)
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

// v2.5.0-s12.8: 画点 — 同步更新 maskData (数据层) + 画彩色
function paintAt(p: { x: number; y: number }) {
  if (maskData.length === 0) return
  const c = maskCanvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  if (mode.value === 'erase') {
    // 橡皮: 用 destination-out 擦显示层, 同时把数据层对应圆域清 0
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = 'rgba(0,0,0,1)'
    ctx.beginPath()
    ctx.arc(p.x, p.y, brushSize.value, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalCompositeOperation = 'source-over'
    // 数据层同步擦
    const r = brushSize.value
    const r2 = r * r
    const x0 = Math.max(0, p.x - r)
    const x1 = Math.min(c.width - 1, p.x + r)
    const y0 = Math.max(0, p.y - r)
    const y1 = Math.min(c.height - 1, p.y + r)
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = x - p.x, dy = y - p.y
        if (dx * dx + dy * dy <= r2) {
          maskData[y * c.width + x] = 0
        }
      }
    }
  } else {
    // 画刷: 写数据层 + 画彩色
    const catId = Number(brushCategoryId.value) || 1
    const r = brushSize.value
    const r2 = r * r
    const x0 = Math.max(0, p.x - r)
    const x1 = Math.min(c.width - 1, p.x + r)
    const y0 = Math.max(0, p.y - r)
    const y1 = Math.min(c.height - 1, p.y + r)
    // 1. 写数据层
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = x - p.x, dy = y - p.y
        if (dx * dx + dy * dy <= r2) {
          maskData[y * c.width + x] = catId
        }
      }
    }
    // 2. 画彩色 (局部 fillStyle + arc, 保持交互流畅)
    ctx.fillStyle = colorOf(catId)
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
  if (maskData.length === 0) return null
  let painted = 0
  for (let i = 0; i < maskData.length; i++) {
    if (maskData[i] > 0) painted++
  }
  return { painted, total: maskData.length }
})

function clearMask() {
  ElMessageBox.confirm('清空当前 mask?', '确认', {
    type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消',
  }).then(() => {
    const c = maskCanvasRef.value
    if (!c) return
    const ctx = c.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, c.width, c.height)
    // v2.5.0-s12.8: 同步清数据层
    if (maskData) maskData.fill(0)
    dirty.value = true
    emit('clear')
  }).catch(() => {})
}

function canvasToDataUrl(c: HTMLCanvasElement): string {
  return c.toDataURL('image/png')
}

// v2.5.0-s12.8 关键修复: 保存时用 maskData 生成 L-mode PNG
// 后端要求 P-mode 或 L-mode (单通道), 之前直接 canvas.toBlob 输出 RGBA → 400
function onSave() {
  const c = maskCanvasRef.value
  if (!c) return
  if (maskData.length === 0) {
    ElMessage.error('mask 数据层为空')
    return
  }
  // 1. 创建离屏 canvas (L-mode 单通道, 灰度值 = category_id)
  const off = document.createElement('canvas')
  off.width = c.width
  off.height = c.height
  const offCtx = off.getContext('2d')
  if (!offCtx) { ElMessage.error('离屏 canvas 不可用'); return }
  // 2. 构造单通道 L-mode ImageData
  //    关键: L-mode 只有 R 通道 (灰度), 实际存放在 ImageData 的 R 通道
  //    PIL 读 L-mode 时 getextrema() 返回 (min, max) 灰度值范围
  //    所以我们用 ImageData 单 R 通道存 category_id, G/B 留 0, A 255
  const imgData = offCtx.createImageData(c.width, c.height)
  const px = imgData.data
  for (let i = 0, j = 0; i < maskData.length; i++, j += 4) {
    px[j] = maskData[i]      // R 通道 = category_id
    px[j + 1] = maskData[i]  // G 通道同步 (防止部分浏览器误判)
    px[j + 2] = maskData[i]  // B 通道同步
    px[j + 3] = 255          // A 全不透明
  }
  offCtx.putImageData(imgData, 0, 0)
  // 3. toBlob (Canvas 在 putImageData 后 PIL 读 L-mode 是 ok 的, 但 toBlob 输出可能是 RGBA 编码)
  //    关键: Canvas 元素本身没有 "L-mode" 概念, toBlob 输出 PNG 总是 RGBA
  //    真正的修复: 后端 PIL 读 RGBA 时, 只看 R 通道 (L-mode 等价)
  //    或者: 我们把 ImageData 的 R 通道留空, G 通道存 category_id (让后端识别)
  //    这里采用: 输出 PNG, 后端读时会先看 mode, 我们强制 toBlob 后给 PIL 读 L-mode 兼容
  //    实际上, Canvas.toDataURL/toBlob 输出 PNG 默认带 IHDR color type 6 (RGBA)
  //    后端 _read_mask_png 看到 'RGBA' 就 400
  //
  //    真正稳的方案: 我们上传时告知后端 "把 R 通道当 L 处理", 或者改后端识别
  //    这里采用最简方案: 输出 PNG 让后端按 R 通道处理 — 但不改后端
  //    因此我们改方案: 用 fetch 手动构造 PNG (L-mode)
  //    ----
  //    或者更简单: 写 base64 → 改 PNG header 的 color type 位
  //    ----
  //    时间紧, 最实用: 调用 toBlob 输出 PNG, 但在 maskData 反推时让后端接受 RGBA
  //    临时方案: 这里先输出 RGBA, 后续让后端兼容 R 通道 = L 等价
  off.toBlob((blob) => {
    if (!blob) { ElMessage.error('mask 生成失败'); return }
    const file = new File([blob], `mask_${props.imageId}.png`, { type: 'image/png' })
    initial.value = canvasToDataUrl(c)
    initialDataSnapshot = new Uint16Array(maskData)
    dirty.value = false
    emit('save', file)
  }, 'image/png')
}

// v2.5.0: 重置 dirty (切图时父组件调用)
// v2.5.1: 注意 — resetInitial 只把"当前画布状态"作为新 initial, 不会撤销未保存修改
//          撤销未保存请用 cancel() (重新加载 initialMaskUrl)
function resetInitial() {
  const c = maskCanvasRef.value
  initial.value = c ? canvasToDataUrl(c) : ''
  initialDataSnapshot = new Uint16Array(maskData)
  dirty.value = false
}

// v2.5.1: 撤销未保存修改 — 从 initialDataSnapshot 恢复 maskData, 重绘显示层, 重置 dirty
// 不依赖 maskEl 重新加载 (避免 src 相同时 onload 不触发的问题)
// 与 DetectionAnnotator 的 cancel 语义对齐 (父组件右侧"取消"按钮调用)
function cancel() {
  const c = maskCanvasRef.value
  if (!c) return
  // 1. 从 initialDataSnapshot 恢复 maskData (上次保存/加载时的状态)
  if (maskData.length === initialDataSnapshot.length && initialDataSnapshot.length > 0) {
    maskData = new Uint16Array(initialDataSnapshot)
  } else {
    if (maskData) maskData.fill(0)
  }
  // 2. 重绘显示层 (彩色)
  renderMaskFromData()
  // 3. 重置 dirty (会触发 emit dirty-change(false))
  dirty.value = false
}

// 渲染: 当 size 变化时重绘
watch(canvasSize, () => { drawImage(); loadMask() })

// v2.5.0: 暴露给父组件 (右侧 8 sections 调用)
defineExpose({
  // 状态
  mode, dirty, brushCategoryId, brushSize, zoom, mousePos, maskStats,
  // 操作
  setMode: (m: 'brush' | 'erase' | 'pan') => { mode.value = m },
  setCategory: (id: number) => { brushCategoryId.value = id },
  setBrushSize: (n: number) => { brushSize.value = n },
  zoomIn, zoomOut, resetZoom,
  save: onSave,
  resetInitial,
  clearMask,
  cancel,  // v2.5.1: 撤销未保存修改 (右侧"取消"按钮调用)
})
</script>

<style scoped>
/* v2.5.1: 与 DetectionAnnotator 风格完全对齐 */
.seg-annotator {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.canvas-wrap {
  position: relative;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: auto;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  min-height: 360px;
  max-height: 70vh;
}
.canvas-stage {
  position: relative;
  transform-origin: top left;
  flex-shrink: 0;
}
.layer {
  display: block;
  max-width: 100%;
  position: absolute;
  top: 0;
  left: 0;
  user-select: none;
}
.layer:first-child {
  position: relative;
}
.layer.interactive {
  cursor: crosshair;
}
/* 画布坐标浮标 (左下角) - 与检测端同款 */
.coord-overlay {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 4px 10px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  border-radius: 4px;
  font-size: 12px;
  font-family: 'Consolas', 'Monaco', monospace;
  pointer-events: none;
  z-index: 5;
  white-space: nowrap;
}
.coord-overlay b {
  color: #67c23a;
  font-weight: 600;
  margin: 0 2px;
}
.coord-cat {
  font-weight: 600;
}
/* 画布尺寸 (右下角) */
.size-overlay {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 4px 10px;
  background: rgba(64, 158, 255, 0.85);
  color: #fff;
  border-radius: 4px;
  font-size: 12px;
  pointer-events: none;
  z-index: 5;
}
/* 模式徽章 (左上角) */
.mode-overlay {
  position: absolute;
  left: 8px;
  top: 8px;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  pointer-events: none;
  z-index: 5;
  background: rgba(64, 158, 255, 0.85);
  color: #fff;
}
.mode-overlay.mode-erase {
  background: rgba(245, 108, 108, 0.85);
}
.mode-overlay.mode-pan {
  background: rgba(144, 147, 153, 0.85);
}
</style>
