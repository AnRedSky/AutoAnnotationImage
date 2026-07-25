<!--
  DetectionInfoPanel.vue (v3.0.0 Phase M 抽离自 DetectionAnnotator.vue)
  ===================================================================
  目标检测画布 - 整合信息面板 (右上角)

  职责:
  - 显示鼠标坐标 (x/y 像素 + 归一化百分比)
  - 显示画布尺寸 (w×h) + 缩放比 + 显示比
  - 紧凑双行布局, 占据图片右上角小区域
  - 半透明深色背景, 不喧宾夺主

  零业务耦合: 只显示 props 传入的坐标和尺寸数据
  单向数据流: 父传 props
-->
<template>
  <div class="info-panel">
    <div class="info-row coord-row">
      <span v-if="cursorPos" class="coord-text">
        x: <b>{{ cursorPos.x.toFixed(0) }}</b>
        <span class="coord-pct">({{ (cursorPos.nx * 100).toFixed(1) }}%)</span>
        <span class="info-sep">·</span>
        y: <b>{{ cursorPos.y.toFixed(0) }}</b>
        <span class="coord-pct">({{ (cursorPos.ny * 100).toFixed(1) }}%)</span>
      </span>
      <span v-else class="coord-placeholder">移入画布查看坐标</span>
    </div>
    <div class="info-row size-row">
      <span><b>{{ canvasW }}</b>×<b>{{ canvasH }}</b>px</span>
      <span class="info-sep">·</span>
      <span>缩放 {{ scalePercent }}%</span>
      <span class="info-sep">·</span>
      <span>显示 {{ zoomPercent }}%</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { CursorPos } from '@/composables/useDetectionTypes'

defineProps<{
  /** 鼠标位置 (null 时显示 placeholder) */
  cursorPos: CursorPos | null
  /** 画布宽度 (px) */
  canvasW: number
  /** 画布高度 (px) */
  canvasH: number
  /** 缩放比 (imageWidth -> canvasW 的比例, 百分比) */
  scalePercent: string
  /** 缩放显示比 (zoom * 100, 百分比) */
  zoomPercent: number
}>()
</script>

<style scoped>
/* v2.5.10: 整合信息面板 (右上角) — coord + size 合并
   - 紧凑双行布局, 占据图片右上角小区域
   - 半透明深色背景, 不喧宾夺主
   - 与左下角的 mode 徽章和顶部中间的 zoom 控件形成清晰的视觉分区 */
.info-panel {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 10;
  background: rgba(0, 0, 0, 0.62);
  color: #fff;
  padding: 5px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  pointer-events: none;
  line-height: 1.5;
  display: flex;
  flex-direction: column;
  gap: 1px;
  text-align: right;
  max-width: calc(100% - 16px);  /* 防止窗口过窄时撑出 wrap */
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}
.info-panel .info-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  white-space: nowrap;
}
.info-panel .info-row.coord-row {
  /* 坐标行: 突出关键数字 */
  font-weight: 500;
}
.info-panel .info-row.size-row {
  /* 尺寸行: 略低对比度, 与坐标行拉开层次 */
  opacity: 0.85;
  font-size: 10.5px;
}
.info-panel b {
  color: #67c23a;
  font-weight: 600;
  margin: 0 1px;
}
.info-panel .coord-pct {
  color: #b3d8a8;  /* 浅绿, 与 x/y 数字色 (#67c23a) 区分 */
  font-size: 10px;
  margin-left: 2px;
}
.info-panel .coord-placeholder {
  color: #c0c4cc;
  font-style: italic;
  font-size: 10.5px;
}
.info-panel .info-sep {
  color: #6b7280;
  margin: 0 1px;
  user-select: none;
}
</style>
