<!--
  DetectionAnnotator.vue (v2.5.8 固定画布 + 边界检测)
  =================================================
  目标检测 bbox 画布组件 (只负责画布渲染 + 鼠标交互)

  v2.5.8 关键变更 (画布布局优化):
  - 画布尺寸固定为 600×400 (CANVAS_W × CANVAS_H), 不再随图片内容调整
  - 外层 wrap 固定 600×480 (WRAP_W × WRAP_H), 上下各 40px 安全区
  - 图片按原始长宽比 letterbox 居中渲染, 空白区域由背景色填充
  - 顶部 40px 安全区: 缩放控制条 / 模式徽章 / 智能标注提示
  - 底部 40px 安全区: 坐标浮标 / 画布尺寸
  - bbox 右上角 X 按钮新增边界检测, 防止靠近边缘时越界
  - 文案/overlay 元素永远落在预设安全区内, 不与核心图片区域重叠

  v2.5.4 智能模式: 鼠标按下智能判定 (draw / select / drag), 无需切模式
  v2.5.5: bbox 删除走确认对话框, 所有变更支持撤销
  v2.5.1 极简版: 移除所有标注操作 UI, 统一由父组件触发

  职责:
  - 加载原图到固定画布 (含 letterbox 缩放)
  - 鼠标拖拽绘制新 bbox (mousedown -> mousemove -> mouseup)
  - 渲染已有 bbox 列表 (颜色按 category_id 分配)
  - 选中 / 删除 / 改类别 bbox (通过父组件调方法)
  - 拖拽整体 bbox + 8 handle 缩放
  - 画布坐标浮标 + 尺寸提示 (固定在底部安全区)

  设计原则:
  - 零业务耦合: 不直接调 API, 不引 store
  - 坐标存储: 归一化 (0-1), 与后端 BBoxAnnotation 一致
  - 渲染坐标系: 实际像素 (固定 canvas size), 由组件内部转换
  - 单向数据流: 画布尺寸/安全区常量从 utils/canvasLayout 统一导入, 三个 annotator 共用

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

  Expose:
    setMode(m) / mode / canUndo / canRedo / undo() / redo() / clearAllWithConfirm() / undoAll()
    removeSelected() / changeSelectedCategory(catId) / selectByIndex(i) / selectedIndex
    defaultCategoryId
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
      <!-- v2.3.2: 缩放控制条 (顶部中间) -->
      <div class="zoom-overlay">
        <el-button-group size="small">
          <el-button @click="zoomOut" :icon="ZoomOut" circle />
          <el-button @click="zoomReset" plain style="min-width: 64px;">{{ zoomPercent }}%</el-button>
          <el-button @click="zoomIn" :icon="ZoomIn" circle />
        </el-button-group>
      </div>
      <!-- v2.5.10: 整合信息面板 (右上角) — 画布坐标浮标 + 画布尺寸 统一整合
           紧凑布局, 两行信息 (坐标行 + 尺寸行), 占据图片右上角小区域
           半透明深色背景, 不喧宾夺主, 关键内容仍可正常查看 -->
      <div class="info-panel">
        <div class="info-row coord-row">
          <span v-if="cursorPos" class="coord-text">
            x: <b>{{ cursorPos.x.toFixed(0) }}</b>
            <span class="coord-pct">({{ (cursorPos.nx * 100).toFixed(1) }}%)</span>
            <span class="info-sep">·</span>
            y: <b>{{ cursorPos.y.toFixed(0) }}</b>
            <span class="coord-pct">({{ (cursorPos.ny * 100).toFixed(1) }}%)</span>
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
      <!-- v2.5.4: 智能标注模式徽章 (左上角) -->
      <div class="mode-overlay mode-smart">
        智能标注模式 · 拖空白画新 / 点 bbox 选中
      </div>
      <!-- v2.5.5: bbox 标签右上角 X 删除按钮 (DOM overlay, 跟随选中 bbox 位置)
           v2.5.10: 使用 clampToWrap 边界检测, 永远落在 wrap 内部, 防止越界 -->
      <div
        v-if="detXBtnPos"
        class="bbox-delete-overlay"
        :style="{ left: detXBtnPos.x + 'px', top: detXBtnPos.y + 'px' }"
        :title="`删除 #${(selectedIndex ?? 0) + 1}「${selectedBBoxLabel}」`"
        @click.stop="confirmAndRemove(selectedIndex ?? 0)"
      >
        <el-icon><Close /></el-icon>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ZoomIn, ZoomOut, Close } from '@element-plus/icons-vue'
