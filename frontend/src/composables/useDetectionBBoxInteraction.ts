/**
 * useDetectionBBoxInteraction.ts
 * ===================================================
 * 目标检测 bbox 交互 composable (v3.0.0 Phase M 拆分自 DetectionAnnotator.vue)
 *
 * 职责:
 * 1. bbox 状态管理: selectedIndex / defaultCategoryId / drawing / dragging / hoverHandle
 * 2. 调色板: PALETTE 颜色表 + colorOf / catName 工具
 * 3. 鼠标交互: onMouseDown / onMouseMove / onMouseUp
 *    - 智能模式: 鼠标按下按优先级判定 (handle hit -> resize / bbox hit -> select+move / 空白 -> draw)
 * 4. bbox 操作: selectByIndex / removeAt / changeSelectedCategory / clearAll
 * 5. 命中检测: findHitIndex / hitTestHandle / handleCursor
 * 6. 拖拽逻辑: handleDrag (move + resize)
 * 7. 坐标转换: eventToImage / pixelToNorm / normToPixel
 * 8. 绘制辅助: round / getHandles / HANDLE_SIZE
 *
 * 设计原则:
 * - 智能模式: 不再需要手动切换 mode, 鼠标按下智能判定
 * - 撤销/重做/历史通过 snapshot() 与 undo()/redo() 委托给 useDetectionHistory
 * - 删除走 ElMessageBox.confirm 弹窗确认 (v2.5.5 起)
 * - 坐标存储: 归一化 (0-1), 渲染时转换为 canvasSize 实际像素
 */
import { ref, computed, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { BBox, Category, CanvasSizeRef, CursorPos, PixelPoint, NormPoint } from './useDetectionTypes'
import { round } from './useDetectionTypes'

/** 8 个缩放控制句柄 (resize 命中测试用) */
export type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'

/** 拖动 / 缩放中临时状态 */
export interface DragState {
  kind: 'move' | 'resize'
  handle?: ResizeHandle
  idx: number
  start: PixelPoint
  orig: BBox
}

/** 绘制中临时状态 */
export interface DrawingState {
  x0: number
  y0: number
  x1: number
  y1: number
}

/** handle 视觉尺寸 (与 CSS 一致) */
export const HANDLE_SIZE = 6

/** 调色板 (与 useDetectionAnnotate / 后端 BBoxAnnotation 颜色表一致) */
const PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]

export interface UseDetectionBBoxInteractionOptions {
  /** canvas 元素 ref */
  canvasRef: Ref<HTMLCanvasElement | null>
  /** 当前画布尺寸 */
  canvasSize: CanvasSizeRef
  /** props.modelValue (v-model 双向绑定) */
  modelValue: Ref<BBox[]>
  /** props.categories */
  categories: Ref<Category[]>
  /** 截图后触发重绘 */
  requestRedraw: () => void
  /** 历史记录 snapshot (add/remove/move/resize/change/clear 前调用) */
  snapshot: () => void
  /** 历史记录 undo (返回 bboxes, 由 caller emit) */
  undo: () => any[] | undefined
  /** 历史记录 redo (返回 bboxes, 由 caller emit) */
  redo: () => any[] | undefined
  /** 是否有未保存修改 (用于 undoAll 兜底) */
  dirty: Ref<boolean>
  /** 重置 initial (save / 切图时) */
  resetInitial: () => void
}

