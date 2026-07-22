<!--
  AnnotationCanvas.vue (v2.5.10 响应式填充)
  ======================================================================
  标注工作台左侧画布壳 (slot 模式):
  - 渲染 el-card + 加载/空状态/元信息
  - 由父组件用 slot 传入具体的 annotator (Detection/Segmentation/Classification)
  - 这样父组件 Annotate.vue 仍可直接 ref="detAnnotRef" 指向 DetectionAnnotator

  v2.5.10 响应式填充:
  - 画布壳宽度 100% 填充 el-col, 高度由父容器决定
  - 加载占位 / 元信息都按此自适应
  - 不再写死 600×530 固定尺寸, 跟随容器 (el-col) 实际大小变化
  - 子 annotator 通过 useCanvasSize 监听 wrap 尺寸, 自动调整 canvas 内部分辨率

  Props (页面私有子组件):
    image:    当前图
    loading:  加载中

  Slot:
    default:  父组件传入的 annotator 组件
-->
<template>
  <el-card :title="image ? `待标注图片 #${image.id}` : '待标注图片'" class="annotate-card">
    <div v-if="loading" v-loading="true" class="annotate-loading"></div>
    <div v-else-if="image" class="annotate-canvas">
      <slot />
      <!-- v2.5.10: 元信息 — 独立安全区, 顶部 1px 分割线, 字号 12px, 与画布内容彻底分离 -->
      <div class="canvas-meta">
        <strong>{{ image.filename }}</strong>
        <span class="canvas-meta-divider">|</span>
        尺寸: {{ image.width }}×{{ image.height }}
        <span class="canvas-meta-divider">|</span>
        大小: {{ ((image.file_size || 0) / 1024).toFixed(1) }} KB
        <el-tag
          v-if="image.task_type" size="small" effect="plain"
          :type="getTaskTypeMeta(image.task_type).type" class="canvas-meta-tag"
        >
          {{ getTaskTypeMeta(image.task_type).label }}
        </el-tag>
      </div>
    </div>
    <el-empty v-else description="暂无待标注图片, 可先点「启动 AI 预标注」批量推理" />
  </el-card>
</template>

<script setup lang="ts">
import { getTaskTypeMeta } from '@/utils/taskType'

interface Image {
  id: number
  filename: string
  width: number
  height: number
  file_size?: number
  task_type: string
}

defineProps<{
  image: Image | null
  loading: boolean
}>()
</script>

<style scoped>
/* v2.5.10: el-card 自身充满 el-col
   - 由父级 .el-col (span=14) 提供宽度
   - height: 100% 让 card 跟随 el-col 高度 (由内容决定) */
.annotate-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.annotate-card :deep(.el-card__body) {
  /* card body 也填满 card 高度, 让 .annotate-canvas 有空间撑高 */
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* v2.5.10: 画布壳响应式填充父容器 (card body)
   - 父容器 (el-card body) 由 flex column 主导
   - .annotate-canvas 用 flex: 1 撑满剩余高度 (元信息占底部)
   - 加载占位与画布都按此自适应
   v2.5.12: 移除 min-height: 480px, 改为 height: 100% + overflow: hidden
   · 父级 annotate-main-row 高度已锁 (calc(100vh - 360px))
   · 此处不能再设 min-height, 否则会突破父级高度, 撑大整行
   · 内容超长时, 内部 .canvas-wrap 用 overflow: auto 滚动 */
.annotate-canvas {
  position: relative;
  width: 100%;
  flex: 1 1 auto;
  min-height: 0;        /* 关键: flex 子项需要 min-height: 0 才能正确收缩 */
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  overflow: hidden;
}

/* v2.5.12: 加载占位使用 100% 高度, spinner 居中 (移除 min-height: 480px 兜底) */
.annotate-loading {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* v2.5.10: 元信息条 — 独立安全区, 顶部 1px 分割线
   - 永远位于画布 wrap 下方, 不与画布内容重叠
   - flex-shrink: 0 保证不被压缩 */
.canvas-meta {
  flex-shrink: 0;
  width: 100%;
  margin-top: 0;
  padding-top: 8px;
  border-top: 1px solid #ebeef5;
  color: #909399;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.canvas-meta strong {
  color: #606266;
  font-weight: 600;
}
.canvas-meta-divider {
  color: #dcdfe6;
  margin: 0 2px;
}
.canvas-meta-tag {
  margin-left: 4px;
}
</style>
