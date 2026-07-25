/**
 * useDetectionXBtnPos.ts
 * ===================================================
 * 目标检测 bbox X 删除按钮位置计算 composable
 *   (v3.0.0 Phase M 抽离自 DetectionAnnotator.vue)
 *
 * 职责:
 * 1. 根据当前选中 bbox 位置 + zoom + canvasSize, 计算 X 按钮在 wrap 内的视觉像素位置
 * 2. 使用 clampToWrapBounds 边界检测, 永远落在 wrap 内部, 防止越界遮挡
 *
 * 设计原则:
 * - 纯计算 composable, 不涉及 bbox 操作 / 历史 / 渲染
 * - 跟随 selectedIndex / modelValue / canvasSize / zoom 响应式更新
 * - 父组件将返回值传给 DetectionDeleteOverlay 渲染
 */
import { computed, type Ref } from 'vue'
import type { BBox } from './useDetectionTypes'

/** X 按钮尺寸 (与 CSS .bbox-delete-overlay 一致) */
export const X_BTN_SIZE = 18

/** 边界 padding (距 wrap 边缘至少留的像素) */
export const X_BTN_PADDING = 4

export interface UseDetectionXBtnPosOptions {
  /** canvas 元素 ref (用于计算 stage 偏移) */
  canvasRef: Ref<HTMLCanvasElement | null>
  /** wrap 容器 ref (用于边界检测) */
  wrapRef: Ref<HTMLElement | null>
  /** 当前画布尺寸 */
  canvasSize: Ref<{ w: number; h: number; width: number; height: number }>
  /** 当前缩放倍率 */
  zoom: Ref<number>
  /** 当前选中的 bbox 索引 */
  selectedIndex: Ref<number | null>
  /** props.modelValue */
  modelValue: Ref<BBox[]>
}

/**
 * 边界检测: 限制坐标在 wrap 范围内
 * - 与 utils/canvasLayout 的 clampToWrap 类似, 但接受自定义 wrap 尺寸
 * - 用于响应式 wrap (useCanvasSize) 场景
 */
function clampToWrapBounds(
  x: number, y: number, w: number, h: number,
  wrapW: number, wrapH: number,
  padding: number = X_BTN_PADDING,
): { x: number; y: number } {
  return {
    x: Math.max(padding, Math.min(wrapW - w - padding, x)),
    y: Math.max(padding, Math.min(wrapH - h - padding, y)),
  }
}

export function useDetectionXBtnPos(options: UseDetectionXBtnPosOptions) {
  const {
    canvasRef, wrapRef, canvasSize, zoom,
    selectedIndex, modelValue,
  } = options

  /**
   * X 按钮在 wrap 内的视觉像素位置
   * - bbox 视觉像素坐标 (canvas 内部坐标 * zoom)
   * - canvas-stage 在 canvas-wrap 内的偏移 (居中布局)
   * - 原始位置: bbox 右上角 - 按钮一半
   * - 边界检测: 限制在 wrap 内部, 留 4px padding
   */
  const xBtnPos = computed<{ x: number; y: number } | null>(() => {
    const i = selectedIndex.value
    if (i === null) return null
    const b = modelValue.value?.[i]
    if (!b) return null
    if (!canvasSize.value.w || !canvasSize.value.h) return null
    if (!canvasRef.value || !wrapRef.value) return null
    // bbox 视觉像素坐标 (canvas 内部坐标 * zoom)
    const xIn = b.x_max * canvasSize.value.w * zoom.value
    const yIn = b.y_min * canvasSize.value.h * zoom.value
    // canvas-stage 在 canvas-wrap 内的偏移 (居中布局)
    const stageRect = canvasRef.value.parentElement!.getBoundingClientRect()
    const wrapRect = wrapRef.value.getBoundingClientRect()
    const offsetX = stageRect.left - wrapRect.left
    const offsetY = stageRect.top - wrapRect.top
    // 原始位置: bbox 右上角 - 按钮一半
    const rawX = offsetX + xIn - X_BTN_SIZE / 2
    const rawY = offsetY + yIn - X_BTN_SIZE / 2
    // 边界检测: 限制在 wrap 内部, 留 4px padding
    return clampToWrapBounds(rawX, rawY, X_BTN_SIZE, X_BTN_SIZE, wrapRect.width, wrapRect.height)
  })

  return { xBtnPos }
}