export function useDetectionBBoxInteraction(options: UseDetectionBBoxInteractionOptions) {
  const {
    canvasRef, canvasSize,
    modelValue, categories,
    requestRedraw, snapshot, undo, redo,
    dirty, resetInitial,
  } = options

  // ============== State ==============
  /** 当前选中的 bbox 索引 (null = 无选中) */
  const selectedIndex = ref<number | null>(null)
  /** 画新 bbox 时的默认类别 (右侧目标类型下拉) */
  const defaultCategoryId = ref<number | null>(null)

  /** 绘制中临时状态 */
  const drawing = ref<DrawingState | null>(null)
  /** 拖动/缩放中临时状态 */
  const dragging = ref<DragState | null>(null)
  /** 当前 hover 的 handle (高亮用) */
  const hoverHandle = ref<ResizeHandle | null>(null)

  /** 鼠标位置浮标 (canvas 内部坐标 + 归一化) */
  const cursorPos = ref<CursorPos | null>(null)

  // ============== Computed ==============
  /** 当前选中 bbox 的 category_id (右侧下拉同步用) */
  const selectedCategoryId = computed(() => {
    if (selectedIndex.value === null) return null
    return modelValue.value?.[selectedIndex.value]?.category_id ?? null
  })

  /** 当前选中 bbox 的类别名 (X 按钮 title 用) */
  const selectedBBoxLabel = computed(() => {
    const i = selectedIndex.value
    if (i === null) return ''
    return catName(modelValue.value?.[i]?.category_id)
  })

  // ============== 调色板 / 类别名 ==============
  function colorOf(catId: number | null | undefined): string {
    if (catId == null) return '#909399'
    return PALETTE[Math.abs(Number(catId)) % PALETTE.length]
  }
  function catName(catId: number | null | undefined): string {
    const c = categories.value.find((x) => x.id === catId)
    return c?.name || `cls_${catId}`
  }

  // categories 变化时, 若 defaultCategoryId 未设置, 取第一个
  // (由父组件 watch categories 触发此函数)
  function ensureDefaultCategory() {
    if (defaultCategoryId.value == null && categories.value.length > 0) {
      defaultCategoryId.value = categories.value[0].id
    }
  }

  /** 外部设置 defaultCategoryId (右侧目标类型下拉变更) */
  function setDefaultCategory(id: number | null) {
    defaultCategoryId.value = id
  }

  // ============== 坐标转换 ==============
  /** 鼠标事件 -> canvas 内部像素坐标 */
  function eventToImage(e: MouseEvent): PixelPoint {
    const c = canvasRef.value
    if (!c) return { x: 0, y: 0 }
    const rect = c.getBoundingClientRect()
    return {
      x: (e.clientX - rect.left) * (c.width / rect.width),
      y: (e.clientY - rect.top) * (c.height / rect.height),
    }
  }
  /** canvas 像素 -> 归一化 (0-1) */
  function pixelToNorm(p: PixelPoint): NormPoint {
    return {
      x: Math.max(0, Math.min(1, p.x / canvasSize.value.w)),
      y: Math.max(0, Math.min(1, p.y / canvasSize.value.h)),
    }
  }
  /** 归一化 -> canvas 像素 */
  function normToPixel(n: NormPoint): PixelPoint {
    return { x: n.x * canvasSize.value.w, y: n.y * canvasSize.value.h }
  }

  // ============== Handle 计算 / 命中测试 ==============
  /** 计算 bbox 8 个 handle 的 canvas 像素位置 */
  function getHandles(b: BBox): Record<ResizeHandle, PixelPoint> {
    const x1 = b.x_min * canvasSize.value.w
    const y1 = b.y_min * canvasSize.value.h
    const x2 = b.x_max * canvasSize.value.w
    const y2 = b.y_max * canvasSize.value.h
    const xm = (x1 + x2) / 2
    const ym = (y1 + y2) / 2
    return {
      nw: { x: x1, y: y1 }, n: { x: xm, y: y1 }, ne: { x: x2, y: y1 },
      e:  { x: x2, y: ym }, se: { x: x2, y: y2 }, s: { x: xm, y: y2 },
      sw: { x: x1, y: y2 }, w: { x: x1, y: ym },
    }
  }
  /** 命中测试: 鼠标点击位置是否落在某个 handle 上 */
  function hitTestHandle(p: PixelPoint, b: BBox): ResizeHandle | null {
    const handles = getHandles(b)
    for (const [name, pos] of Object.entries(handles) as [ResizeHandle, PixelPoint][]) {
      if (Math.abs(p.x - pos.x) <= HANDLE_SIZE && Math.abs(p.y - pos.y) <= HANDLE_SIZE) {
        return name
      }
    }
    return null
  }
  /** handle 名称 -> CSS cursor */
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

  // ============== bbox 命中测试 ==============
  /** 命中测试: 鼠标位置是否落在某个 bbox 内 (按 z-order 后绘者优先) */
  function findHitIndex(p: NormPoint): number | null {
    const list = modelValue.value || []
    for (let i = list.length - 1; i >= 0; i--) {
      const b = list[i]
      if (p.x >= b.x_min && p.x <= b.x_max && p.y >= b.y_min && p.y <= b.y_max) {
        return i
      }
    }
    return null
  }

  // ============== bbox 操作 ==============
  /** 选中指定 idx (select + redraw) */
  function selectByIndex(i: number | null) {
    selectedIndex.value = i
    requestRedraw()
  }

  /**
   * v2.5.5: 删除前确认对话框
   * - 确认后: snapshot 入撤销栈 + 删除 + 调整 selectedIndex
   * - 取消: 静默
   */
  async function confirmAndRemove(i: number): Promise<boolean> {
    const list = modelValue.value || []
    if (i < 0 || i >= list.length) return false
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
      return false  // 取消
    }
    // 确认后执行删除
    const next = [...list]
    next.splice(i, 1)
    // 撤销支持
    snapshot()
    modelValue.value = next
    if (selectedIndex.value === i) selectedIndex.value = null
    else if (selectedIndex.value != null && selectedIndex.value > i) selectedIndex.value -= 1
    ElMessage.success(`已删除 #${i + 1} 「${catLabel}」`)
    requestRedraw()
    return true
  }

  /** removeAt 兼容旧引用, 走 confirmAndRemove */
  function removeAt(i: number) {
    return confirmAndRemove(i)
  }

  /** 删除当前选中 bbox */
  function removeSelected() {
    if (selectedIndex.value === null) return
    confirmAndRemove(selectedIndex.value)
  }

  /** 变更当前选中 bbox 的类别 */
  function changeSelectedCategory(catId: number | null) {
    if (selectedIndex.value === null || catId == null) return
    const list = [...(modelValue.value || [])]
    list[selectedIndex.value] = { ...list[selectedIndex.value], category_id: catId }
    // 撤销支持
    snapshot()
    modelValue.value = list
    ElMessage.success(`标签已变更为「${catName(catId)}」`)
    requestRedraw()
  }

  /**
   * v2.5.14: 一键撤销全部未保存修改 (恢复到 last saved 状态)
   * - 替代原 "取消" 按钮: 一次性回到上次保存的 bbox 列表
   * - 没有修改就不响应
   */
  function undoAll() {
    if (!dirty.value) {
      ElMessage.info('当前无未保存修改')
      return
    }
    // 直接 reset initial -> dirty=false
    resetInitial()
    // 清空历史栈, 避免点撤销按钮后又多次触发
    ElMessage.success('已撤销本次所有修改')
  }

  /**
   * v2.5.14 + v2.5.38 + v2.5.41: 清空全部标注 (弹窗确认)
   * - 弹窗让用户确认, 避免误操作
   * - 确认后清空并 snapshot, 允许 Ctrl+Z 撤销此次清空
   * - 「清空」仅清本地草稿, 需点「保存」才会真正从数据库删除
   */
  async function clearAllWithConfirm() {
    const list = modelValue.value || []
    if (list.length === 0) {
      ElMessage.info('当前画布已为空, 无需清空')
      return
    }
    try {
      await ElMessageBox.confirm(
        [
          `确定清空全部 ${list.length} 个标注?`,
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
    } catch {
      return  // 取消
    }
    // 确认后: 推入 undoStack, 允许撤销
    snapshot()
    modelValue.value = []
    selectedIndex.value = null
    requestRedraw()
    ElMessage.success('本地草稿已清空, 请点「保存」删除数据库中的标注')
  }

  // ============== 拖动 / 缩放 ==============
  function handleDrag(cur: PixelPoint) {
    const d = dragging.value!
    const list = [...(modelValue.value || [])]
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
    // move/resize 不入 snapshot (会爆栈), 仅在 mouseup 时入一次
    modelValue.value = list
  }

  // ============== 鼠标事件 ==============
  /**
   * v2.5.4: 智能模式 — 鼠标按下按优先级判定操作
   * 1) 命中选中 bbox 的 handle -> resize drag
   * 2) 命中任意已有 bbox -> 选中 + 启动 move drag
   * 3) 点空白处 -> 画新 bbox (无需先切到 draw 模式)
   */
  function onMouseDown(e: MouseEvent) {
    const p = eventToImage(e)
    const pn = pixelToNorm(p)
    // 1) 命中选中 bbox 的 handle -> resize drag
    if (selectedIndex.value !== null) {
      const sel = modelValue.value?.[selectedIndex.value]
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
      const sel = modelValue.value?.[hitIdx]
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
      requestRedraw()
      return
    }
    if (dragging.value) {
      handleDrag(p)
      requestRedraw()
      return
    }
    // v2.5.4: hover handle 高亮 (智能模式, 无论有无选中都生效)
    if (selectedIndex.value !== null) {
      const sel = modelValue.value?.[selectedIndex.value]
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
      if (wPx < 5 || hPx < 5) { requestRedraw(); return }
      if (defaultCategoryId.value == null) {
        ElMessage.warning('请先在右侧选一个类别')
        requestRedraw()
        return
      }
      snapshot()
      const next = [
        ...(modelValue.value || []),
        {
          x_min: round(xMin), y_min: round(yMin),
          x_max: round(xMax), y_max: round(yMax),
          category_id: defaultCategoryId.value,
        },
      ]
      modelValue.value = next
      // v2.5.4: 智能模式 - 画完新 bbox 立即选中
      selectByIndex(next.length - 1)
      requestRedraw()
      return
    }
    if (dragging.value) {
      // 拖动结束后才入 snapshot (避免 mousemove 频繁入栈撑爆)
      snapshot()
      dragging.value = null
      requestRedraw()
    }
  }

  // ============== 对外暴露的 undo/redo 包装 ==============
  /** 触发撤销 (走 useDetectionHistory 拿新 bboxes, 写到 modelValue) */
  function doUndo() {
    const prev = undo()
    if (prev) {
      modelValue.value = prev
      ElMessage.success('已撤销')
      requestRedraw()
    }
  }
  /** 触发重做 */
  function doRedo() {
    const next = redo()
    if (next) {
      modelValue.value = next
      ElMessage.success('已重做')
      requestRedraw()
    }
  }

  return {
    // state
    selectedIndex, defaultCategoryId,
    drawing, dragging, hoverHandle,
    cursorPos,
    // computed
    selectedCategoryId, selectedBBoxLabel,
    // 调色板
    colorOf, catName,
    // 类别
    ensureDefaultCategory, setDefaultCategory,
    // 坐标转换
    eventToImage, pixelToNorm, normToPixel,
    // handle
    getHandles, hitTestHandle, handleCursor,
    // bbox 操作
    findHitIndex, selectByIndex, removeAt, removeSelected,
    changeSelectedCategory, clearAllWithConfirm, undoAll, confirmAndRemove,
    // 鼠标事件
    onMouseDown, onMouseMove, onMouseUp,
    // 撤销重做
    doUndo, doRedo,
  }
}
