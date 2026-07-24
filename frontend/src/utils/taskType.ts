/**
 * 数据集任务类型枚举的显示映射
 * 后端存储: classification | detection | segmentation (英文枚举值, 跨语言稳定)
 * 前端展示: 实际业务含义 (中文, 带图标)
 *
 * v2.5.2 任务类型正式定义 (教学文档/帮助面板用):
 * - classification: 图像分类 (Classification)
 *     任务: 对整张图像进行单一类别判断, 输出 1 个类别标签 + 置信度
 *     输出: (label, confidence) - 整图级别
 *     适用: 「是/不是」「属于哪一类」 (猫/狗/缺陷/正常 等)
 *     不关注目标位置, 只看整张图的语义
 *
 * - detection: 目标检测 (Object Detection)
 *     任务: 识别图中所有感兴趣目标的「位置」+「类别」, 输出多个 bbox
 *     输出: [(x_min, y_min, x_max, y_max, category_id, confidence), ...] - 目标级
 *     适用: 「找出图中所有 X」 (行人/车辆/缺陷 等)
 *     位置是矩形 (axis-aligned bbox), 不画轮廓
 *
 * - segmentation: 图像分割 (Semantic Segmentation)
 *     任务: 对图像「每个像素」进行分类, 输出与原图同尺寸的 mask
 *     输出: H×W 的 mask, 每个像素 = category_id (0=背景, 1..N=前景)
 *     适用: 精细轮廓识别 (医学影像/自动驾驶车道线/工业缺陷 等)
 *     位置是逐像素, 比 bbox 精细, 但标注成本高
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
  /** v2.5.2: 任务类型正式定义 (详细说明, 帮助面板用) */
  definition: string
  /** v2.5.2: 输出粒度 (整图 / 目标 / 像素) */
  output: string
  /** v2.5.2: 典型应用场景 (帮助面板用) */
  scenario: string
}

export const TASK_TYPE_META: Record<string, TaskTypeMeta> = {
  classification: {
    label: '图像分类',
    desc: '整张图片归为 1 个类别, 适用「是/不是」「属于哪一类」场景',
    icon: Picture,
    type: 'primary',
    definition: '对整张图像进行单一类别判断, 输出 1 个类别标签 + 置信度',
    output: '整图级 (label + confidence)',
    scenario: '猫狗分类 / 质检合格判定 / 医学影像良恶性',
  },
  detection: {
    label: '目标检测',
    desc: '框选图中多个目标并分类, 适用「找出并识别」场景',
    icon: Aim,
    type: 'success',
    definition: '识别图中所有感兴趣目标的「位置」+「类别」, 输出多个矩形框 + 类别',
    output: '目标级 (bbox + category + confidence)',
    scenario: '行人车辆检测 / 工业缺陷定位 / 零售商品识别',
  },
  segmentation: {
    label: '图像分割',
    desc: '逐像素分类, 适用精细轮廓识别场景',
    icon: Crop,
    type: 'warning',
    definition: '对图像「每个像素」进行分类, 输出与原图同尺寸的语义 mask',
    output: '像素级 (H×W mask, 每像素 = 类别 ID)',
    scenario: '医学器官分割 / 自动驾驶车道线 / 工业缺陷轮廓',
  },
}

