/**
 * canvasLayout.ts — 标注画布固定布局与边界检测工具 (v2.5.10 响应式增强)
 * ===================================================================
 *
 * 目的:
 * 1. 画布尺寸固定, 防止因图片内容/窗口尺寸调整导致画布大小变化
 * 2. 划分文案/overlay 安全区, 防止与核心图片区域发生重叠
 * 3. 提供边界检测工具, 动态元素 (如 bbox 右上角 X 按钮) 也不会越界
 * 4. v2.5.10 新增: useCanvasSize 组合式函数, 支持画布响应式填充容器
 *
 * 布局结构 (固定值, 作为 fallback):
 *   ┌────────────────────────────────────┐ ← wrap: WRAP_W × WRAP_H
 *   │ [zoom / mode badge]                 │ ← 顶部安全区: SAFE_ZONE (40px)
 *   ├────────────────────────────────────┤
 *   │                                    │
 *   │      image (letterbox fitted)      │ ← canvas: CANVAS_W × CANVAS_H
 *   │      + bbox / mask 绘制区          │   (与 wrap 同宽, 居中)
 *   │                                    │
 *   ├────────────────────────────────────┤
 *   │ [coord / size overlay]              │ ← 底部安全区: SAFE_ZONE (40px)
 *   └────────────────────────────────────┘
 *
 * 三种任务 (classification / detection / segmentation) 共享同一套尺寸,
 * 避免切任务时画布尺寸跳动。
 *
 * v2.5.10 响应式增强:
 * - 画布不再写死 600×400, 而是通过 useCanvasSize 监听容器尺寸
 * - wrap 容器使用 width: 100% / height: 100% (含 min-height: 480)
 * - canvas 内部尺寸由 ResizeObserver 同步, 窗口/容器变化时自动重绘
 * - 图片仍按 letterbox 居中渲染 (getImageDrawRect 工具保留)
 * - 文案/overlay 元素集中到顶部, 不再使用上下 40px 安全区
 * - 协调浮标 + 尺寸信息合并为一个 info-panel, 统一在右上角
 */

import { ref, onMounted, onBeforeUnmount, watch, type Ref } from 'vue'

/** 画布固定宽度 (内层 canvas / 实际绘制区域, 单位 px) */
export const CANVAS_W = 600

/** 画布固定高度 (内层 canvas / 实际绘制区域, 单位 px) */
export const CANVAS_H = 400

/** 画布外层 wrap 容器固定宽度 (与 CANVAS_W 同宽, 上下各留 SAFE_ZONE) */
export const WRAP_W = 600

/** 画布外层 wrap 容器固定高度 (含上下两个 SAFE_ZONE) */
export const WRAP_H = 480

/** 顶部/底部安全区高度 (文案/overlay 使用, 单位 px) */
export const SAFE_ZONE = 40

/**
 * 计算图片在固定画布中的 letterbox 绘制区域
 * - 保持图片原始长宽比
 * - 居中渲染, 空白区域由调用方用背景色填充
 *
 * @param imgW   图片原始宽度 (px), 0/负数返回全画布
 * @param imgH   图片原始高度 (px), 0/负数返回全画布
 * @param cw     画布宽度, 默认 CANVAS_W
 * @param ch     画布高度, 默认 CANVAS_H
 * @returns      { x, y, w, h } 实际绘制矩形
 */
export function getImageDrawRect(
  imgW: number,
  imgH: number,
  cw: number = CANVAS_W,
  ch: number = CANVAS_H,
): { x: number; y: number; w: number; h: number } {
  if (!imgW || !imgH) {
    return { x: 0, y: 0, w: cw, h: ch }
  }
  const scale = Math.min(cw / imgW, ch / imgH)
  const drawW = imgW * scale
  const drawH = imgH * scale
  return {
    x: (cw - drawW) / 2,
    y: (ch - drawH) / 2,
    w: drawW,
    h: drawH,
  }
}

/**
 * 边界检测: 把一个点 (x, y) 限制在画布 wrap 内的安全区
 * - 适用于 bbox 右上角 X 按钮等"跟随内容"但不能越界"的动态元素
 * - 边界与外层 wrap 同宽 (WRAP_W × WRAP_H)
 * - 默认 padding=4: 元素到 wrap 边缘至少留 4px
 *
 * @param x        元素左上角 X
 * @param y        元素左上角 Y
 * @param elW      元素宽度
 * @param elH      元素高度
 * @param padding  元素到 wrap 边缘的最小距离, 默认 4px
 * @returns        { x, y } 调整后坐标
 */
export function clampToWrap(
  x: number,
  y: number,
  elW: number,
  elH: number,
  padding: number = 4,
): { x: number; y: number } {
  const minX = padding
  const maxX = WRAP_W - elW - padding
  const minY = padding
  const maxY = WRAP_H - elH - padding
  return {
    x: Math.max(minX, Math.min(maxX, x)),
    y: Math.max(minY, Math.min(maxY, y)),
  }
}

