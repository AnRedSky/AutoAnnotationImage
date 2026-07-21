<!--
  DetectionAnnotator.vue (v2.3.1 极简版)
  ==========================================
  目标检测 bbox 画布组件 (只负责画布渲染 + 鼠标交互)

  v2.3.1 S10 重构:
  - 移除所有标注相关操作 UI (draw/edit 切换、撤销/重做、保存/取消、类别下拉、bbox 列表、快捷键提示)
  - 统一由父组件 (Annotate.vue 右侧操作面板) 触发
  - 通过 defineExpose 暴露 setMode / undo / redo / clearDraft / removeSelected / changeSelectedCategory / selectByIndex
    + defaultCategoryId / mode / canUndo / canRedo / selectedIndex 供父组件读

  职责:
  - 加载原图到 canvas (含缩放)
  - 鼠标拖拽绘制新 bbox (mousedown -> mousemove -> mouseup)
  - 渲染已有 bbox 列表 (颜色按 category_id 分配)
  - 选中 / 删除 / 改类别 bbox (通过父组件调方法)
  - 拖拽整体 bbox + 8 handle 缩放
  - 画布坐标浮标 + 尺寸提示
  - 键盘快捷键: Delete 删选中, Ctrl+Z/Y 撤销/重做, d/e 切模式 (保留, 跟画布操作强耦合)

  设计原则:
  - 零业务耦合: 不直接调 API, 不引 store
  - 坐标存储: 归一化 (0-1), 与后端 BBoxAnnotation 一致
  - 渲染坐标系: 实际像素 (canvas size), 由组件内部转换
  - 上下文菜单/标注相关操作按钮 → 全部移到父组件 (v2.3.1 目标)

  Props:
    imageUrl:    原图 URL (必填)
    imageId:     当前图 id (用于 emit)
    imageWidth:  原图实际宽
    imageHeight: 原图实际高
    categories:  [{id, name, color?}]  类别列表 (用于下拉选 + 颜色)
    modelValue:  当前 bbox 列表 [{x_min, y_min, x_max, y_max, category_id, id?}]

  Emits:
    update:modelValue  bbox 列表变更
    save               触发父组件保存
    cancel             撤销未保存的变更
    next               请求跳到下一张图
    prev               请求跳到上一张图

  Expose (v2.3.1 新增):
    setMode(m) / mode / canUndo / canRedo / undo() / redo() / clearDraft()
    removeSelected() / changeSelectedCategory(catId) / selectByIndex(i) / selectedIndex
    defaultCategoryId
-->
<template>
  <div class="det-annotator">
    <!-- 画布区域 (只渲染, 不带任何操作 UI) -->
    <div ref="wrapRef" class="canvas-wrap" @wheel.prevent="onWheel">
      <div class="canvas-stage" :style="{ width: canvasSize.w + 'px', height: canvasSize.h + 'px', transform: `scale(${zoom})` }">
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
      <!-- v2.3.2: 缩放控制条 (顶部中间) -->
      <div class="zoom-overlay">
        <el-button-group size="small">
          <el-button @click="zoomOut" :icon="ZoomOut" circle />
          <el-button @click="zoomReset" plain style="min-width: 64px;">{{ zoomPercent }}%</el-button>
          <el-button @click="zoomIn" :icon="ZoomIn" circle />
        </el-button-group>
      </div>
      <!-- 画布坐标浮标 (左下角) -->
      <div class="coord-overlay">
        <span v-if="cursorPos">
          x: <b>{{ cursorPos.x.toFixed(0) }}</b> ({{ (cursorPos.nx * 100).toFixed(1) }}%)
          &nbsp;y: <b>{{ cursorPos.y.toFixed(0) }}</b> ({{ (cursorPos.ny * 100).toFixed(1) }}%)
        </span>
        <span v-else>移入画布查看坐标</span>
      </div>
      <!-- 画布尺寸 (右下角) -->
      <div class="size-overlay">
        {{ canvasSize.w }} × {{ canvasSize.h }}px · 缩放 {{ scalePercent }}% · 显示 {{ zoomPercent }}%
      </div>
      <!-- 模式徽章 (左上角) -->
      <div class="mode-overlay" :class="`mode-${mode}`">
        {{ mode === 'draw' ? '绘制模式 (D)' : '编辑模式 (E)' }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ZoomIn, ZoomOut } from '@element-plus/icons-vue'

