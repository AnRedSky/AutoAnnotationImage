<!--
  DetectionZoomBar.vue (v3.0.0 Phase M 抽离自 DetectionAnnotator.vue)
  ===================================================================
  目标检测画布 - 缩放控制条 (顶部中间)

  职责:
  - 显示当前缩放百分比
  - 提供 缩小/重置/放大 三个按钮
  - 通过 emit 事件向上抛出, 由父组件调用 useDetectionZoom 方法

  零业务耦合: 不引入 store, 不直接调用 composable
  单向数据流: 父传 props, 子 emit 事件
-->
<template>
  <div class="zoom-overlay">
    <el-button-group size="small">
      <el-button @click="$emit('zoom-out')" :icon="ZoomOut" circle />
      <el-button @click="$emit('zoom-reset')" plain style="min-width: 64px;">
        {{ zoomPercent }}%
      </el-button>
      <el-button @click="$emit('zoom-in')" :icon="ZoomIn" circle />
    </el-button-group>
  </div>
</template>

<script setup lang="ts">
import { ZoomIn, ZoomOut } from '@element-plus/icons-vue'

defineProps<{
  /** 缩放百分比 (e.g. 100) */
  zoomPercent: number
}>()

defineEmits<{
  (e: 'zoom-in'): void
  (e: 'zoom-out'): void
  (e: 'zoom-reset'): void
}>()
</script>

<style scoped>
/* v2.5.10: 缩放控制条 (顶部中间) */
.zoom-overlay {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  padding: 2px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
}
</style>
