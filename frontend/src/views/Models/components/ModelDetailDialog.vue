<script setup lang="ts">
/**
 * ModelDetailDialog - 模型详情弹窗
 *
 * v3.0.0 Phase I 拆分: 从 Models/index.vue 抽离, page 只剩 v-model + 触发
 * - 顶部信息卡 (激活态 + 名称 + 基础模型 + 类别数)
 * - 关键指标 4 卡 (准确率 / 精确率 / 召回率 / F1)
 * - 基础信息描述列表 (ID / 数据集 / 模型文件路径)
 *
 * 父组件只需:
 *   <ModelDetailDialog v-model="detailOpen" :model="detail" />
 */
import { computed } from 'vue'
import { Grid, CircleCheck } from '@element-plus/icons-vue'

const props = defineProps<{
  modelValue: boolean
  model: any
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
}>()

// v-model 桥接
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

// 数值格式化 helpers (从原 page 同步过来)
const pct = (v: any) => (v != null ? `${(Number(v) * 100).toFixed(2)}%` : '-')
const f1fmt = (v: any) => (v != null ? Number(v).toFixed(3) : '-')
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`模型详情 - ${model?.name || ''}`"
    width="720px"
  >
    <div v-if="model">
      <!-- 顶部信息卡 (含激活状态徽章) -->
      <div class="detail-hero" :class="{ 'is-active': model.is_active }">
        <div class="hero-left">
          <div class="hero-mark">
            <el-icon><Grid /></el-icon>
          </div>
          <div>
            <div class="hero-name">{{ model.name }}</div>
            <div class="hero-base">{{ model.base_model }} · 类别数 {{ model.num_classes }}</div>
          </div>
        </div>
        <el-tag v-if="model.is_active" type="success" effect="dark">当前激活</el-tag>
        <el-tag v-else effect="plain">未激活</el-tag>
      </div>

      <!-- 关键指标 4 卡 -->
      <el-row :gutter="12" class="metrics-row">
        <el-col :span="6">
          <div class="metric-tile metric-tile--blue">
            <div class="metric-label">准确率</div>
            <div class="metric-value">{{ pct(model.accuracy) }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-tile metric-tile--green">
            <div class="metric-label">精确率</div>
            <div class="metric-value">{{ pct(model.precision) }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-tile metric-tile--orange">
            <div class="metric-label">召回率</div>
            <div class="metric-value">{{ pct(model.recall) }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric-tile metric-tile--purple">
            <div class="metric-label">F1</div>
            <div class="metric-value">{{ f1fmt(model.f1_score) }}</div>
          </div>
        </el-col>
      </el-row>

      <el-descriptions :column="2" border size="small" style="margin-top: 12px;">
        <el-descriptions-item label="模型 ID">{{ model.id }}</el-descriptions-item>
        <el-descriptions-item label="数据集 ID">{{ model.dataset_id }}</el-descriptions-item>
        <el-descriptions-item label="基础模型">{{ model.base_model }}</el-descriptions-item>
        <el-descriptions-item label="类别数">{{ model.num_classes }}</el-descriptions-item>
        <el-descriptions-item label="模型文件" :span="2">
          <code class="path-code">{{ model.file_path }}</code>
        </el-descriptions-item>
      </el-descriptions>
    </div>
  </el-dialog>
</template>

<style scoped>
/* 详情弹窗 */
.detail-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.06) 0%, rgba(110, 81, 233, 0.06) 100%);
  border: 1px solid rgba(79, 124, 255, 0.12);
  margin-bottom: 16px;
}
.detail-hero.is-active {
  background: linear-gradient(135deg, rgba(0, 196, 140, 0.08) 0%, rgba(0, 163, 224, 0.08) 100%);
  border-color: rgba(0, 196, 140, 0.18);
}
.hero-left { display: flex; align-items: center; gap: 12px; }
.hero-mark {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  box-shadow: 0 4px 12px rgba(79, 124, 255, 0.3);
}
.detail-hero.is-active .hero-mark {
  background: var(--gradient-success);
  box-shadow: 0 4px 12px rgba(0, 196, 140, 0.3);
}
.hero-name { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.hero-base { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

.metrics-row { margin-top: 4px; }
.metric-tile {
  border-radius: var(--radius-md);
  padding: 14px 16px;
  background: #fff;
  border: 1px solid var(--border-soft);
  text-align: center;
}
.metric-tile--blue   { background: linear-gradient(135deg, #f0f4ff 0%, #e9ecff 100%); border-color: rgba(79, 124, 255, 0.18); }
.metric-tile--green  { background: linear-gradient(135deg, #e6fbf3 0%, #d9f5ec 100%); border-color: rgba(0, 196, 140, 0.18); }
.metric-tile--orange { background: linear-gradient(135deg, #fff2e9 0%, #ffe7d6 100%); border-color: rgba(255, 138, 76, 0.18); }
.metric-tile--purple { background: linear-gradient(135deg, #f4e9ff 0%, #ead7ff 100%); border-color: rgba(114, 46, 209, 0.18); }
.metric-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.metric-value { font-size: 18px; font-weight: 600; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.metric-tile--blue   .metric-value { color: #4f7cff; }
.metric-tile--green  .metric-value { color: #00c48c; }
.metric-tile--orange .metric-value { color: #ff8a4c; }
.metric-tile--purple .metric-value { color: #722ed1; }

.path-code {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-soft);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--text-regular);
  word-break: break-all;
}
</style>