// ============== Props / Emits ==============
interface BBox {
  id?: number
  x_min: number
  y_min: number
  x_max: number
  y_max: number
  category_id: number
  confidence?: number
}
interface Category {
  id: number
  name: string
  color?: string
}
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
  // v2.5.1: dirty 状态变化通知 (父组件保存按钮 disabled 用, 避免深层 ref 访问响应式追踪失效)
  (e: 'dirty-change', dirty: boolean): void
}>()

// ============== State ==============
const mode = ref<'draw' | 'edit'>('draw')
const selectedIndex = ref<number | null>(null)
const defaultCategoryId = ref<number | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const imgEl = new Image()
const canvasSize = ref<{ w: number; h: number }>({ w: 0, h: 0 })

// 绘制中临时状态
const drawing = ref<{ x0: number; y0: number; x1: number; y1: number } | null>(null)

// 拖动 / 缩放 中临时状态
interface DragState {
  kind: 'move' | 'resize'
  handle?: ResizeHandle
  idx: number
  start: { x: number; y: number }
  orig: BBox
}
const dragging = ref<DragState | null>(null)

// 鼠标 hover 在 handle 上
const hoverHandle = ref<ResizeHandle | null>(null)

// v2.3.2: 画布缩放状态 (CSS transform, 不影响坐标计算)
const zoom = ref(1.0)
const MIN_ZOOM = 0.25
const MAX_ZOOM = 8.0
const zoomPercent = computed(() => Math.round(zoom.value * 100))
function onWheel(e: WheelEvent) {
  e.preventDefault()
  // 鼠标位置局部坐标 (相对 wrapRef 中心)
  const rect = wrapRef.value?.getBoundingClientRect()
  if (!rect) return
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  // 缩放: deltaY < 0 放大
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
  const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom.value * factor))
  if (newZoom === zoom.value) return
  zoom.value = newZoom
  // 缩放滚轮坐标: 让鼠标位置"对准" (粗略, 不需要 perfect pan)
  // wrapRef 已经有 overflow: auto, 用户可滚动查看
  void cx; void cy
}
function zoomIn() { zoom.value = Math.min(MAX_ZOOM, zoom.value * 1.25) }
function zoomOut() { zoom.value = Math.max(MIN_ZOOM, zoom.value / 1.25) }
function zoomReset() { zoom.value = 1.0 }

// 画布坐标浮标
const cursorPos = ref<{ x: number; y: number; nx: number; ny: number } | null>(null)
const scalePercent = computed(() => {
  const dw = props.imageWidth || 0
  if (!dw || !canvasSize.value.w) return '100'
  return ((canvasSize.value.w / dw) * 100).toFixed(0)
})

type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'

// 撤销栈
interface HistoryEntry { bboxes: BBox[] }
const undoStack = ref<HistoryEntry[]>([])
const redoStack = ref<HistoryEntry[]>([])
const MAX_HISTORY = 20
function snapshot() {
  undoStack.value.push({ bboxes: JSON.parse(JSON.stringify(props.modelValue || [])) })
  if (undoStack.value.length > MAX_HISTORY) undoStack.value.shift()
  redoStack.value = []
}
const canUndo = computed(() => undoStack.value.length > 0)
const canRedo = computed(() => redoStack.value.length > 0)
function undo() {
  if (!canUndo.value) return
  redoStack.value.push({ bboxes: JSON.parse(JSON.stringify(props.modelValue || [])) })
  const prev = undoStack.value.pop()!
  emit('update:modelValue', prev.bboxes)
  ElMessage.success('已撤销')
}
function redo() {
  if (!canRedo.value) return
  undoStack.value.push({ bboxes: JSON.parse(JSON.stringify(props.modelValue || [])) })
  const next = redoStack.value.pop()!
  emit('update:modelValue', next.bboxes)
  ElMessage.success('已重做')
}

