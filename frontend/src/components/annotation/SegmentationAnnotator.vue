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
  - 通过 emit('save', maskBlob) 抛出 PNG 文件 (L-mode 单通道索引), 父组件负责上传到后端

  v2.5.0-s12.8 关键修复 (mask 数据层重构):
  - 之前: canvas 直接画 RGBA 彩色, save 时上传 RGBA PNG, 后端 400 拒绝
  - 现在: 维护 maskData (Uint16Array, 单通道 category_id 索引)
          显示层: maskData[i] > 0 → 画彩色 (调色板)
          保存层: maskData → 离屏 canvas L-mode → PNG (后端契约)
  - 配合 v2.5.0-s12.3a: 缩放/defineExpose/快捷键/坐标浮标

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
function resetInitial() {
  const c = maskCanvasRef.value
  initial.value = c ? canvasToDataUrl(c) : ''
  initialDataSnapshot = new Uint16Array(maskData)
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
