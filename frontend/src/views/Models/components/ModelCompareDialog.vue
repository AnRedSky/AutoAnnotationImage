<script setup lang="ts">
/**
 * ModelCompareDialog - 模型版本对比弹窗
 *
 * v3.0.0 Phase I 拆分: 从 Models/index.vue 抽离, page 只剩 v-model + 触发
 * - 顶部两卡 (A vs B 基础信息 + 准确率对比)
 * - 指标差异 (Δ accuracy / precision / recall / F1, 正负值配色)
 * - 模型 A 的混淆矩阵 (如有)
 *
 * 父组件只需:
 *   <ModelCompareDialog v-model="compareOpen" :compare="compare" />
 */
import { computed } from 'vue'
import { Grid } from '@element-plus/icons-vue'

const props = defineProps<{
  modelValue: boolean
  compare: any
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})
</script>

<template>
  <el-dialog v-model="visible" title="模型版本对比" width="820px">
    <div v-if="compare">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-card shadow="never" class="compare-card compare-card--a">
            <div class="compare-head">
              <el-icon><Grid /></el-icon>
              <span>{{ compare.model_a.name }}</span>
            </div>
            <div class="compare-base">基础模型: {{ compare.model_a.base_model }}</div>
            <el-statistic
              title="准确率" :value="Number(compare.model_a.accuracy ?? 0)" :precision="4"
              :value-style="{ color: '#4f7cff', fontWeight: 600 }"
            />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never" class="compare-card compare-card--b">
            <div class="compare-head">
              <el-icon><Grid /></el-icon>
              <span>{{ compare.model_b.name }}</span>
            </div>
            <div class="compare-base">基础模型: {{ compare.model_b.base_model }}</div>
            <el-statistic
              title="准确率" :value="Number(compare.model_b.accuracy ?? 0)" :precision="4"
              :value-style="{ color: '#00c48c', fontWeight: 600 }"
            />
          </el-card>
        </el-col>
      </el-row>

      <el-card header="指标差异 (A - B)" style="margin-top: 16px;" shadow="never" class="delta-card">
        <el-row :gutter="16">
          <el-col :span="6">
            <el-statistic
              title="准确率 Δ"
              :value="Number(compare.delta.accuracy ?? 0)"
              :precision="4"
              :value-style="{ color: (compare.delta.accuracy ?? 0) >= 0 ? '#00c48c' : '#ff4d4f', fontWeight: 600 }"
            />
          </el-col>
          <el-col :span="6">
            <el-statistic
              title="精确率 Δ"
              :value="Number(compare.delta.precision ?? 0)"
              :precision="4"
              :value-style="{ color: (compare.delta.precision ?? 0) >= 0 ? '#00c48c' : '#ff4d4f' }"
            />
          </el-col>
          <el-col :span="6">
            <el-statistic
              title="召回率 Δ"
              :value="Number(compare.delta.recall ?? 0)"
              :precision="4"
              :value-style="{ color: (compare.delta.recall ?? 0) >= 0 ? '#00c48c' : '#ff4d4f' }"
            />
          </el-col>
          <el-col :span="6">
            <el-statistic
              title="F1 Δ"
              :value="Number(compare.delta.f1_score ?? 0)"
              :precision="4"
              :value-style="{ color: (compare.delta.f1_score ?? 0) >= 0 ? '#00c48c' : '#ff4d4f' }"
            />
          </el-col>
        </el-row>
      </el-card>

      <el-card
        v-if="compare.model_a.confusion_matrix"
        header="混淆矩阵（模型 A）"
        style="margin-top: 16px;"
        shadow="never"
      >
        <pre class="cm-pre">{{ JSON.stringify(compare.model_a.confusion_matrix, null, 2) }}</pre>
      </el-card>
    </div>
  </el-dialog>
</template>

<style scoped>
.compare-card { border-radius: var(--radius-md) !important; }
.compare-card.compare-card--a { border-top: 3px solid #4f7cff !important; }
.compare-card.compare-card--b { border-top: 3px solid #00c48c !important; }
.compare-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.compare-base { font-size: 12px; color: var(--text-secondary); margin-bottom: 12px; }

.delta-card :deep(.el-statistic__content) {
  font-variant-numeric: tabular-nums;
}

.cm-pre {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-soft);
  padding: 12px;
  border-radius: var(--radius-sm);
  max-height: 320px;
  overflow: auto;
  white-space: pre;
}
</style>
