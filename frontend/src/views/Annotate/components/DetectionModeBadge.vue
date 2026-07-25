<!--
  DetectionModeBadge.vue (v3.0.0 Phase M 抽离自 DetectionAnnotator.vue)
  ===================================================================
  目标检测画布 - 智能标注模式徽章 (左上角)

  职责:
  - 显示当前标注模式提示
  - 鼠标按下智能判定 (draw / select / drag), 无需切模式

  零业务耦合: 纯展示组件
-->
<template>
  <div class="mode-overlay mode-smart">
    智能标注模式 · 拖空白画新 / 点 bbox 选中
  </div>
</template>

<script setup lang="ts">
// 当前是常量显示, 无 props
</script>

<style scoped>
/* 模式徽章 (左上角) */
.mode-overlay {
  position: absolute;
  left: 8px;
  top: 8px;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  pointer-events: none;
  z-index: 5;
  background: rgba(64, 158, 255, 0.85);
  color: #fff;
}
.mode-overlay.mode-edit {
  background: rgba(230, 162, 60, 0.85);
}
</style>