// dirty
const initial = ref<string>(JSON.stringify(props.modelValue || []))
const dirty = computed(() => JSON.stringify(props.modelValue || []) !== initial.value)
// v2.5.1: dirty 变化时通知父组件 (右侧 8 sections 保存按钮 disabled 用)
// 解决: 父组件用 detAnnotRef?.dirty?.value 这种深层 ref 访问在模板中响应式追踪失效的问题
watch(dirty, (v) => emit('dirty-change', v), { immediate: true })
function resetInitial() {
  initial.value = JSON.stringify(props.modelValue || [])
  // 重置 dirty 时, 同步重置撤销栈 (否则切图后 undo 会回到旧图状态)
  undoStack.value = []
  redoStack.value = []
  selectedIndex.value = null
}

const selectedCategoryId = computed(() => {
  if (selectedIndex.value === null) return null
  return props.modelValue?.[selectedIndex.value]?.category_id ?? null
})

// 调色板
const PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function colorOf(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
}
function catName(catId: number | null | undefined): string {
  const c = props.categories.find((x) => x.id === catId)
  return c?.name || `cls_${catId}`
}

// 监听 categories 变化
watch(
  () => props.categories,
  (cats) => {
    if (defaultCategoryId.value == null && cats.length > 0) {
      defaultCategoryId.value = cats[0].id
    }
  },
  { immediate: true }
)

watch(
  () => props.imageUrl,
  () => {
    selectedIndex.value = null
    resetInitial()  // v2.3.2 修复: 切图后重置 dirty, 避免新图仍显示有未保存改动
    loadImage()
  }
)
onMounted(() => loadImage())

function loadImage() {
  if (!props.imageUrl) return
  imgEl.crossOrigin = 'anonymous'
  imgEl.onload = () => {
    const maxW = 600
    const scale = Math.min(1, maxW / (imgEl.naturalWidth || props.imageWidth || 1))
    canvasSize.value = {
      w: Math.round((imgEl.naturalWidth || props.imageWidth) * scale),
      h: Math.round((imgEl.naturalHeight || props.imageHeight) * scale),
    }
    nextTick(() => draw())
  }
  imgEl.onerror = () => ElMessage.error('图片加载失败')
  imgEl.src = props.imageUrl
}

function eventToImage(e: MouseEvent): { x: number; y: number } {
  if (!canvasRef.value) return { x: 0, y: 0 }
  const rect = canvasRef.value.getBoundingClientRect()
  const x = (e.clientX - rect.left) * (canvasRef.value.width / rect.width)
  const y = (e.clientY - rect.top) * (canvasRef.value.height / rect.height)
  return { x, y }
}
function pixelToNorm(p: { x: number; y: number }): { x: number; y: number } {
  return {
    x: Math.max(0, Math.min(1, p.x / canvasSize.value.w)),
    y: Math.max(0, Math.min(1, p.y / canvasSize.value.h)),
  }
}
function normToPixel(n: { x: number; y: number }): { x: number; y: number } {
  return { x: n.x * canvasSize.value.w, y: n.y * canvasSize.value.h }
}

const HANDLE_SIZE = 6
function getHandles(b: BBox): Record<ResizeHandle, { x: number; y: number }> {
  const x1 = b.x_min * canvasSize.value.w
  const y1 = b.y_min * canvasSize.value.h
  const x2 = b.x_max * canvasSize.value.w
  const y2 = b.y_max * canvasSize.value.h
  const xm = (x1 + x2) / 2
  const ym = (y1 + y2) / 2
  return {
    nw: { x: x1, y: y1 }, n: { x: xm, y: y1 }, ne: { x: x2, y: y1 },
    e: { x: x2, y: ym }, se: { x: x2, y: y2 }, s: { x: xm, y: y2 },
    sw: { x: x1, y: y2 }, w: { x: x1, y: ym },
  }
}
function hitTestHandle(p: { x: number; y: number }, b: BBox): ResizeHandle | null {
  const handles = getHandles(b)
  for (const [name, pos] of Object.entries(handles) as [ResizeHandle, { x: number; y: number }][]) {
    if (Math.abs(p.x - pos.x) <= HANDLE_SIZE && Math.abs(p.y - pos.y) <= HANDLE_SIZE) {
      return name
    }
  }
  return null
}
function handleCursor(h: ResizeHandle | null): string {
  if (!h) return 'default'
  const map: Record<ResizeHandle, string> = {
    nw: 'nwse-resize', se: 'nwse-resize',
    ne: 'nesw-resize', sw: 'nesw-resize',
    n: 'ns-resize', s: 'ns-resize',
    e: 'ew-resize', w: 'ew-resize',
  }
  return map[h]
}

