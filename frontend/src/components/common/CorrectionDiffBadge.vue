<!--
  CorrectionDiffBadge.vue (v3.4.0)
  ====================================================
  原子公共组件: AI 预测 vs 当前人工标注 的差异徽章
  路径: src/components/common/CorrectionDiffBadge.vue (按规则放 common 公共层)
  - 零业务耦合: 所有数据通过 props 传入
  - 3 种状态:
    * 一致 (绿色): AI top1 === 当前 final_label
    * 差异 (红色): AI top1 !== 当前 final_label
    * 无 AI (灰色): 当前无 ai_prediction
  - 用法:
    <CorrectionDiffBadge
      :ai-top1="candidate.label"
      :current-label-name="currentLabel?.name"
    />
-->
<template>
  <div v-if="state === 'consistent'" class="diff-badge diff-badge--ok">
    <el-tag type="success" size="small" effect="plain">
      <el-icon style="vertical-align: -2px;"><Check /></el-icon>
      AI 与人工一致
    </el-tag>
  </div>
  <div v-else-if="state === 'diff'" class="diff-badge diff-badge--diff">
    <el-tooltip placement="top" :content="`AI 预测: ${aiTop1} → 人工标注: ${currentLabelName}`">
      <el-tag type="danger" size="small" effect="dark">
        <el-icon style="vertical-align: -2px;"><Close /></el-icon>
        AI: {{ aiTop1 }} → 当前: {{ currentLabelName }}
      </el-tag>
    </el-tooltip>
  </div>
  <div v-else class="diff-badge diff-badge--none">
    <el-tag type="info" size="small" effect="plain" disable-transitions>
      纯人工标注
    </el-tag>
  </div>
</template>

<script setup lang="ts">
/**
 * CorrectionDiffBadge - 原子组件
 * 零业务耦合, 纯展示 AI 预测 vs 当前 类别差异
 */
import { computed } from 'vue'
import { Check, Close } from '@element-plus/icons-vue'

interface Props {
  /** AI 预测的 top1 类别名 (无 AI 预测时为 null) */
  aiTop1?: string | null
  /** 当前人工标注的类别名 (无标注时为 null) */
  currentLabelName?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  aiTop1: null,
  currentLabelName: null,
})

const state = computed<'consistent' | 'diff' | 'none'>(() => {
  if (!props.aiTop1 || !props.currentLabelName) return 'none'
  return props.aiTop1 === props.currentLabelName ? 'consistent' : 'diff'
})
</script>

<style scoped>
.diff-badge {
  display: inline-block;
}
.diff-badge--ok { /* 默认 */ }
.diff-badge--diff { /* 默认 */ }
.diff-badge--none { /* 默认 */ }
</style>
