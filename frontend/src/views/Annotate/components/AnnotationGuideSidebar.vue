<!--
  AnnotationGuideSidebar.vue
  =====================================================
  标注工作台左侧"操作指导"侧栏
  - 按当前任务类型 (classification / detection / segmentation) 展示对应操作指导
  - 当前任务高亮 + 完整步骤, 其它任务折叠为摘要
  - 任务切换时自动滚动到当前任务卡片

  设计原则:
  - 静态文案: 不依赖任何业务 state, 仅按 taskType prop 切换显示
  - 单文件 < 500 行: 文案硬编码在 script, template 仅做渲染
  - 与画布 (el-col span=14) + 右侧面板 (el-col span=7) 配套使用
  - 父级 el-col 固定可视宽度 250px (266px - 16px gutter)
-->
<template>
  <el-card ref="cardRef" class="guide-sidebar" shadow="never" body-style="padding: 12px;">
    <!-- 顶部标题 -->
    <div class="guide-header">
      <el-icon :size="16" color="#409eff"><InfoFilled /></el-icon>
      <span class="guide-title-text">标注操作指导</span>
    </div>

    <!-- 当前任务卡片 (高亮 + 完整内容) -->
    <div
      v-if="currentGuide"
      class="current-guide"
      :class="`task-${currentTaskType}`"
    >
      <div class="current-guide-header">
        <el-tag
          :type="currentGuide.meta.type"
          effect="dark"
          size="small"
        >● {{ currentGuide.meta.label }} (当前任务)</el-tag>
        <span class="current-guide-subtitle">{{ currentGuide.subtitle }}</span>
      </div>

      <div class="current-guide-content">
        <div
          v-for="(section, idx) in currentGuide.sections"
          :key="idx"
          class="guide-section"
        >
          <div class="guide-section-title">
            <el-icon :size="12"><component :is="section.icon" /></el-icon>
            <span>{{ section.title }}</span>
          </div>
          <ul class="guide-section-list">
            <li
              v-for="(line, i) in section.lines"
              :key="i"
              v-html="line"
            />
          </ul>
        </div>
      </div>
    </div>

    <!-- 其它任务摘要 (折叠 + 提示切换) -->
    <el-divider style="margin: 12px 0 8px;">
      <span style="font-size: 11px; color: #909399;">其它任务类型</span>
    </el-divider>
    <div class="other-guides">
      <div
        v-for="g in otherGuides"
        :key="g.taskType"
        class="other-guide-item"
        :title="`点击查看 ${g.meta.label} 完整指导`"
      >
        <el-tag :type="g.meta.type" size="small" effect="plain" class="other-guide-tag">
          {{ g.meta.label }}
        </el-tag>
        <span class="other-guide-summary">{{ g.summary }}</span>
      </div>
      <div class="other-guide-hint">
        💡 切换数据集时, 任务类型自动同步, 本栏内容跟随刷新
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
/**
 * 3 个任务类型的指导文案定义
 * - 静态数据, 与后端 / 前端 state 解耦
 * - 任何一条文案修改只动本文件, 不影响业务逻辑
 *
 * v2.5.46 扩展: 每个任务的 section 进一步细化
 * - 「置信度阈值」section: 解释 AI 预标注阈值的工作机制与调参建议
 * - 「IoU 阈值」section (仅检测): 解释 NMS 中 IoU 的作用
 * - 「本会话统计」section: 解释顶部 6 张统计卡的含义
 * - 「工具模式」section (仅分割): 解释画刷/橡皮/查看模式差异
 */
import { computed, nextTick, ref, watch } from 'vue'
import { InfoFilled, Aim, Brush, View, List, Histogram, Key, SetUp, DataLine, MagicStick, Crop } from '@element-plus/icons-vue'
import { getTaskTypeMeta, type TaskTypeMeta } from '@/utils/taskType'

interface GuideSection {
  icon: any
  title: string
  lines: string[]  // 允许 HTML (kbd 标签)
}

interface Guide {
  taskType: 'classification' | 'detection' | 'segmentation'
  meta: TaskTypeMeta
  subtitle: string   // 一句话定位 (e.g. "看图判类别, AI 给 5 候选")
  sections: GuideSection[]
  summary: string    // 折叠态摘要
}

