<!--
  DetectionAnnotator.vue (v2.2.0 增强版)
  ==========================================
  目标检测 bbox 画布组件

  职责:
  - 加载原图到 canvas (含 HiDPI 自适应)
  - 鼠标拖拽绘制新 bbox (mousedown -> mousemove -> mouseup)
  - 渲染已有 bbox 列表 (颜色按 category_id 分配)
  - 支持选中 / 删除 / 改类别 bbox
  - 拖拽整体 bbox + 8 handle 缩放 (v2.2.0 新增)
  - 键盘快捷键: Delete 删除选中, d/e 切模式, n/p 上下张, Ctrl+Z 撤销 (v2.2.0 新增)
  - 通过 emit('change', bboxes) 抛出当前 bbox 列表, 由父组件 (Annotate.vue) 负责持久化

  设计原则:
  - 零业务耦合: 不直接调 API, 不引 store; 数据全靠 props 传入, 操作全靠 emit
  - 坐标存储: 归一化 (0-1), 与后端 BBoxAnnotation.x_min/y_min/x_max/y_max 一致
  - 渲染坐标系: 实际像素 (canvas size), 由组件内部转换

  Props:
    imageUrl:    原图 URL (必填)
    imageId:     当前图 id (用于 emit)
    imageWidth:  原图实际宽
    imageHeight: 原图实际高
    categories:  [{id, name, color?}]  类别列表 (用于下拉选 + 颜色)
    modelValue:  当前 bbox 列表 [{x_min, y_min, x_max, y_max, category_id, id?}]

  Emits:
    update:modelValue  bbox 列表变更
    save               触发父组件保存 (父组件拿当前 modelValue 调后端 API)
    cancel             撤销未保存的变更 (父组件可重读 server-side bbox)
    next               请求跳到下一张图 (n 键)
    prev               请求跳到上一张图 (p 键)