// v2.5.10: 响应式画布尺寸 (跟随 wrap 容器变化) + letterbox 渲染 + 边界检测
import { getImageDrawRect, useCanvasSize } from '@/utils/canvasLayout'

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
// v2.5.4: 智能模式 — 移除 mode ref, 鼠标按下智能判定操作 (draw / select / drag)
const selectedIndex = ref<number | null>(null)
const defaultCategoryId = ref<number | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const imgEl = new Image()
// v2.5.10: 画布尺寸响应式 — 由 useCanvasSize 监听 wrap 容器变化
// - 容器尺寸变化时自动同步, 窗口/侧栏调整都会触发重绘
// - 切换图片时: 图片按原始长宽比 letterbox 渲染到当前画布中
// - 初始 fallback 600×400 (来自 canvasLayout 常量, 避免初始化时尺寸为 0)
const { size: canvasSize } = useCanvasSize({ containerRef: wrapRef })

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

/**
 * v2.5.14: 一键撤销全部未保存修改 (恢复到 last saved 状态)
 * - 替代原右侧 "取消" 按钮: 一次性回到上次保存的 bbox 列表
 * - 内部直接调用 resetInitial + 清空历史栈, 避免循环 undo
 * - 没有修改就不响应
 * - 快捷键 Ctrl+Z 仍然走单步 undo(), 此方法专给按钮调用
 */
function undoAll() {
  if (!dirty.value) {
    ElMessage.info('当前无未保存修改')
    return
  }
  // 直接 reset initial -> dirty=false
  resetInitial()
  // 清空历史栈, 避免点撤销按钮后又多次触发
  undoStack.value = []
  redoStack.value = []
  ElMessage.success('已撤销本次所有修改')
}

/**
 * v2.5.14: 清空全部标注 (弹窗确认)
 * - 替代原右侧 "清空未保存" 按钮: 一次性清空当前 draft
 * - 弹窗让用户确认, 避免误操作
 * - 确认后清空并 snapshot, 允许 Ctrl+Z 撤销此次清空
 * - 同样给按钮调用, 不绑定快捷键
 *
 * v2.5.38: 弹窗文案调整 — 明确告知用户「清空」后还需要点「保存」才能
 *   真正从后端删除已标注的 bbox, 避免「以为已经清掉」但实际后端仍有数据。
 *   - 本函数只清本地草稿, 标记 dirty=true, 配合「保存」按钮提交后端
 *   - 「清空」本身不会向后端发送任何请求
 *
 * v2.5.41: 弹窗文案适配统一「保存」按钮
 * - 之前: 提示「需点「保存 (0)」才能从数据库删除」
 * - 现在: 按钮文字已统一为「保存」, 同步去掉 "(0)" 后缀, 避免和新版按钮不一致
 */