function onMouseDown(e: MouseEvent) {
  const p = eventToImage(e)
  if (mode.value === 'draw') {
    drawing.value = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }
    return
  }
  const pn = pixelToNorm(p)
  if (selectedIndex.value !== null) {
    const sel = props.modelValue?.[selectedIndex.value]
    if (sel) {
      const h = hitTestHandle(p, sel)
      if (h) {
        dragging.value = {
          kind: 'resize', handle: h, idx: selectedIndex.value,
          start: p, orig: { ...sel },
        }
        return
      }
    }
  }
  const hitIdx = findHitIndex(pn)
  selectByIndex(hitIdx)
  if (hitIdx !== null) {
    const sel = props.modelValue?.[hitIdx]
    if (sel) {
      dragging.value = {
        kind: 'move', idx: hitIdx,
        start: p, orig: { ...sel },
      }
    }
  }
}
function onMouseMove(e: MouseEvent) {
  const p = eventToImage(e)
  const np = pixelToNorm(p)
  cursorPos.value = { x: p.x, y: p.y, nx: np.x, ny: np.y }
  if (drawing.value) {
    drawing.value.x1 = p.x
    drawing.value.y1 = p.y
    draw()
    return
  }
  if (dragging.value) {
    handleDrag(p)
    draw()
    return
  }
  if (mode.value === 'edit' && selectedIndex.value !== null) {
    const sel = props.modelValue?.[selectedIndex.value]
    const h = sel ? hitTestHandle(p, sel) : null
    if (h !== hoverHandle.value) {
      hoverHandle.value = h
      if (canvasRef.value) canvasRef.value.style.cursor = handleCursor(h)
    }
  } else {
    if (canvasRef.value) canvasRef.value.style.cursor = mode.value === 'draw' ? 'crosshair' : 'default'
  }
}
function onMouseUp(_e: MouseEvent) {
  cursorPos.value = null
  if (drawing.value) {
    const d = drawing.value
    drawing.value = null
    const xMin = Math.min(d.x0, d.x1) / canvasSize.value.w
    const yMin = Math.min(d.y0, d.y1) / canvasSize.value.h
    const xMax = Math.max(d.x0, d.x1) / canvasSize.value.w
    const yMax = Math.max(d.y0, d.y1) / canvasSize.value.h
    const wPx = Math.abs(d.x1 - d.x0)
    const hPx = Math.abs(d.y1 - d.y0)
    if (wPx < 5 || hPx < 5) { draw(); return }
    if (defaultCategoryId.value == null) {
      ElMessage.warning('请先在右侧选一个类别')
      draw(); return
    }
    snapshot()
    const next = [
      ...(props.modelValue || []),
      {
        x_min: round(xMin), y_min: round(yMin),
        x_max: round(xMax), y_max: round(yMax),
        category_id: defaultCategoryId.value,
      },
    ]
    emit('update:modelValue', next)
    // v2.3.2: 画完新 bbox 立即选中, 用户可立即改类别
    selectByIndex(next.length - 1)
    draw()
    return
  }
  if (dragging.value) {
    dragging.value = null
    draw()
  }
}