-->
<template>
  <div class="det-annotator">
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-button-group size="small">
        <el-button :type="mode === 'draw' ? 'primary' : 'default'" @click="setMode('draw')">
          <el-icon><EditPen /></el-icon>绘制 (D)
        </el-button>
        <el-button :type="mode === 'edit' ? 'primary' : 'default'" @click="setMode('edit')">
          <el-icon><Select /></el-icon>编辑 (E)
        </el-button>
        <el-button @click="undo" :disabled="!canUndo">
          <el-icon><RefreshLeft /></el-icon>撤销 (Ctrl+Z)
        </el-button>
        <el-button @click="redo" :disabled="!canRedo">
          <el-icon><RefreshRight /></el-icon>重做 (Ctrl+Shift+Z)
        </el-button>
        <el-button @click="clearDraft">清空未保存</el-button>
      </el-button-group>
      <span class="hint">
        <template v-if="mode === 'draw'">
          拖拽鼠标画新 bbox · 切换下一张 (N) · 上一张 (P)
        </template>
        <template v-else>
          点击选中 · Delete 键删除 · 拖动 body 平移 · 拖 8 个 handle 缩放
        </template>
      </span>
    </div>

    <!-- 画布区域 -->
    <div ref="wrapRef" class="canvas-wrap">
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
      <!-- v2.3.0 S10: 画布坐标浮标 (左下角) -->
      <div class="coord-overlay">
        <span v-if="cursorPos">
          x: <b>{{ cursorPos.x.toFixed(0) }}</b> ({{ (cursorPos.nx * 100).toFixed(1) }}%)
          &nbsp;y: <b>{{ cursorPos.y.toFixed(0) }}</b> ({{ (cursorPos.ny * 100).toFixed(1) }}%)
        </span>
        <span v-else>移入画布查看坐标</span>
      </div>
      <!-- v2.3.0 S10: 画布尺寸 (右下角) -->
      <div class="size-overlay">
        {{ canvasSize.w }} × {{ canvasSize.h }}px · 缩放 {{ scalePercent }}%
      </div>
    </div>

    <!-- 类别下拉 (绘制模式时设置下一个 bbox 的默认类别) -->
    <div class="cat-bar" v-if="mode === 'draw'">
      <span>新 bbox 类别:</span>
      <el-select v-model="defaultCategoryId" placeholder="选择类别" size="small" style="width: 200px;" filterable>
        <el-option
          v-for="c in categories" :key="c.id" :value="c.id"
          :label="c.name"
        >
          <span class="cat-dot" :style="{ background: colorOf(c.id) }"></span>
          {{ c.name }}
        </el-option>
      </el-select>
    </div>

    <!-- 选中 bbox 时的类别修改下拉 (编辑模式) -->
    <div class="cat-bar" v-else-if="selectedIndex !== null">
      <span>选中 bbox #{{ selectedIndex + 1 }} 类别:</span>
      <el-select
        :model-value="selectedCategoryId"
        @update:model-value="(v: number | null) => changeSelectedCategory(v)"
        size="small" style="width: 200px;" filterable
      >
        <el-option
          v-for="c in categories" :key="c.id" :value="c.id"
          :label="c.name"
        >
          <span class="cat-dot" :style="{ background: colorOf(c.id) }"></span>
          {{ c.name }}
        </el-option>
      </el-select>
      <el-button size="small" type="danger" plain @click="removeSelected">
        <el-icon><Delete /></el-icon>删除 (Del)
      </el-button>
    </div>

    <!-- bbox 列表 (用于在编辑模式选择 / 改类别 / 删除) -->
    <div class="bbox-list" v-if="modelValue && modelValue.length > 0">
      <div class="list-title">当前 bbox ({{ modelValue.length }})</div>
      <el-tag
        v-for="(b, idx) in modelValue" :key="b.id || idx"
        :type="selectedIndex === idx ? 'primary' : 'info'"
        :effect="selectedIndex === idx ? 'dark' : 'plain'"
        class="bbox-tag"
        @click="selectIndex(idx)"
        closable
        @close="removeAt(idx)"
      >
        <span class="cat-dot" :style="{ background: colorOf(b.category_id) }"></span>
        #{{ idx + 1 }} {{ catName(b.category_id) }}
      </el-tag>
    </div>

    <!-- 操作按钮 -->
    <div class="actions">
      <el-button type="primary" :icon="Check" :disabled="!dirty" @click="onSave">
        保存 ({{ modelValue?.length || 0 }})
      </el-button>
      <el-button @click="$emit('cancel')">取消</el-button>
      <span class="shortcut-hint">
        <el-tag size="small" effect="plain">D 绘制</el-tag>
        <el-tag size="small" effect="plain">E 编辑</el-tag>
        <el-tag size="small" effect="plain">N 下一张</el-tag>
        <el-tag size="small" effect="plain">P 上一张</el-tag>
        <el-tag size="small" effect="plain">Del 删除</el-tag>
        <el-tag size="small" effect="plain">Ctrl+Z 撤销</el-tag>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, EditPen, Select, Delete, RefreshLeft, RefreshRight } from '@element-plus/icons-vue'

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

// 拖动 / 缩放 中临时状态 (v2.2.0 新增)
interface DragState {
  kind: 'move' | 'resize'
  handle?: ResizeHandle  // 仅 resize 时
  idx: number
  start: { x: number; y: number }  // canvas 像素
  orig: BBox  // 归一化
}
const dragging = ref<DragState | null>(null)

// 鼠标 hover 在 handle 上 (用于改变 cursor)
const hoverHandle = ref<ResizeHandle | null>(null)

// v2.3.0 S10: 鼠标坐标浮标
const cursorPos = ref<{ x: number; y: number; nx: number; ny: number } | null>(null)
// 缩放比例 (显示用, 0-100%, 1.0=100%)
const scalePercent = computed(() => {
  const dw = props.imageWidth || 0
  if (!dw || !canvasSize.value.w) return '100'
  return ((canvasSize.value.w / dw) * 100).toFixed(0)
})

type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'

// 撤销栈 (v2.2.0 新增, 仅跟踪 modelValue 变更)
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

