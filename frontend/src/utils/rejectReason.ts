/**
 * 不合格图片标记原因常量与中文映射
 * v3.0.0 新增：与后端 app/common/enums.py:RejectReason 保持一致
 *
 * 设计: 6 个预设枚举 + "其他"允许自由文本;
 *       枚举值存 Image.reject_reason, 自定义文本存 AnnotationLog.payload.custom_text
 */

export type RejectReasonValue =
  | 'blurry'
  | 'wrong_category'
  | 'duplicate'
  | 'out_of_scope'
  | 'violation'
  | 'other'

export interface RejectReasonOption {
  value: RejectReasonValue
  label: string
}

/** 不合格原因下拉选项（供 el-select 使用） */
export const REJECT_REASON_OPTIONS: RejectReasonOption[] = [
  { value: 'blurry', label: '图片模糊' },
  { value: 'wrong_category', label: '类别错误' },
  { value: 'duplicate', label: '重复图片' },
  { value: 'out_of_scope', label: '非本数据集类别' },
  { value: 'violation', label: '内容违规' },
  { value: 'other', label: '其他' },
]

/** 原因值 → 中文标签映射（供展示用） */
export const REJECT_REASON_LABELS: Record<string, string> =
  REJECT_REASON_OPTIONS.reduce((acc, opt) => {
    acc[opt.value] = opt.label
    return acc
  }, {} as Record<string, string>)

/** 根据原因值获取中文标签 */
export function getRejectReasonLabel(reason: string | null | undefined): string {
  if (!reason) return ''
  return REJECT_REASON_LABELS[reason] || reason
}
