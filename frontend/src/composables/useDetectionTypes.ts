/**
 * useDetectionTypes.ts
 * ===================================================
 * 目标检测画布共享类型与工具 (v3.0.0 Phase M 拆分自 DetectionAnnotator.vue)
 *
 * 内容:
 * - BBox / Category / DrawingState / CursorPos 等类型定义
 * - round 数值精度工具
 *
 * 设计目的:
 * - 集中类型定义, 避免循环 import (composables 之间共享类型)
 * - 与后端 BBoxAnnotation 模型字段一致 (x_min/y_min/x_max/y_max/category_id)
 */
import type { Ref } from 'vue'

/** 单个 bbox (与后端 BBoxAnnotation 字段一致) */
export interface BBox {
  id?: number
  x_min: number
  y_min: number
  x_max: number
  y_max: number
  category_id: number
  confidence?: number
}

/** 类别 (从父组件 categories prop 传入) */
export interface Category {
  id: number
  name: string
  color?: string
}

/** 鼠标位置浮标 (canvas 内部像素 + 归一化) */
export interface CursorPos {
  x: number
  y: number
  nx: number
  ny: number
}

/** 画布内部像素坐标 */
export interface PixelPoint { x: number; y: number }

/** 归一化坐标 (0-1) */
export interface NormPoint { x: number; y: number }

/** 画布尺寸 (与 useCanvasSize 一致) */
export interface CanvasSize {
  w: number
  h: number
  width: number
  height: number
}

/** 画布尺寸 ref 类型 (useCanvasSize 返回) */
export type CanvasSizeRef = Ref<CanvasSize>

/**
 * 数值精度工具
 * - bbox 坐标归一化后, 保留 4 位小数 (避免浮点精度问题)
 * - 用于 bbox 坐标的 round (替换原始 Math.round(v) 默认 0 位)
 */
export function round(v: number): number {
  return Math.round(v * 10000) / 10000
}