// dirty: modelValue 与初始化时不一致视为有未保存改动
const initial = ref<string>(JSON.stringify(props.modelValue || []))
const dirty = computed(() => JSON.stringify(props.modelValue || []) !== initial.value)

// 选中 bbox 的 category_id (用于编辑模式下拉双向绑定)
const selectedCategoryId = computed(() => {
  if (selectedIndex.value === null) return null
  return props.modelValue?.[selectedIndex.value]?.category_id ?? null
})

// ============== 类别调色板 (固定 8 色, 按 category_id hash) ==============
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

// 监听 categories 变化, 默认选第一个
watch(
  () => props.categories,
  (cats) => {
    if (defaultCategoryId.value == null && cats.length > 0) {
      defaultCategoryId.value = cats[0].id
    }
  },
  { immediate: true }
)

// 监听 imageUrl 变化, 重新加载图片; 加载时清空选中 + 撤销栈
watch(
  () => props.imageUrl,
  () => {
    selectedIndex.value = null
    undoStack.value = []
    redoStack.value = []
    loadImage()
  }
)
onMounted(() => loadImage())

// ============== 图片加载 + canvas 尺寸 ==============
function loadImage() {
  if (!props.imageUrl) return
  imgEl.crossOrigin = 'anonymous'
  imgEl.onload = () => {
    // 限定画布: 最大 600 宽, 等比缩放
    const maxW = 600
    const scale = Math.min(1, maxW / (imgEl.naturalWidth || props.imageWidth || 1))
    canvasSize.value = {
      w: Math.round((imgEl.naturalWidth || props.imageWidth) * scale),
      h: Math.round((imgEl.naturalHeight || props.imageHeight) * scale),
    }
    nextTick(() => draw())
  }
  imgEl.onerror = () => {
    ElMessage.error('图片加载失败')
  }
  imgEl.src = props.imageUrl
}

// ============== 鼠标事件 -> 坐标转换 ==============
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

