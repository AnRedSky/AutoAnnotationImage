<!--
  AnnotationCanvas.vue (v2.5.7 拆分自 Annotate.vue)
  =================================================
  标注工作台左侧画布壳 (slot 模式):
  - 渲染 el-card + 加载/空状态/元信息
  - 由父组件用 slot 传入具体的 annotator (Detection/Segmentation/Classification)
  - 这样父组件 Annotate.vue 仍可直接 ref="detAnnotRef" 指向 DetectionAnnotator

  Props (页面私有子组件):
    image:    当前图
    loading:  加载中

  Slot:
    default:  父组件传入的 annotator 组件
-->
<template>
  <el-card :title="image ? `待标注图片 #${image.id}` : '待标注图片'">
    <div v-if="loading" v-loading="true" style="height: 360px;"></div>
    <div v-else-if="image" class="annotate-canvas">
      <slot />
      <!-- 元信息 -->
      <div style="color: #999; margin-top: 8px; font-size: 13px;">
        <strong>{{ image.filename }}</strong>
        | 尺寸: {{ image.width }}×{{ image.height }}
        | 大小: {{ ((image.file_size || 0) / 1024).toFixed(1) }} KB
        <el-tag
          v-if="image.task_type" size="small" effect="plain"
          :type="getTaskTypeMeta(image.task_type).type" style="margin-left: 6px;"
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
.annotate-canvas {
  position: relative;
}
</style>
