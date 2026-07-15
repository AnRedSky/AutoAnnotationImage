/**
 * 数据集任务类型枚举的显示映射
 * 后端存储: classification | detection | segmentation (英文枚举值, 跨语言稳定)
 * 前端展示: 实际业务含义 (中文, 带图标)
 *
 * - classification: 图像分类 (整张图归为 1 个类别)
 * - detection:     目标检测 (图内多个目标, 框选 + 类别)
 * - segmentation:  图像分割 (逐像素分类, 当前未实现, 仅占位)
 */
import { Picture, Aim, Crop, QuestionFilled } from '@element-plus/icons-vue'
import type { Component } from 'vue'

export interface TaskTypeMeta {
  /** 中文显示名 */
  label: string
  /** 简短描述, 用于 tooltip */
  desc: string
  /** Element Plus icon 组件, 模板里用 <component :is="..."/> 渲染 */
  icon: Component
  /** tag 颜色 */
  type: 'primary' | 'success' | 'warning' | 'info' | 'danger'
}

export const TASK_TYPE_META: Record<string, TaskTypeMeta> = {
  classification: {
    label: '图像分类',
    desc: '整张图片归为 1 个类别, 适用「是/不是」「属于哪一类」场景',
    icon: Picture,
    type: 'primary',
  },
  detection: {
    label: '目标检测',
    desc: '框选图中多个目标并分类, 适用「找出并识别」场景',
    icon: Aim,
    type: 'success',
  },
  segmentation: {
    label: '图像分割',
    desc: '逐像素分类, 适用精细轮廓识别场景 (当前为预留类型)',
    icon: Crop,
    type: 'warning',
  },
}

/** fallback: 后端返回未知值时使用 */
const DEFAULT_META: TaskTypeMeta = {
  label: '未知类型',
  desc: '任务类型未识别',
  icon: QuestionFilled,
  type: 'info',
}

/**
 * 获取任务类型的元信息
 * @param value 后端返回的 task_type 字符串 (可能为 null/undefined/空)
 * @returns 永远返回有效元信息 (未知值返回 DEFAULT_META, 空值返回 classification)
 */
export function getTaskTypeMeta(value: string | null | undefined): TaskTypeMeta {
  if (!value) return TASK_TYPE_META.classification
  return TASK_TYPE_META[value] || DEFAULT_META
}

/** 快捷: 仅获取显示名 */
export function taskTypeLabel(value: string | null | undefined): string {
  return getTaskTypeMeta(value).label
}

/** 创建数据集时的可选值列表 (与后端 Enum 保持一致) */
export const TASK_TYPE_OPTIONS = [
  { value: 'classification', label: TASK_TYPE_META.classification.label },
  { value: 'detection', label: TASK_TYPE_META.detection.label },
  { value: 'segmentation', label: TASK_TYPE_META.segmentation.label },
]