function handleDrag(cur: { x: number; y: number }) {
  const d = dragging.value!
  const list = [...(props.modelValue || [])]
  const b = { ...d.orig }
  if (d.kind === 'move') {
    const dx = (cur.x - d.start.x) / canvasSize.value.w
    const dy = (cur.y - d.start.y) / canvasSize.value.h
    const w = b.x_max - b.x_min
    const h = b.y_max - b.y_min
    let nx = d.orig.x_min + dx
    let ny = d.orig.y_min + dy
    nx = Math.max(0, Math.min(1 - w, nx))
    ny = Math.max(0, Math.min(1 - h, ny))
    b.x_min = round(nx)
    b.y_min = round(ny)
    b.x_max = round(nx + w)
    b.y_max = round(ny + h)
  } else if (d.kind === 'resize' && d.handle) {
    const o1 = normToPixel({ x: d.orig.x_min, y: d.orig.y_min })
    const o2 = normToPixel({ x: d.orig.x_max, y: d.orig.y_max })
    let nx1 = o1.x, ny1 = o1.y, nx2 = o2.x, ny2 = o2.y
    if (d.handle.includes('w')) nx1 = cur.x
    if (d.handle.includes('e')) nx2 = cur.x
    if (d.handle.includes('n')) ny1 = cur.y
    if (d.handle.includes('s')) ny2 = cur.y
    if (nx2 - nx1 < 5) {
      if (d.handle.includes('w')) nx1 = nx2 - 5
      else nx2 = nx1 + 5
    }
    if (ny2 - ny1 < 5) {
      if (d.handle.includes('n')) ny1 = ny2 - 5
      else ny2 = ny1 + 5
    }
    nx1 = Math.max(0, nx1); ny1 = Math.max(0, ny1)
    nx2 = Math.min(canvasSize.value.w, nx2); ny2 = Math.min(canvasSize.value.h, ny2)
    b.x_min = round(nx1 / canvasSize.value.w)
    b.y_min = round(ny1 / canvasSize.value.h)
    b.x_max = round(nx2 / canvasSize.value.w)
    b.y_max = round(ny2 / canvasSize.value.h)
  }
  list[d.idx] = b
  emit('update:modelValue', list)
}

function findHitIndex(p: { x: number; y: number }): number | null {
  const list = props.modelValue || []
  for (let i = list.length - 1; i >= 0; i--) {
    const b = list[i]
    if (p.x >= b.x_min && p.x <= b.x_max && p.y >= b.y_min && p.y <= b.y_max) {
      return i
    }
  }
  return null
}
function selectByIndex(i: number | null) {
  selectedIndex.value = i
  draw()
}
function removeAt(i: number) {
  const list = [...(props.modelValue || [])]
  list.splice(i, 1)
  snapshot()
  emit('update:modelValue', list)
  if (selectedIndex.value === i) selectedIndex.value = null
  else if (selectedIndex.value != null && selectedIndex.value > i) selectedIndex.value -= 1
  draw()
}
function removeSelected() {
  if (selectedIndex.value === null) return
  removeAt(selectedIndex.value)
}
function changeSelectedCategory(catId: number | null) {
  if (selectedIndex.value === null || catId == null) return
  const list = [...(props.modelValue || [])]
  list[selectedIndex.value] = { ...list[selectedIndex.value], category_id: catId }
  snapshot()
  emit('update:modelValue', list)
  draw()
}
function clearDraft() {
  if (!props.modelValue || props.modelValue.length === 0) return
  ElMessageBox.confirm('清空所有 bbox? (未保存)', '确认', {
    type: 'warning',
    confirmButtonText: '清空',
    cancelButtonText: '取消',
  }).then(() => {
    snapshot()
    emit('update:modelValue', [])
    selectedIndex.value = null
    draw()
  }).catch(() => {})
}

function setMode(m: 'draw' | 'edit') {
  mode.value = m
  if (m === 'draw') {
    selectedIndex.value = null
    if (canvasRef.value) canvasRef.value.style.cursor = 'crosshair'
  } else {
    if (canvasRef.value) canvasRef.value.style.cursor = 'default'
  }
}

// 键盘快捷键 (保留画布强相关: Delete / Ctrl+Z / d/e)
function onKey(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable) {
    return
  }
  if (e.ctrlKey || e.metaKey) {
    if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return }
    if ((e.key === 'z' && e.shiftKey) || e.key === 'y') { e.preventDefault(); redo(); return }
  }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selectedIndex.value !== null) { e.preventDefault(); removeSelected() }
    return
  }
  const k = e.key.toLowerCase()
  if (k === 'd') setMode('draw')
  else if (k === 'e') setMode('edit')
  else if (k === 'n') emit('next')
  else if (k === 'p') emit('prev')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// 保存 / 取消
