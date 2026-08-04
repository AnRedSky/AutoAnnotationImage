/**
 * 训练模式联合类型 (与后端 TrainingJob.pretrain_mode 字段对齐)
 * - from_scratch: 微调 (基于 timm ImageNet 预训练权重, 不依赖业务 MV)
 * - incremental:  增量训练 / 再训练 (基于某个已有 ModelVersion 继续)
 * - resume:       继续训练 (继续暂停的同 job, 复用 model_name)
 * - null/undefined: 历史任务 (已由后端回填脚本把 NULL 改为 from_scratch; 若还有未回填的脏数据, fallback 展示为「微调」)
 */
export type PretrainMode = 'from_scratch' | 'incremental' | 'resume'

export interface PretrainModeMeta {
  /** 中文显示名 (列表 chip / 详情字段) */
  label: string
  /** tag 颜色 (Element Plus tag type) */
  type: 'primary' | 'success' | 'warning' | 'info' | 'danger'
  /** tooltip 长描述 */
  desc: string
}

/**
 * v3.0.0 改版: 业务侧把训练模式简化为「微调 / 增量」两类
 * - from_scratch 展示为「微调」(基于 timm ImageNet 等公开预训练权重, 对自定义数据集做微调)
 * - incremental  展示为「增量」(基于业务 ModelVersion 继续训练)
 * - resume       展示为「继续训练」(继续暂停的同 job)
 *
 * 历史 NULL 任务: 业务侧要求统一默认为「微调」, DEFAULT_META 显式给「微调」label
 * (后端运行 migrations/backfill_pretrain_mode_finetune.py 已批量回填 NULL → from_scratch)
 */
export const PRETRAIN_MODE_META: Record<string, PretrainModeMeta> = {
  from_scratch: {
    label: '微调',
    type: 'info',
    desc: '基于 timm ImageNet 等公开预训练权重, 对自定义数据集做微调',
  },
  incremental: {
    label: '增量',
    type: 'success',
    desc: '基于某个已有 ModelVersion 继续训练, 继承其 .pth 权重作为起点',
  },
  resume: {
    label: '继续训练',
    type: 'warning',
    desc: '继续执行暂停的同 job, 复用原 model_name 与全部配置',
  },
}

/**
 * 兜底 meta: 用于未识别的值 / null / undefined / 空字符串
 * v3.0.0 改版: 业务侧要求「历史的训练任务训练模式统一默认为微调」,
 * 这里直接复用 from_scratch 的 label, 保证 UI 始终显示「微调」, 不显示「未知」
 */
const DEFAULT_META: PretrainModeMeta = PRETRAIN_MODE_META.from_scratch

/**
 * 获取训练模式的展示元信息
 * - 永远返回有效 meta
 * - 未知值 / null / undefined / '' 全部回退到 DEFAULT_META (即「微调」)
 */
export function getPretrainModeMeta(value: string | null | undefined): PretrainModeMeta {
  if (!value) return DEFAULT_META
  return PRETRAIN_MODE_META[value] || DEFAULT_META
}

/** 快捷: 仅获取显示名 */
export function pretrainModeLabel(value: string | null | undefined): string {
  return getPretrainModeMeta(value).label
}

/** 快捷: 仅获取 tag type */
export function pretrainModeTagType(value: string | null | undefined): PretrainModeMeta['type'] {
  return getPretrainModeMeta(value).type
}