/** fallback: 后端返回未知值时使用 */
const DEFAULT_META: TaskTypeMeta = {
  label: '未知类型',
  desc: '任务类型未识别',
  icon: QuestionFilled,
  type: 'info',
  definition: '未识别的任务类型',
  output: '未知',
  scenario: '请联系管理员检查数据集 task_type 字段',
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

// ============== S7 新增: 任务类型 -> 默认基础模型 ==============
/**
 * 训练页/标注页「基础模型」下拉默认值, 随任务类型动态切换
 * - classification: timm ImageNet 预训练 (efficientnet_b0, 经典)
 * - detection:     yolov8n (轻量, 适合快速验证)
 * - segmentation:  deeplabv3_resnet50 (torchvision 标准实现)
 */
export const DEFAULT_BASE_MODEL: Record<string, string> = {
  classification: 'efficientnet_b0',
  detection: 'yolov8n',
  segmentation: 'deeplabv3_resnet50',
}

/**
 * 获取指定任务类型对应的默认基础模型名
 * @param taskType 后端返回的 task_type 字符串
 * @returns 默认基础模型名; 未知值回退到 classification
 */
export function getDefaultBaseModel(taskType: string | null | undefined): string {
  if (taskType && DEFAULT_BASE_MODEL[taskType]) {
    return DEFAULT_BASE_MODEL[taskType]
  }
  return DEFAULT_BASE_MODEL.classification
}

// ============== S7 新增: Annotator 组件动态分派 ==============
/**
 * 标注器组件的「逻辑组件名」
 * - 配合 <component :is="..."> 做按 task_type 动态渲染
 * - 实际组件在 views/Annotate.vue / components/annotation/* 中定义
 * - 此处仅做字符串常量, 避免循环 import
 */
export const ANNOTATOR_COMPONENT = {
  CLASSIFICATION: 'ClassificationAnnotator',
  DETECTION: 'DetectionAnnotator',
  SEGMENTATION: 'SegmentationAnnotator',
} as const

export type AnnotatorComponentName =
  (typeof ANNOTATOR_COMPONENT)[keyof typeof ANNOTATOR_COMPONENT]

/**
 * 获取指定任务类型对应的标注器组件名
 * - classification -> ClassificationAnnotator (沿用原 Annotate.vue 内联 UI)
 * - detection     -> DetectionAnnotator (canvas 画 bbox)
 * - segmentation  -> SegmentationAnnotator (canvas 画 mask)
 */
export function getAnnotatorComponent(taskType: string | null | undefined): AnnotatorComponentName {
  switch (taskType) {
    case 'detection':
      return ANNOTATOR_COMPONENT.DETECTION
    case 'segmentation':
      return ANNOTATOR_COMPONENT.SEGMENTATION
    case 'classification':
    default:
      return ANNOTATOR_COMPONENT.CLASSIFICATION
  }
}

// ============== S7 新增: 任务类型训练参数提示 ==============
/**
 * 训练页「参数」字段随 task_type 动态显示
 * - classification 字段: epochs / batch_size / learning_rate
 * - detection     字段: + imgsz / iou_threshold / conf_threshold
 * - segmentation  字段: + crop_size / backbone
 */
export interface TrainingParamHint {
  key: string
  label: string
  tip: string
  taskTypes: string[]
}

export const TRAINING_PARAM_HINTS: TrainingParamHint[] = [
  { key: 'epochs',         label: '训练轮数',       tip: '完整遍历训练集的次数, 越大越慢但越精细',           taskTypes: ['classification', 'detection', 'segmentation'] },
  { key: 'batch_size',     label: '批大小',         tip: '单次前向/反向的样本数, 受显存限制',                taskTypes: ['classification', 'detection', 'segmentation'] },
  { key: 'learning_rate',  label: '学习率',         tip: '模型权重更新步长, 越大收敛越快但易震荡',          taskTypes: ['classification', 'detection', 'segmentation'] },
  { key: 'imgsz',          label: '输入尺寸',       tip: 'YOLO 训练/推理方形边长, 越大越精细但显存翻倍',     taskTypes: ['detection'] },
  { key: 'iou_threshold',  label: 'NMS IoU 阈值',   tip: '目标检测 NMS 抑制重叠框, 越大保留越多框',          taskTypes: ['detection'] },
  { key: 'conf_threshold', label: '置信度阈值',     tip: '低于此阈值的检测框会被丢弃',                       taskTypes: ['detection'] },
  { key: 'crop_size',      label: '裁剪尺寸',       tip: 'DeepLab 训练时随机裁剪尺寸',                       taskTypes: ['segmentation'] },
  { key: 'backbone',       label: '骨干网络',       tip: 'DeepLab 分割骨干, resnet50 / resnet101',           taskTypes: ['segmentation'] },
]

/** 给定任务类型, 过滤出适用的训练参数提示 (保持 ORDER 顺序) */
export function getTrainingParamHints(taskType: string | null | undefined): TrainingParamHint[] {
  if (!taskType) return TRAINING_PARAM_HINTS.filter((h) => h.taskTypes.includes('classification'))
  return TRAINING_PARAM_HINTS.filter((h) => h.taskTypes.includes(taskType))
}

// ============== S7 新增: 任务专属指标列 ==============
/**
 * Models.vue 「任务专属指标」字段
 * - classification: accuracy (top-1)
 * - detection:     mAP@0.5 / mAP@0.5:0.95 / precision / recall
 * - segmentation:  mIoU / pixel_accuracy / loss
 */
export const TASK_METRIC_KEYS: Record<string, { key: string; label: string; precision?: number }[]> = {
  classification: [
    { key: 'accuracy', label: '准确率', precision: 2 },
  ],
  detection: [
    { key: 'map_50',    label: 'mAP@0.5',  precision: 4 },
    { key: 'map_50_95', label: 'mAP@0.5:0.95', precision: 4 },
    { key: 'precision', label: 'Precision', precision: 4 },
    { key: 'recall',    label: 'Recall',    precision: 4 },
  ],
  segmentation: [
    { key: 'miou',           label: 'mIoU',         precision: 4 },
    { key: 'pixel_accuracy', label: 'Pixel Acc.',   precision: 4 },
    { key: 'loss',           label: 'Val Loss',     precision: 4 },
  ],
}

/** 任务类型对应的指标列 */
export function getTaskMetricKeys(taskType: string | null | undefined) {
  return TASK_METRIC_KEYS[taskType || 'classification'] || TASK_METRIC_KEYS.classification
}