async function clearAllWithConfirm() {
  if (!props.modelValue || props.modelValue.length === 0) {
    ElMessage.info('当前画布已为空, 无需清空')
    return
  }
  try {
    await ElMessageBox.confirm(
      [
        `确定清空全部 ${props.modelValue.length} 个标注?`,
        '',
        '「清空」仅清空本地草稿, 需点「保存」才会真正从数据库删除。',
        '若误操作, 可点击「撤销本次修改」恢复到清空前状态。',
      ].join('\n'),
      '清空确认',
      {
        type: 'warning',
        confirmButtonText: '清空 (本地草稿)',
        cancelButtonText: '取消',
      }
    )
    // 确认后: 推入 undoStack, 允许撤销
    snapshot()
    emit('update:modelValue', [])
    selectedIndex.value = null
    draw()
    ElMessage.success('本地草稿已清空, 请点「保存」删除数据库中的标注')
  } catch {
    // 用户取消弹窗, 不做任何处理
  }
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

// v2.5.5: bbox 标签右上角 X 按钮位置 (DOM overlay, 放在 canvas-wrap 内 canvas-stage 外)
// canvas-stage 用 transform: scale(zoom), 缩放后 bbox 在 canvas-wrap 坐标系下的视觉像素位置:
// (x_max * canvasSize.w * zoom, y_min * canvasSize.h * zoom)
// 但 canvas-stage 在 canvas-wrap 内居中, 所以还要加上 canvas-stage 相对 canvas-wrap 的偏移
// X 按钮 16x16 大小固定, 用 transform: scale(1/zoom) 反向缩放, 抵消 canvas-stage 的 scale(zoom)
// v2.5.8: 边界检测 — 当 bbox 靠近画布边缘时, X 按钮可能超出 wrap 范围
//          使用 clampToWrap 强制限制在 wrap 边界内, 防止越界遮挡
// v2.5.10: wrap 尺寸响应式 (useCanvasSize), X 按钮的边界检测使用 canvasSize 实时尺寸
const X_BTN_SIZE = 18  // X 按钮尺寸 (与 CSS .bbox-delete-overlay 一致)
const detXBtnPos = computed<{ x: number; y: number } | null>(() => {
  const i = selectedIndex.value
  if (i === null) return null
  const b = props.modelValue?.[i]
  if (!b) return null
  if (!canvasSize.value.w || !canvasSize.value.h) return null
  if (!canvasRef.value || !wrapRef.value) return null
  // bbox 视觉像素坐标 (canvas 内部坐标 * zoom)
  const xIn = b.x_max * canvasSize.value.w * zoom.value
  const yIn = b.y_min * canvasSize.value.h * zoom.value
  // canvas-stage 在 canvas-wrap 内的偏移 (居中布局)
  // v2.5.10: stage 现在使用 100% 填充 wrap, 偏移为 0
  const stageRect = canvasRef.value.parentElement!.getBoundingClientRect()
  const wrapRect = wrapRef.value.getBoundingClientRect()
  const offsetX = stageRect.left - wrapRect.left
  const offsetY = stageRect.top - wrapRect.top
  // 原始位置: bbox 右上角 - 按钮一半
  const rawX = offsetX + xIn - X_BTN_SIZE / 2
  const rawY = offsetY + yIn - X_BTN_SIZE / 2
  // 边界检测: 限制在 wrap 内部, 留 4px padding
  // v2.5.10: 使用 wrapRect 的实时尺寸而非常量
  return clampToWrapBounds(rawX, rawY, X_BTN_SIZE, X_BTN_SIZE, wrapRect.width, wrapRect.height, 4)
})

/**
 * 边界检测: 限制坐标在 wrap 范围内
 * - 与 utils/canvasLayout 的 clampToWrap 类似, 但接受自定义 wrap 尺寸
 * - 用于响应式 wrap (useCanvasSize) 场景
 */
function clampToWrapBounds(
  x: number, y: number, w: number, h: number,
  wrapW: number, wrapH: number,
  padding: number = 4,
): { x: number; y: number } {
  return {
    x: Math.max(padding, Math.min(wrapW - w - padding, x)),
    y: Math.max(padding, Math.min(wrapH - h - padding, y)),
  }
}
// v2.5.5: 选中 bbox 类别名 (X 按钮 title 用)
const selectedBBoxLabel = computed(() => {
  const i = selectedIndex.value
  if (i === null) return ''
  return catName(props.modelValue?.[i]?.category_id)
})

// v2.5.10: 画布尺寸响应式 — 尺寸变化时自动重绘
// - 容器 resize / 窗口缩放 / 侧栏展开折叠 都会触发
// - nextTick 等待 DOM 更新完成, 再调用 draw 拿到正确 canvas 尺寸
watch(
  () => [canvasSize.value.w, canvasSize.value.h],
  () => {
    nextTick(() => draw())
  },
  { flush: 'post' },
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
    // v2.5.10: 画布尺寸由 useCanvasSize 响应式管理, 不再手动设置
    // 图片按 letterbox 居中渲染到当前画布中 (由 draw() 处理)
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
  const pn = pixelToNorm(p)
  // v2.5.4: 智能模式 — 鼠标按下按优先级判定操作
  // 1) 命中选中 bbox 的 handle -> resize drag
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
  // 2) 命中任意已有 bbox -> 选中 + 启动 move drag
  const hitIdx = findHitIndex(pn)
  if (hitIdx !== null) {
    selectByIndex(hitIdx)
    const sel = props.modelValue?.[hitIdx]
    if (sel) {
      dragging.value = {
        kind: 'move', idx: hitIdx,
        start: p, orig: { ...sel },
      }
    }
    return
  }
  // 3) 点空白处 -> 画新 bbox (无需先切到 draw 模式)
  if (defaultCategoryId.value == null) {
    ElMessage.warning('请先在右侧选一个类别')
    return
  }
  drawing.value = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }
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
  // v2.5.4: hover handle 高亮, 无论有无模式都生效 (智能模式)
  if (selectedIndex.value !== null) {
    const sel = props.modelValue?.[selectedIndex.value]
    const h = sel ? hitTestHandle(p, sel) : null
    if (h !== hoverHandle.value) {
      hoverHandle.value = h
      if (canvasRef.value) canvasRef.value.style.cursor = handleCursor(h)
    }
  } else {
    // 无选中时: crosshair 提示可画新 bbox
    if (canvasRef.value) canvasRef.value.style.cursor = 'crosshair'
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
    // v2.5.4: 智能模式 - 画完新 bbox 立即选中, 8 handle 立即可见, 无需切模式
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
  // v2.5.5: 走确认对话框, 防止误删 (用户需求: 标签删除前弹确认对话框)
  return confirmAndRemove(i)
}
function removeSelected() {
  if (selectedIndex.value === null) return
  confirmAndRemove(selectedIndex.value)
}
function changeSelectedCategory(catId: number | null) {
  if (selectedIndex.value === null || catId == null) return
  const list = [...(props.modelValue || [])]
  list[selectedIndex.value] = { ...list[selectedIndex.value], category_id: catId }
  // v2.5.5: 撤销支持 (用户需求: 标签变更需支持撤销)
  snapshot()
  emit('update:modelValue', list)
  ElMessage.success(`标签已变更为「${catName(catId)}」`)
  draw()
}
/**
 * v2.5.5: 删除前确认对话框 (用户需求: 删除操作需弹确认)
 * - 选中 1 个: 显示该 bbox 类别名
 * - 多个: 通用提示
 * - 确认后: snapshot() 入撤销栈 + 删除
 * - 取消: 静默
 */
async function confirmAndRemove(i: number) {
  const list = props.modelValue || []
  if (i < 0 || i >= list.length) return
  const b = list[i]
  const catLabel = catName(b.category_id)
  try {
    await ElMessageBox.confirm(
      `确认删除 #${i + 1} 「${catLabel}」? 此操作可撤销 (Ctrl+Z)`,
      '删除标签',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      }
    )
  } catch {
    return  // 取消
  }
  // 确认后执行删除
  const next = [...list]
  next.splice(i, 1)
  // v2.5.5: 撤销支持 (用户需求: 删除需支持撤销)
  snapshot()
  emit('update:modelValue', next)
  if (selectedIndex.value === i) selectedIndex.value = null
  else if (selectedIndex.value != null && selectedIndex.value > i) selectedIndex.value -= 1
  ElMessage.success(`已删除 #${i + 1} 「${catLabel}」`)
  draw()
}
// v2.5.14: 移除原 clearDraft 方法
// - 原方法被右侧 "清空未保存" 按钮调用 (emit('clear-draft'))
// - 现已替换为 clearAllWithConfirm (弹窗更友好, 提示更清晰)
// - 该方法无其它调用方, 直接删除