// ============== 分类任务指导 ==============
const classificationGuide: Guide = {
  taskType: 'classification',
  meta: getTaskTypeMeta('classification'),
  subtitle: '看图判类别, AI 给 Top-5 候选',
  sections: [
    {
      icon: Aim,
      title: '工作流程',
      lines: [
        '① 仔细查看图片内容',
        '② 顶部查看 AI 候选标签 + 置信度 (0~1)',
        '③ 候选可信 → 点<kbd>✓ 确认</kbd>直接采用',
        '④ 候选错误 → 在修正下拉选正确类别',
        '⑤ 点<kbd>下一张</kbd>进入下一张图',
      ],
    },
    {
      icon: Key,
      title: '快捷键',
      lines: [
        '<kbd>Tab</kbd> / <kbd>Enter</kbd> → 下一张',
        '<kbd>Shift+Tab</kbd> → 上一张',
      ],
    },
    {
      icon: MagicStick,
      title: '置信度阈值',
      lines: [
        '决定一张图被 AI 自动落标的<strong>最低可信度</strong>',
        '阈值越高, 越少图被自动标 (但漏标风险低)',
        '阈值越低, 越多图被自动标 (但需要复核的多)',
        '<strong>推荐值</strong>: <kbd>0.6</kbd> (冷启动 0.5, 训练后 0.7+)',
        '调整后会<strong>实时影响</strong> AI 预标注结果, 配合测评功能验证',
      ],
    },
    {
      icon: DataLine,
      title: '本会话统计',
      lines: [
        '顶部 6 张统计卡, 反映<strong>本工作会话</strong>的标注效率',
        '本会话已标 = 确认 + 修正 (两类人工操作合计)',
        '本会话耗时 = 累计标注用时 (含思考与画框时间)',
        '估算 AI 节省 = 按 3 秒/张估算, 与累计耗时对比',
      ],
    },
    {
      icon: InfoFilled,
      title: '注意事项',
      lines: [
        '置信度 <strong>≥ 0.8</strong> 时可直接确认',
        '候选均在项目类目外 (基础模型 ImageNet 输出) → 必须人工下拉选',
        '所有时间成本自动累计到顶部「本会话耗时」',
      ],
    },
  ],
  summary: '单标签分类, 5 候选, 直接确认或下拉修正',
}

// ============== 检测任务指导 ==============
const detectionGuide: Guide = {
  taskType: 'detection',
  meta: getTaskTypeMeta('detection'),
  subtitle: '多目标矩形框定位 + 分类',
  sections: [
    {
      icon: Aim,
      title: '工作流程',
      lines: [
        '① 选择<strong>目标类型</strong> (画新 bbox 时使用)',
        '② 在图像上<strong>拖动</strong>画矩形框包围目标',
        '③ 拖 bbox 角点<strong>缩放</strong>, 拖 body<strong>平移</strong>',
        '④ 点 bbox 标签<strong>改类别</strong> (弹下拉)',
        '⑤ 点 × 按钮<strong>删除</strong> (需确认)',
        '⑥ 满意后点<kbd>保存</kbd>提交到后端 (新增/删除/修改/全清空均用此按钮)',
      ],
    },
    {
      icon: Key,
      title: '快捷键',
      lines: [
        '<kbd>Ctrl+Z</kbd> → 撤销',
        '<kbd>Ctrl+Y</kbd> → 重做',
        '<kbd>Delete</kbd> → 删除选中 bbox',
        '<kbd>N</kbd> → 保存并下一张',
        '<kbd>B</kbd> → 上一张',
      ],
    },
    {
      icon: MagicStick,
      title: '置信度阈值 (检测)',
      lines: [
        '控制 AI 自动框的<strong>最低置信度</strong>',
        '每张图每个 bbox 都有独立置信度 (0~1)',
        '落标逻辑: <strong>至少 1 个 bbox 置信度 ≥ 阈值</strong>且<strong>类目在项目内</strong>',
        '推荐值 <kbd>0.4</kbd>~<kbd>0.5</kbd> (检测比分类更宽容, 低阈值配合高 IoU 可获召回)',
      ],
    },
    {
      icon: Crop,
      title: 'IoU 阈值 (NMS)',
      lines: [
        '<strong>IoU</strong> = 两个框的交并比, 用于<strong>非极大值抑制 (NMS)</strong>',
        'IoU 高 → 重复框被抑制得更狠, 留下<strong>数量更少但更准</strong>的框',
        'IoU 低 → 重复框被抑制得少, 留下<strong>数量更多</strong>的框 (可能误检)',
        '<strong>推荐值</strong>: <kbd>0.45</kbd> (YOLO 默认; 重叠目标用 <kbd>0.6</kbd>+)',
        '仅 AI 自动预标注生效, 人工画框不受影响',
      ],
    },
    {
      icon: List,
      title: '智能建议',
      lines: [
        '<strong>智能建议</strong>: 来自后端 v2.2 跨图统计, 自动推荐本图可能的目标',
        '<strong>跨图复制</strong>: 复制同数据集其它图的 bbox 位置作为参考',
        '不满意建议 → 点「忽略建议」, 自行标注',
      ],
    },
    {
      icon: DataLine,
      title: '本会话统计',
      lines: [
        '顶部 6 张统计卡, 反映<strong>本工作会话</strong>的标注效率',
        '本会话已标 = 确认 + 修正的<strong>图数</strong> (不是 bbox 数)',
        '本会话耗时 = 累计标注用时 (画框 + 调整 + 保存)',
      ],
    },
    {
      icon: InfoFilled,
      title: '注意事项',
      lines: [
        'bbox <strong>归一化坐标</strong> (0~1), 跨分辨率缩放无影响',
        '拖空白处即画新框, 无需切换"画框模式"',
        '未保存离开会<strong>自动保存</strong>, 避免数据丢失',
      ],
    },
  ],
  summary: '拖框 + 选类别, 缩放/平移/撤销, 智能建议可参考',
}

