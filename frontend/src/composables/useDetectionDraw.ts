/**
 * useDetectionDraw.ts
 * ===================================================
 * 目标检测 canvas 渲染 composable (v3.0.0 Phase M 拆分自 DetectionAnnotator.vue)
 *
 * 职责:
 * 1. 图片加载: loadImage, 监听 props.imageUrl 变化重载
 * 2. canvas 渲染: draw 函数, 包含背景/图片/所有 bbox/handle/drawing
 * 3. 监听 modelValue 变化自动重绘
 * 4. 监听 canvasSize 变化自动重绘 (容器尺寸响应式)
 *
 * 设计原则:
 * - 纯渲染层, 不涉及 bbox 操作 / 鼠标交互 / 历史管理
 * - 坐标系统: bbox 归一化 (0-1) -> canvas 像素 (canvasSize.w/h)
 * - letterbox 渲染: 图片按原始长宽比居中绘制, 空白区域由背景色填充
 * - handle 高亮: hoverHandle 名称匹配的 handle 渲染为橙色 + 1px 放大
 */
import { ref, watch, onMounted, onBeforeUnmount, nextTick, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getImageDrawRect } from '@/utils/canvasLayout'
import type { BBox } from './useDetectionTypes'
import { round } from './useDetectionTypes'
import { HANDLE_SIZE, type ResizeHandle, type DrawingState } from './useDetectionBBoxInteraction'

export interface UseDetectionDrawOptions {
  /** canvas 元素 ref */
  canvasRef: Ref<HTMLCanvasElement | null>
  /** 当前画布尺寸 */
  canvasSize: Ref<{ w: number; h: number; width: number; height: number }>
  /** 当前图片 URL */
  imageUrl: Ref<string>
  /** props.modelValue (v-model 双向绑定) */
  modelValue: Ref<BBox[]>
  /** 当前选中的 bbox 索引 */
  selectedIndex: Ref<number | null>
  /** 正在 hover 的 handle */
  hoverHandle: Ref<ResizeHandle | null>
  /** 当前绘制中的临时状态 */
  drawing: Ref<DrawingState | null>
  /** 调色板颜色函数 (catId -> color) */
  colorOf: (catId: number | null | undefined) => string
  /** 类别名查询函数 (catId -> name) */
  catName: (catId: number | null | undefined) => string
  /** 8 handle 位置计算 (bbox -> {nw, n, ne, e, se, s, sw, w}) */
  getHandles: (b: BBox) => Record<ResizeHandle, { x: number; y: number }>
}

export function useDetectionDraw(options: UseDetectionDrawOptions) {
  const {
    canvasRef, canvasSize, imageUrl, modelValue,
    selectedIndex, hoverHandle, drawing,
    colorOf, catName, getHandles,
  } = options

  // ============== State ==============
  /** 图片对象 (组件生命周期内复用, 避免重复创建) */
  const imgEl = new Image()

  // ============== 加载图片 ==============
  function loadImage() {
    if (!imageUrl.value) return
    imgEl.crossOrigin = 'anonymous'
    imgEl.onload = () => {
      // v2.5.10: 画布尺寸由 useCanvasSize 响应式管理, 不再手动设置
      // 图片按 letterbox 居中渲染到当前画布中 (由 draw() 处理)
      nextTick(() => draw())
    }
    imgEl.onerror = () => ElMessage.error('图片加载失败')
    imgEl.src = imageUrl.value
  }

  // ============== 渲染 ==============
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
    // 绘制所有 bbox
    const list = modelValue.value || []
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
    // 绘制选中 bbox 的 8 handle
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
    // 绘制正在画的新 bbox (虚线)
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

  // ============== Watchers ==============
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

  // modelValue 变化时重绘 (deep watch, 含添加/删除/移动等)
  watch(
    () => modelValue.value,
    () => draw(),
    { deep: true }
  )

  // imageUrl 变化时重载图片
  watch(imageUrl, () => loadImage())

  // ============== 生命周期 ==============
  onMounted(() => loadImage())

  onBeforeUnmount(() => {
    imgEl.onload = null
    imgEl.onerror = null
  })

  return {
    imgEl,
    draw,
    loadImage,
  }
}