// v2.5.4: 合并绘制/编辑为智能模式, 移除 setMode
// 鼠标按下智能判定: 命中 handle -> resize; 命中 bbox -> 选中+move; 空白处 -> 画新 bbox
// 不再需要手动切换模式 (D/E 快捷键已废弃)

// 键盘快捷键 (保留画布强相关: Delete / Ctrl+Z / Y)
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
  // v2.5.4: 移除 d/e 切模式快捷键 (智能模式, 无需切)
  if (k === 'n') emit('next')
  else if (k === 'p') emit('prev')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// 保存 / 取消
function onSave() {
  // v2.5.38 修复: 允许保存空列表 (用于「清空全部」后保存以删除后端标注)
  // - 之前: props.modelValue.length === 0 时直接 ElMessage.warning 拦截,
  //         导致右侧保存按钮在 bboxList 为空时被 disabled,
  //         且即便绕过 disabled, onSave 也会拒绝空列表, 用户无法清空已标注信息
  // - 现在: 走标准 save 流程, 父组件的 saveDetectionBBoxes 会先调 clearBBoxes
  //         (DELETE /api/detection/annotations/clear/{imageId}) 删后端全部 bbox,
  //         再用空列表循环 0 次, 效果等于删除全部已保存标注
  initial.value = JSON.stringify(props.modelValue || [])
  emit('save', props.modelValue || [])
}