function onSave() {
  if (!props.modelValue || props.modelValue.length === 0) {
    ElMessage.warning('至少画一个 bbox')
    return
  }
  initial.value = JSON.stringify(props.modelValue)
  emit('save', props.modelValue)
}

// 渲染
function draw() {
  const c = canvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  if (imgEl.complete && imgEl.naturalWidth > 0) {
    ctx.drawImage(imgEl, 0, 0, c.width, c.height)
  } else {
    ctx.fillStyle = '#f5f5f5'
    ctx.fillRect(0, 0, c.width, c.height)
    ctx.fillStyle = '#999'
    ctx.font = '14px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('图片加载中…', c.width / 2, c.height / 2)
  }
  const list = props.modelValue || []
  list.forEach((b, i) => {
    const x = b.x_min * c.width
    const y = b.y_min * c.height
    const w = (b.x_max - b.x_min) * c.width
    const h = (b.y_max - b.y_min) * c.height
    const color = colorOf(b.category_id)
    const selected = selectedIndex.value === i
    ctx.strokeStyle = color
    ctx.lineWidth = selected ? 3 : 2
    ctx.strokeRect(x, y, w, h)
    ctx.fillStyle = color
    const label = `#${i + 1} ${catName(b.category_id)}`
    const labelW = 8 + ctx.measureText(label).width
    ctx.fillRect(x, y - 18, labelW, 18)
    ctx.fillStyle = '#fff'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText(label, x + 4, y - 4)
  })
  if (mode.value === 'edit' && selectedIndex.value !== null) {
    const sel = list[selectedIndex.value]
    if (sel) {
      const handles = getHandles(sel)
      for (const [name, pos] of Object.entries(handles) as [ResizeHandle, { x: number; y: number }][]) {
        const isHover = hoverHandle.value === name
        ctx.fillStyle = isHover ? '#ff5722' : '#fff'
        ctx.strokeStyle = '#409eff'
        ctx.lineWidth = 2
        const s = isHover ? HANDLE_SIZE + 1 : HANDLE_SIZE
        ctx.fillRect(pos.x - s / 2, pos.y - s / 2, s, s)
        ctx.strokeRect(pos.x - s / 2, pos.y - s / 2, s, s)
      }
    }
  }
  if (drawing.value) {
    const d = drawing.value
    const x = Math.min(d.x0, d.x1)
    const y = Math.min(d.y0, d.y1)
    const w = Math.abs(d.x1 - d.x0)
    const h = Math.abs(d.y1 - d.y0)
    ctx.strokeStyle = '#409eff'
    ctx.lineWidth = 2
    ctx.setLineDash([5, 3])
    ctx.strokeRect(x, y, w, h)
    ctx.setLineDash([])
  }
}

function round(v: number) { return Math.round(v * 10000) / 10000 }

watch(
  () => props.modelValue,
  () => draw(),
  { deep: true }
)

onBeforeUnmount(() => {
  imgEl.onload = null
  imgEl.onerror = null
})

// ============== v2.3.1 S10: 暴露给父组件 (右侧操作面板) ==============
defineExpose({
  setMode,
  undo,
  redo,
  clearDraft,
  removeSelected,
  changeSelectedCategory,
  selectByIndex,
  save: onSave,
  resetInitial,  // v2.3.2: 父组件保存后重置 dirty
  mode,
  canUndo,
  canRedo,
  selectedIndex,
  defaultCategoryId,
  dirty,
  categories: computed(() => props.categories),
  colorOf,
  catName,
  // v2.3.2: 画布缩放控制
  zoomIn,
  zoomOut,
  zoomReset,
  zoomPercent,
})
</script>

<style scoped>
.det-annotator {
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
.canvas {
  display: block;
  max-width: 100%;
  cursor: crosshair;
  user-select: none;
}
/* v2.3.2: 缩放控制条 (顶部中间) */
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
/* 坐标浮标 (左下角) */
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
}
.coord-overlay b {
  color: #67c23a;
  font-weight: 600;
  margin: 0 2px;
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
/* 模式徽章 (左下角上方) */
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
.mode-overlay.mode-edit {
  background: rgba(230, 162, 60, 0.85);
}
</style>