// ============== Handle 命中检测 (v2.2.0 新增) ==============
const HANDLE_SIZE = 6  // 像素
function getHandles(b: BBox): Record<ResizeHandle, { x: number; y: number }> {
  // 返回 8 个 handle 的像素坐标
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

// ============== 鼠标事件处理 ==============
function onMouseDown(e: MouseEvent) {
  const p = eventToImage(e)
  if (mode.value === 'draw') {
    drawing.value = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }
    return
  }
  // edit 模式
  const pn = pixelToNorm(p)
  // 1) 先查 handle (仅在选中 bbox 上查)
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
  // 2) 命中 body -> 选中 + 准备拖动
  const hitIdx = findHitIndex(pn)
  selectIndex(hitIdx)
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
  if (drawing.value) {
    const p = eventToImage(e)
    drawing.value.x1 = p.x
    drawing.value.y1 = p.y
    draw()
    return
  }
  if (dragging.value) {
    const p = eventToImage(e)
    handleDrag(p)
    draw()
    return
  }
  // hover: 仅在 edit 模式 + 选中 bbox 时检测 handle
  if (mode.value === 'edit' && selectedIndex.value !== null) {
    const p = eventToImage(e)
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
  // v2.3.0 S10: 离画布时清空坐标浮标
  cursorPos.value = null
  if (drawing.value) {
    const d = drawing.value
    drawing.value = null
    // 归一化 + 排序
    const xMin = Math.min(d.x0, d.x1) / canvasSize.value.w
    const yMin = Math.min(d.y0, d.y1) / canvasSize.value.h
    const xMax = Math.max(d.x0, d.x1) / canvasSize.value.w
    const yMax = Math.max(d.y0, d.y1) / canvasSize.value.h
    // 过滤太小的拖拽 (像素 < 5)
    const wPx = Math.abs(d.x1 - d.x0)
    const hPx = Math.abs(d.y1 - d.y0)
    if (wPx < 5 || hPx < 5) {
      draw(); return
    }
    // 类别必须选
    if (defaultCategoryId.value == null) {
      ElMessage.warning('请先在下方选一个类别')
      draw(); return
    }
    snapshot()
    const next = [
      ...(props.modelValue || []),
      {
        x_min: round(xMin),
        y_min: round(yMin),
        x_max: round(xMax),
        y_max: round(yMax),
        category_id: defaultCategoryId.value,
      },
    ]
    emit('update:modelValue', next)
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
    // 把 orig 转像素坐标
    const o1 = normToPixel({ x: d.orig.x_min, y: d.orig.y_min })
    const o2 = normToPixel({ x: d.orig.x_max, y: d.orig.y_max })
    let nx1 = o1.x, ny1 = o1.y, nx2 = o2.x, ny2 = o2.y
    if (d.handle.includes('w')) nx1 = cur.x
    if (d.handle.includes('e')) nx2 = cur.x
    if (d.handle.includes('n')) ny1 = cur.y
    if (d.handle.includes('s')) ny2 = cur.y
    // 防止反向 (最小 5 像素)
    if (nx2 - nx1 < 5) {
      if (d.handle.includes('w')) nx1 = nx2 - 5
      else nx2 = nx1 + 5
    }
    if (ny2 - ny1 < 5) {
      if (d.handle.includes('n')) ny1 = ny2 - 5
      else ny2 = ny1 + 5
    }
    // 限制在画布内
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
  for (let i = list.length - 1; i >= 0; i--) {  // 倒序, 上层优先
    const b = list[i]
    if (p.x >= b.x_min && p.x <= b.x_max && p.y >= b.y_min && p.y <= b.y_max) {
      return i
    }
  }
  return null
}
function selectIndex(i: number | null) {
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

// ============== 键盘快捷键 (v2.2.0 新增) ==============
function onKey(e: KeyboardEvent) {
  // 避免在 input/textarea 内触发
  const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable) {
    return
  }
  if (e.ctrlKey || e.metaKey) {
    if (e.key === 'z' && !e.shiftKey) {
      e.preventDefault(); undo(); return
    }
    if ((e.key === 'z' && e.shiftKey) || e.key === 'y') {
      e.preventDefault(); redo(); return
    }
  }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selectedIndex.value !== null) {
      e.preventDefault()
      removeSelected()
    }
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

// ============== 保存 / 取消 ==============
function onSave() {
  if (!props.modelValue || props.modelValue.length === 0) {
    ElMessage.warning('至少画一个 bbox')
    return
  }
  initial.value = JSON.stringify(props.modelValue)
  emit('save', props.modelValue)
}

// ============== 渲染 ==============
function draw() {
  const c = canvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  // 清屏
  ctx.clearRect(0, 0, c.width, c.height)
  // 原图
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
  // 已有 bbox
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
    // 标签
    ctx.fillStyle = color
    const label = `#${i + 1} ${catName(b.category_id)}`
    const labelW = 8 + ctx.measureText(label).width
    ctx.fillRect(x, y - 18, labelW, 18)
    ctx.fillStyle = '#fff'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText(label, x + 4, y - 4)
  })
  // 选中 bbox 的 8 个 handle (v2.2.0 新增)
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
  // 绘制中
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

// modelValue 变化时重绘
watch(
  () => props.modelValue,
  () => draw(),
  { deep: true }
)

// ============== 生命周期清理 ==============
onBeforeUnmount(() => {
  imgEl.onload = null
  imgEl.onerror = null
})
</script>

<style scoped>
.det-annotator {
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
.toolbar .hint {
  color: #909399;
  font-size: 12px;
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
.canvas {
  display: block;
  max-width: 100%;
  cursor: crosshair;
  user-select: none;
}
.cat-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.bbox-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.list-title {
  font-size: 12px;
  color: #909399;
  margin-right: 4px;
}
.bbox-tag {
  cursor: pointer;
}
.cat-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: middle;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  align-items: center;
  flex-wrap: wrap;
}
.shortcut-hint {
  display: flex;
  gap: 4px;
  margin-left: auto;
  flex-wrap: wrap;
}
</style>