// 渲染
function draw() {
  const c = canvasRef.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  // v2.5.8: 画布尺寸固定, 图片按 letterbox 方式渲染到固定画布
  // - 先用背景色填充整个画布
  // - 再按图片原始长宽比缩放并居中绘制, 空白区域保留背景色
  ctx.fillStyle = '#f5f5f5'
  ctx.fillRect(0, 0, c.width, c.height)
  if (imgEl.complete && imgEl.naturalWidth > 0) {
    const rect = getImageDrawRect(imgEl.naturalWidth, imgEl.naturalHeight, c.width, c.height)
    ctx.drawImage(imgEl, rect.x, rect.y, rect.w, rect.h)
  } else {
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
  if (selectedIndex.value !== null) {  // v2.5.4: 智能模式 — 选中即显示 handle
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
// v2.5.4: 智能模式 — 移除 setMode/mode 暴露, 新增 removeAt/removeBBoxAt
// v2.5.5: 所有删除走 confirmAndRemove (带确认对话框)
defineExpose({
  undo,
  redo,
  removeSelected,             // 删除当前选中 (走确认)
  changeSelectedCategory,
  selectByIndex,
  removeAt,                   // 走确认对话框
  removeBBoxAt: removeAt,     // 别名 (兼容旧引用)
  removeAtWithConfirm: confirmAndRemove,  // v2.5.5: 显式命名, 父组件可读性更好
  confirmAndRemove,           // 内部函数直接暴露
  save: onSave,
  resetInitial,  // v2.3.2: 父组件保存后重置 dirty
  canUndo,
  canRedo,
  selectedIndex,
  selectedBBox: computed(() => {  // v2.5.5: 当前选中的 bbox (供父组件算 overlay 位置)
    const i = selectedIndex.value
    if (i === null) return null
    return props.modelValue?.[i] || null
  }),
  defaultCategoryId,
  /** v2.5.13: 父组件 (右侧目标类型下拉) 修改默认类别时调用, 同步给子组件
   *  父组件通过 useDetectionAnnotate 的 onDetTargetCategoryChange 触发
   *  之前用 ?. 静默吞掉调用, 导致选了下拉后画布仍按旧 defaultCategoryId 画框 */
  setDefaultCategory: (id: number | null) => { defaultCategoryId.value = id },
  /** v2.5.14: 一键撤销全部未保存修改 (替代右侧「取消」按钮) */
  undoAll,
  /** v2.5.14: 清空全部标注带确认 (替代右侧「清空未保存」按钮) */
  clearAllWithConfirm,
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
/* v2.5.10: 缩放控制条 (顶部中间) */
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
/* v2.5.10: 整合信息面板 (右上角) — coord + size 合并
   - 紧凑双行布局, 占据图片右上角小区域
   - 半透明深色背景, 不喧宾夺主
   - 与左下角的 mode 徽章和顶部中间的 zoom 控件形成清晰的视觉分区 */
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
  max-width: calc(100% - 16px);  /* 防止窗口过窄时撑出 wrap */
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
  /* 坐标行: 突出关键数字 */
  font-weight: 500;
}
.info-panel .info-row.size-row {
  /* 尺寸行: 略低对比度, 与坐标行拉开层次 */
  opacity: 0.85;
  font-size: 10.5px;
}
.info-panel b {
  color: #67c23a;
  font-weight: 600;
  margin: 0 1px;
}
.info-panel .coord-pct {
  color: #b3d8a8;  /* 浅绿, 与 x/y 数字色 (#67c23a) 区分 */
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
.mode-overlay.mode-edit {
  background: rgba(230, 162, 60, 0.85);
}
/* v2.5.5: bbox 标签右上角 X 删除按钮 (DOM overlay, 跟随选中 bbox 位置)
   v2.5.10: 位置由 detXBtnPos 通过 clampToWrapBounds 边界检测, 永远落在 wrap 内部 */
.bbox-delete-overlay {
  position: absolute;
  width: 18px;
  height: 18px;
  background: #f56c6c;
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 12px;
  z-index: 20;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.25);
  transition: transform 0.15s ease, background 0.15s ease;
}
.bbox-delete-overlay:hover {
  background: #ff7875;
  transform: scale(1.15);
}
.bbox-delete-overlay .el-icon {
  font-size: 12px;
  pointer-events: none;
}
</style>