/**
 * 边界检测: 判定一个矩形是否与画布绘制区重叠
 * - 画布绘制区在 wrap 中的位置: y ∈ [SAFE_ZONE, SAFE_ZONE + CANVAS_H]
 * - 用于 overlay 元素的"防遮挡图片"自检
 *
 * @param rect   矩形 {x, y, w, h}
 * @returns      true = 与画布绘制区重叠 (危险, 应避开)
 */
export function isOverlapCanvas(
  rect: { x: number; y: number; w: number; h: number },
): boolean {
  const canvasTop = SAFE_ZONE
  const canvasBottom = SAFE_ZONE + CANVAS_H
  return rect.y < canvasBottom && rect.y + rect.h > canvasTop
}

// ============== v2.5.10 新增: 响应式画布尺寸 Composable ==============

/**
 * 画布尺寸接口 (与 canvasSize ref 兼容)
 * - w/h 字段命名沿用 DetectionAnnotator/SegmentationAnnotator 的旧 canvasSize
 * - width/height 别名字段, 方便 useCanvasSize 消费者直接拿到数字
 */
export interface CanvasBoxSize {
  /** 宽度 (px, >= 1) */
  w: number
  /** 高度 (px, >= 1) */
  h: number
  /** 宽度别名 */
  width: number
  /** 高度别名 */
  height: number
}

/**
 * useCanvasSize 选项
 */
export interface UseCanvasSizeOptions {
  /** 容器元素 ref (e.g. wrapRef) */
  containerRef: Ref<HTMLElement | null>
  /** 容器尚未挂载/尺寸为 0 时的回退宽度, 默认 600 */
  fallbackWidth?: number
  /** 容器尚未挂载/尺寸为 0 时的回退高度, 默认 400 */
  fallbackHeight?: number
  /** ResizeObserver 防抖 (ms), 默认 50 (避免窗口快速 resize 时频繁重绘) */
  debounceMs?: number
  /** 是否在容器尺寸变化时立即触发回调, 默认 true */
  immediate?: boolean
}

/**
 * 响应式画布尺寸 composable
 * - 通过 ResizeObserver 监听容器尺寸变化, 自动更新画布尺寸
 * - 容器尺寸为 0 时使用 fallback 值, 避免初始化阶段尺寸错误
 * - 组件卸载时自动断开观察, 无内存泄漏
 * - 暴露的 size 始终有效 (>= 1), 调用方可直接用于 canvas.width / canvas.height
 *
 * 使用方式:
 *   const wrapRef = ref<HTMLDivElement | null>(null)
 *   const { size } = useCanvasSize({ containerRef: wrapRef })
 *   // watch(size, () => draw(), { deep: true })
 *
 * @param options 配置
 * @returns       { size } 响应式画布尺寸 { w, h, width, height }
 */
export function useCanvasSize(options: UseCanvasSizeOptions) {
  const {
    containerRef,
    fallbackWidth = CANVAS_W,
    fallbackHeight = CANVAS_H,
    debounceMs = 50,
    immediate = true,
  } = options

  const size = ref<CanvasBoxSize>({
    w: fallbackWidth,
    h: fallbackHeight,
    width: fallbackWidth,
    height: fallbackHeight,
  })

  let observer: ResizeObserver | null = null
  let timer: number | null = null

  function measure() {
    const el = containerRef.value
    if (!el) return
    const rect = el.getBoundingClientRect()
    // v2.5.10: 使用 getBoundingClientRect 的实际渲染尺寸
    // 注意: 容器有 padding 时, clientWidth/Height 不含 padding, 但这里画布
    //       容器 (.canvas-wrap) 无 padding, 可直接用 clientWidth/Height
    const w = Math.max(1, Math.floor(rect.width))
    const h = Math.max(1, Math.floor(rect.height))
    if (size.value.w !== w || size.value.h !== h) {
      size.value = { w, h, width: w, height: h }
    }
  }

  function debouncedMeasure() {
    if (timer !== null) {
      window.clearTimeout(timer)
    }
    timer = window.setTimeout(() => {
      timer = null
      measure()
    }, debounceMs)
  }

  onMounted(() => {
    // 首次同步测量
    if (immediate) measure()

    // 注册 ResizeObserver
    if (typeof ResizeObserver !== 'undefined' && containerRef.value) {
      observer = new ResizeObserver(() => debouncedMeasure())
      observer.observe(containerRef.value)
    } else {
      // 降级: 监听 window resize
      window.addEventListener('resize', debouncedMeasure)
    }
  })

  onBeforeUnmount(() => {
    if (observer) {
      observer.disconnect()
      observer = null
    }
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
    if (typeof ResizeObserver === 'undefined') {
      window.removeEventListener('resize', debouncedMeasure)
    }
  })

  // 容器 ref 从 null 变为有效元素时, 立即测量一次
  watch(
    containerRef,
    (el) => {
      if (el && immediate) {
        // nextTick 确保 layout 已完成
        Promise.resolve().then(() => measure())
      }
    },
  )

  return { size }
}
