<!--
  ClassificationPanel.vue (v2.5.7 拆分自 Annotate.vue, v2.5.11 精简文本)
  ====================================================
  图像分类任务右侧 AI 候选面板 (仅保留操作)

  v2.5.11 精简:
  - 简化 allUnknown 块: 只保留 "未知" 标签, 删除 2 行解释文字
  - 简化未知候选 tag: 只显示 "未知", 删除括号说明
  - 删除 "所有待标注图片已加载完毕" 提示行
    (此提示在 el-empty 已有 "暂无 AI 预测" 提示, 重复占用空间)

  包含: AI Top-5 候选 + 修正下拉 + 上一张/下一张 + 去数据集详情

  Props (业务通用, 弱耦合):
    image:            当前图 (用于 file_url, filename)
    candidates:       AI Top-5 候选
    allUnknown:       全部未知 (基础模型 ImageNet 输出场景)
    categories:       项目类别列表
    sortedCategories: 排序后类别 (按 id 升序)
    canGoPrev, noMore

  Emits:
    submit(categoryId, label, isFromAI): 用户采纳/强制采用某标签
    prev, next, view-dataset
-->
<template>
  <el-card class="op-card" title="AI 候选标签（Top-5）">
    <el-empty v-if="!image && candidates.length === 0" description="请选择数据集" :image-size="80" />
    <el-empty v-else-if="candidates.length === 0" description="该图无 AI 预测, 请直接选择其他类别" :image-size="60" />
    <!-- 关键简化: 基础模型 (ImageNet 预训练) 输出 = 全部 Top-5 都不在项目类目
         -> 整组归一为「未知」, 不再分 5 个候选 + 各自置信度 -->
    <div v-else-if="allUnknown" class="model-confidence-bar"
      style="text-align: center; padding: 24px 12px; border: 1px dashed #f56c6c; border-radius: 6px; background: #fef0f0;">
      <el-tag type="danger" size="large" effect="dark">未知</el-tag>
    </div>
    <div v-for="(c, idx) in candidates" v-show="!allUnknown" :key="`${c.label}-${idx}`" class="model-confidence-bar">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span>
          <el-tag size="small" type="info">#{{ idx + 1 }}</el-tag>
          <strong style="margin-left: 6px;" :class="{ 'unknown-label': !findCategory(c.label) }">
            {{ findCategory(c.label) ? c.label : '未知' }}
          </strong>
        </span>
        <el-tag :type="c.confidence > 0.8 ? 'success' : c.confidence > 0.5 ? 'warning' : 'info'">
          {{ (c.confidence * 100).toFixed(1) }}%
        </el-tag>
      </div>
      <el-progress :percentage="Math.round(c.confidence * 100)" :show-text="false"
        :color="c.confidence > 0.8 ? '#67c23a' : c.confidence > 0.5 ? '#e6a23c' : '#909399'" />
      <div style="margin-top: 4px;">
        <template v-if="findCategory(c.label)">
          <el-button size="small" type="primary" :icon="Check"
            @click="submitClick(findCategory(c.label)!.id, c.label, true)">
            确认此标签
          </el-button>
          <el-button size="small"
            @click="submitClick(findCategory(c.label)!.id, c.label, false)">
            强制采用
          </el-button>
        </template>
        <el-tag v-else type="danger" size="small">未知</el-tag>
      </div>
    </div>
    <el-divider v-if="categories.length > 0">或选择其他类别</el-divider>
    <el-select
      v-if="categories.length > 0"
      placeholder="选择其他类别（修正）" style="width: 100%;"
      filterable
      @change="onCategoryChange"
    >
      <el-option v-for="c in sortedCategories" :key="c.id" :label="c.name" :value="c.id" />
    </el-select>
    <div style="margin-top: 8px; display: flex; gap: 8px;">
      <el-button
        style="flex: 1;"
        :icon="ArrowLeft"
        :disabled="!canGoPrev"
        @click="emit('prev')"
      >上一张</el-button>
      <el-button
        style="flex: 1;"
        :type="noMore ? 'info' : 'danger'"
        :plain="!noMore"
        :icon="Close"
        :disabled="noMore"
        @click="emit('next')"
      >{{ noMore ? '已是最后一张' : '下一张' }}</el-button>
    </div>
    <div v-if="image" style="margin-top: 8px; text-align: center;">
      <el-link type="primary" :icon="View" @click="emit('view-dataset')">
        去数据集详情浏览全部图片
      </el-link>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Check, Close, ArrowLeft, View } from '@element-plus/icons-vue'

interface Category { id: number; name: string }
interface Candidate { label: string; confidence: number }
interface Image { id: number; filename: string }

const props = defineProps<{
  image: Image | null
  candidates: Candidate[]
  allUnknown: boolean
  categories: Category[]
  sortedCategories: Category[]
  canGoPrev: boolean
  noMore: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', categoryId: number, label: string, isFromAI: boolean): void
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'view-dataset'): void
}>()

function findCategory(label: string): Category | undefined {
  // 注意: Category 接口只有 id/name, 没有 label 字段
  return props.categories.find((c) => c.name === label)
}

function submitClick(categoryId: number, label: string, isFromAI: boolean) {
  emit('submit', categoryId, label, isFromAI)
}

function onCategoryChange(id: number) {
  const cat = props.categories.find((c) => c.id === id)
  if (cat) emit('submit', cat.id, cat.name, false)
}
</script>

<style scoped>
/* 跟随父 el-col 高度, 与左侧侧栏/中间画布三列同高
   - el-card 本体 100% 填充 el-col
   - body 内部 flex 1 + auto overflow, 内容过长时本卡片内部滚动 */
.op-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.op-card :deep(.el-card__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.model-confidence-bar {
  padding: 8px 0;
  border-bottom: 1px dashed #ebeef5;
}
.model-confidence-bar:last-of-type {
  border-bottom: none;
}
.unknown-label {
  color: #f56c6c;
}
</style>
