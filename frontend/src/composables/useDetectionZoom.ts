/**
 * useDetectionZoom.ts
 * ===================================================
 * 目标检测画布缩放 composable (v3.0.0 Phase M 拆分自 DetectionAnnotator.vue)
 *
 * 职责:
 * 1. 维护画布缩放状态 (zoom ref)
 * 2. 提供 zoomIn / zoomOut / zoomReset 控制方法
 * 3. 处理滚轮缩放 (onWheel) — 鼠标滚轮 deltaY < 0 放大, > 0 缩小
 * 4. 计算 zoomPercent (显示用百分比)
 *
 * 设计原则:
 * - 纯状态管理, 不涉及 bbox 操作或 canvas 渲染
 * - 缩放通过 CSS transform: scale(zoom) 应用于 canvas-stage, 不影响坐标计算
 * - 缩放范围 MIN_ZOOM=0.25 ~ MAX_ZOOM=8.0, 避免过大或过小
 * - 滚轮缩放时 wrapRef 已有 overflow: auto, 用户可滚动查看
 */
import { ref, computed, type Ref } from 'vue'

/** 缩放下限 */
export const MIN_ZOOM = 0.25
/** 缩放上限 */
export const MAX_ZOOM = 8.0
/** 缩放步进 (滚轮) */
const WHEEL_FACTOR = 1.15
/** 缩放步进 (按钮) */
const BUTTON_FACTOR = 1.25

export interface UseDetectionZoomOptions {
  /** wrap 容器 ref, 用于滚轮缩放时获取鼠标位置 */
  wrapRef: Ref<HTMLElement | null>
}

export function useDetectionZoom(options: UseDetectionZoomOptions) {
  const { wrapRef } = options

  // ============== State ==============
  /** 缩放倍率 (1.0 = 100%) */
  const zoom = ref(1.0)

  // ============== Computed ==============
  /** 缩放百分比 (用于 UI 显示) */
  const zoomPercent = computed(() => Math.round(zoom.value * 100))

  // ============== Methods ==============
  /** 滚轮缩放 — 鼠标位置不变时缩放 (粗略 pan) */
  function onWheel(e: WheelEvent) {
    e.preventDefault()
    // 鼠标位置局部坐标 (相对 wrapRef 中心) — 当前未使用 (CSS transform 自动 pan)
    const rect = wrapRef.value?.getBoundingClientRect()
    if (!rect) return
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    // 缩放: deltaY < 0 放大
    const factor = e.deltaY < 0 ? WHEEL_FACTOR : 1 / WHEEL_FACTOR
    const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom.value * factor))
    if (newZoom === zoom.value) return
    zoom.value = newZoom
    // 缩放滚轮坐标: 让鼠标位置"对准" (粗略, 不需要 perfect pan)
    // wrapRef 已经有 overflow: auto, 用户可滚动查看
    void cx; void cy
  }

  /** 按钮放大 */
  function zoomIn() {
    zoom.value = Math.min(MAX_ZOOM, zoom.value * BUTTON_FACTOR)
  }

  /** 按钮缩小 */
  function zoomOut() {
    zoom.value = Math.max(MIN_ZOOM, zoom.value / BUTTON_FACTOR)
  }

  /** 按钮重置 (100%) */
  function zoomReset() {
    zoom.value = 1.0
  }

  return {
    // state
    zoom,
    // computed
    zoomPercent,
    // methods
    onWheel,
    zoomIn,
    zoomOut,
    zoomReset,
  }
}