// ============== 分割任务指导 ==============
const segmentationGuide: Guide = {
  taskType: 'segmentation',
  meta: getTaskTypeMeta('segmentation'),
  subtitle: '像素级区域分割 + 类别标注',
  sections: [
    {
      icon: Aim,
      title: '工作流程',
      lines: [
        '① 选择<strong>工具模式</strong> (画刷/橡皮/查看)',
        '② 选择<strong>画刷类别</strong> (要标注的目标类别)',
        '③ 调整<strong>笔刷大小</strong> (2~40px)',
        '④ 在目标区域<strong>按住鼠标涂抹</strong>, 释放停止',
        '⑤ 误涂区域切到<strong>橡皮</strong>擦除像素',
        '⑥ 满意后点<kbd>保存</kbd>提交 PNG mask',
      ],
    },
    {
      icon: SetUp,
      title: '工具模式',
      lines: [
        '<strong>画刷 (B)</strong>: 在目标区域<strong>写入</strong>当前类别的像素',
        '<strong>橡皮 (E)</strong>: 擦除<strong>任意类别</strong>的像素, 恢复为背景 (0)',
        '<strong>查看 (V)</strong>: <strong>只读</strong>模式, 可拖动画布/缩放, 不修改像素',
        '模式切换保留笔刷大小设置, 避免重复调参',
      ],
    },
    {
      icon: Key,
      title: '快捷键',
      lines: [
        '<kbd>B</kbd> → 画刷模式',
        '<kbd>E</kbd> → 橡皮模式',
        '<kbd>V</kbd> → 查看模式 (拖动画布)',
        '<kbd>[</kbd> / <kbd>]</kbd> → 减小/增大笔刷',
      ],
    },
    {
      icon: MagicStick,
      title: '置信度阈值 (分割)',
      lines: [
        '分割任务的置信度 = 像素维 softmax 的<strong>最大值</strong>',
        '落标逻辑: <strong>max_softmax ≥ 阈值</strong>即自动标为 ai_labeled',
        '但即使未达阈值, mask 仍会写入供人工精修, 避免重复推理',
        '<strong>推荐值</strong>: <kbd>0.5</kbd>~<kbd>0.7</kbd> (分割边界模糊, 阈值不宜过高)',
      ],
    },
    {
      icon: Histogram,
      title: '像素与图层',
      lines: [
        'mask 存为<strong>单通道 PNG</strong>, 像素值 = 类别 ID',
        '前端按画刷类别动态染色 (与右侧调色板一致)',
        '<strong>未保存</strong> 状态下, 离开页面会<strong>自动保存</strong>',
      ],
    },
    {
      icon: DataLine,
      title: '本会话统计',
      lines: [
        '顶部 6 张统计卡, 反映<strong>本工作会话</strong>的标注效率',
        '本会话已标 = 确认 + 修正的<strong>图数</strong> (mask 完整保存即算)',
        '本会话耗时 = 累计涂抹 + 精修 + 保存用时',
      ],
    },
    {
      icon: InfoFilled,
      title: '注意事项',
      lines: [
        '涂到画布边界外的部分会被裁剪',
        '笔刷<strong>中心点</strong>决定该像素的类别',
        '切换 dataset 时会清空历史栈, 重新从第一张开始',
      ],
    },
  ],
  summary: '画刷涂抹 + 选类别, 橡皮纠错, 像素级保存',
}

