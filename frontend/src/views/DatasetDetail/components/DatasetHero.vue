<script setup lang="ts">
/**
 * DatasetHero - 数据集详情页顶部 hero 区
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 包含: 返回按钮 + 数据集名称 + 任务类型 tag + 描述
 * 右侧: 刷新 / 上传 / 分享到团队 / 导出 四个操作
 *
 * v3.3.1 L2: 新增「分享到团队」按钮 (仅 owner 可见)
 *   - 通过 emit('share') 由父组件打开 ShareDatasetDialog
 *   - 这样 hero 保持纯展示, 不耦合 teamApi
 */
import { ArrowLeft, Refresh, UploadFilled, Download, Share } from '@element-plus/icons-vue'
import { getTaskTypeMeta } from '@/utils/taskType'

const props = defineProps<{
  dataset: any
  /** 当前用户是否是数据集 owner, 控制分享按钮是否显示 */
  isOwner?: boolean
}>()

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'refresh'): void
  (e: 'upload'): void
  (e: 'export', format: 'coco' | 'yolo' | 'csv'): void
  (e: 'share'): void
}>()
</script>

<template>
  <div v-if="dataset" class="ds-hero">
    <div class="ds-hero__bg" />
    <div class="ds-hero__body">
      <div class="ds-hero__left">
        <el-button :icon="ArrowLeft" round size="small" @click="emit('back')" class="ds-hero__back">
          返回
        </el-button>
        <div class="ds-hero__title">
          <h1>{{ dataset.name }}</h1>
          <div class="ds-hero__meta">
            <el-tooltip
              v-if="dataset.task_type"
              :content="getTaskTypeMeta(dataset.task_type).desc"
              placement="bottom"
            >
              <el-tag size="small" :type="getTaskTypeMeta(dataset.task_type).type" effect="plain">
                <el-icon style="margin-right: 3px; vertical-align: -2px;">
                  <component :is="getTaskTypeMeta(dataset.task_type).icon" />
                </el-icon>
                {{ getTaskTypeMeta(dataset.task_type).label }}
              </el-tag>
            </el-tooltip>
            <span v-if="dataset.description" class="ds-hero__desc">
              {{ dataset.description }}
            </span>
          </div>
        </div>
      </div>
      <div class="ds-hero__actions">
        <el-button :icon="Refresh" @click="emit('refresh')">刷新</el-button>
        <el-button
          v-if="props.isOwner"
          :icon="Share"
          @click="emit('share')"
        >
          分享到团队
        </el-button>
        <el-button :icon="UploadFilled" type="success" @click="emit('upload')">
          上传图片
        </el-button>
        <el-dropdown @command="(c: any) => emit('export', c)">
          <el-button :icon="Download">导出</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="coco">COCO 格式</el-dropdown-item>
              <el-dropdown-item command="yolo">YOLO 格式</el-dropdown-item>
              <el-dropdown-item command="csv">CSV 明细</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ds-hero {
  position: relative;
  border-radius: var(--radius-md);
  overflow: hidden;
  flex-shrink: 0;
  background: linear-gradient(135deg, var(--bg-soft) 0%, #fff 100%);
  border: 1px solid var(--border-soft);
}
.ds-hero__bg {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.05) 0%, rgba(110, 81, 233, 0.05) 100%);
  pointer-events: none;
}
.ds-hero__body {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  gap: 16px;
  flex-wrap: wrap;
}
.ds-hero__left { display: flex; align-items: center; gap: 12px; }
.ds-hero__back { flex-shrink: 0; }
.ds-hero__title h1 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
}
.ds-hero__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}
.ds-hero__desc { color: var(--text-secondary); }
.ds-hero__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
