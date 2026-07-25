<!--
  DetectionDeleteOverlay.vue (v3.0.0 Phase M 抽离自 DetectionAnnotator.vue)
  ===================================================================
  目标检测画布 - bbox 选中后右上角 X 删除按钮 (DOM overlay)

  职责:
  - 仅当 bbox 被选中时显示 (父组件传 position 控制可见性)
  - 位置由父组件根据 canvasSize + zoom + bbox 右上角计算
  - 边界检测由父组件使用 clampToWrapBounds 强制限制在 wrap 内
  - 点击触发父组件的删除 (父组件会走 confirmAndRemove 弹窗)

  设计原则:
  - 零业务耦合: 不直接调 ElMessageBox.confirm, 由父组件统一处理
  - 单向数据流: 父传 position + label + index, 子 emit delete 事件
-->
<template>
  <div
    v-if="position"
    class="bbox-delete-overlay"
    :style="{ left: position.x + 'px', top: position.y + 'px' }"
    :title="title"
    @click.stop="$emit('delete')"
  >
    <el-icon><Close /></el-icon>
  </div>
</template>

<script setup lang="ts">
import { Close } from '@element-plus/icons-vue'

defineProps<{
  /** 位置 (px, 父组件 clampToWrapBounds 计算后传入) */
  position: { x: number; y: number } | null
  /** 鼠标悬停 title (e.g. "删除 #1「猫」") */
  title: string
}>()

defineEmits<{
  (e: 'delete'): void
}>()
</script>

<style scoped>
/* v2.5.5: bbox 标签右上角 X 删除按钮 (DOM overlay, 跟随选中 bbox 位置)
   v2.5.10: 位置由父组件通过 clampToWrapBounds 边界检测, 永远落在 wrap 内部 */
.bbox-delete-overlay {
  position: absolute;
  width: 18px;
  height: 18px;
  background: #f56c6c;
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 12px;
  z-index: 20;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.25);
  transition: transform 0.15s ease, background 0.15s ease;
}
.bbox-delete-overlay:hover {
  background: #ff7875;
  transform: scale(1.15);
}
.bbox-delete-overlay .el-icon {
  font-size: 12px;
  pointer-events: none;
}
</style>