// ============== props + computed ==============
const props = defineProps<{
  /** 当前任务类型 (来自父组件 dataset.task_type, 默认 classification) */
  taskType: 'classification' | 'detection' | 'segmentation'
}>()

const allGuides: Guide[] = [classificationGuide, detectionGuide, segmentationGuide]

const currentGuide = computed<Guide | undefined>(() =>
  allGuides.find((g) => g.taskType === props.taskType)
)

const currentTaskType = computed(() => props.taskType)

const otherGuides = computed<Guide[]>(() =>
  allGuides.filter((g) => g.taskType !== props.taskType)
)

// ============== 滚动行为 ==============
// 任务类型切换时, 自动将卡片内容滚到顶部, 避免用户停留在上一任务的中间位置
const cardRef = ref<InstanceType<typeof import('element-plus')['ElCard']> | null>(null)

const scrollToTop = () => {
  const body = (cardRef.value?.$el as HTMLElement | undefined)?.querySelector?.('.el-card__body') as HTMLElement | null
  if (body) body.scrollTop = 0
}

watch(() => props.taskType, () => {
  // 等待 DOM 切换 + 高度重算完成后再滚, 防止滚到旧高度
  nextTick(scrollToTop)
})
</script>

<style scoped>
/* 卡片整体: 跟随父行高, 与画布/右栏三列同高
   关键: 不再使用 viewport max-height, 否则会与 el-row 的 stretch 行为冲突 */
.guide-sidebar {
  font-size: 12px;
  line-height: 1.5;
  height: 100%;
  display: flex;
  flex-direction: column;
  /* 子项 overflow 必备, 否则 flex 子项会撑出容器 */
  min-height: 0;
}
/* body 内部滚动 + 自定义细滚动条 */
.guide-sidebar :deep(.el-card__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scroll-behavior: smooth;
  /* Firefox 细滚动条 */
  scrollbar-width: thin;
  scrollbar-color: #dcdfe6 transparent;
}
/* WebKit / Blink (Chrome/Edge/Safari) 细滚动条 */
.guide-sidebar :deep(.el-card__body)::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.guide-sidebar :deep(.el-card__body)::-webkit-scrollbar-track {
  background: transparent;
}
.guide-sidebar :deep(.el-card__body)::-webkit-scrollbar-thumb {
  background: #dcdfe6;
  border-radius: 3px;
  transition: background 0.2s;
}
.guide-sidebar :deep(.el-card__body)::-webkit-scrollbar-thumb:hover {
  background: #909399;
}
.guide-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-bottom: 8px;
  border-bottom: 1px dashed #ebeef5;
  margin-bottom: 8px;
  /* 标题始终钉在顶部, 不随内部内容滚动 */
  flex-shrink: 0;
}
.guide-title-text {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

/* 当前任务卡片 */
.current-guide {
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 4px;
}
.current-guide.task-classification { background: #f0f9ff; border-left: 3px solid #409eff; }
.current-guide.task-detection      { background: #fdf6ec; border-left: 3px solid #e6a23c; }
.current-guide.task-segmentation   { background: #f0f9eb; border-left: 3px solid #67c23a; }

.current-guide-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.current-guide-subtitle {
  font-size: 11px;
  color: #606266;
  font-weight: normal;
}

.current-guide-content {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.guide-section { }
.guide-section-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 1px;
}
.guide-section-list {
  list-style: none;
  padding-left: 16px;
  margin: 0;
}
.guide-section-list li {
  font-size: 11.5px;
  color: #606266;
  line-height: 1.6;
  position: relative;
}
.guide-section-list li::before {
  content: '·';
  position: absolute;
  left: -10px;
  color: #c0c4cc;
}
.guide-section-list li :deep(strong) {
  color: #303133;
  font-weight: 600;
}
.guide-section-list li :deep(kbd) {
  display: inline-block;
  padding: 0 4px;
  font-size: 10.5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #409eff;
  background: #fff;
  border: 1px solid #dcdfe6;
  border-radius: 3px;
  box-shadow: 0 1px 0 #dcdfe6;
  margin: 0 1px;
}

/* 其它任务摘要 */
.other-guide-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 3px;
  cursor: default;
  margin-bottom: 4px;
}
.other-guide-item:hover {
  background: #f5f7fa;
}
.other-guide-tag {
  flex-shrink: 0;
}
.other-guide-summary {
  font-size: 11px;
  color: #909399;
  line-height: 1.5;
}
.other-guide-hint {
  font-size: 10.5px;
  color: #c0c4cc;
  text-align: center;
  padding: 4px 0 0;
  font-style: italic;
}
</style>
