<!--
  AnnotationBatchBar.vue (v3.5.0 新增)
  ====================================================
  标注工作台 - 批量操作条 (页面私有子组件)

  职责:
  1. 展示当前激活的「状态筛选」对应的图数量 + 批量操作入口
  2. 批量操作:
     - 批量去除标注 (annotationApi.clear): 把 selectedIds 的图全部回退到 pending 状态
     - 批量标记不合格 (annotationApi.batchMarkUnqualified): 一键标记多张图为不合格
  3. 选中管理:
     - 「全选当前列表」按钮 → emit('select-all')
     - 「清空选择」按钮 → emit('clear-selection')
  4. 显示当前已选数量 / 当前列表总数

  Props:
    totalInView: 当前视图 (按状态筛选后) 的图总数
    selectedCount: 已选中的图数量
    statusLabel: 当前状态的中文标签
    batchOperating: 父组件传入的 loading flag (避免按钮重复触发)

  Emits:
    batch-clear: 批量清除标注
    batch-mark-unqualified: 批量标记不合格 (参数: reason)
    select-all / clear-selection
-->
<template>
  <div v-if="totalInView > 0" class="batch-bar">
    <div class="batch-bar__left">
      <span class="batch-bar__label">
        当前 <strong>{{ statusLabel }}</strong> 共
        <strong class="batch-bar__count">{{ totalInView }}</strong> 张,
        已选 <strong class="batch-bar__count batch-bar__count--accent">{{ selectedCount }}</strong> 张
      </span>
    </div>
    <div class="batch-bar__right">
      <el-button
        size="small" plain
        :disabled="selectedCount === totalInView && totalInView > 0"
        @click="emit('select-all')"
      >全选</el-button>
      <el-button
        size="small" plain
        :disabled="selectedCount === 0"
        @click="emit('clear-selection')"
      >清空选择</el-button>
      <el-divider direction="vertical" />
      <el-button
        size="small" type="warning" plain
        :disabled="selectedCount === 0"
        :loading="batchOperating === 'mark'"
        @click="onBatchMarkUnqualified"
      >批量标记不合格</el-button>
      <el-button
        size="small" type="danger" plain
        :disabled="selectedCount === 0"
        :loading="batchOperating === 'clear'"
        @click="emit('batch-clear')"
      >批量清除标注</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElMessageBox } from 'element-plus'

const props = defineProps({
  totalInView: { type: Number, required: true },
  selectedCount: { type: Number, required: true },
  statusLabel: { type: String, required: true },
  /** 父组件传入的 loading flag, 类型 'mark' | 'clear' | null */
  batchOperating: { type: String as () => 'mark' | 'clear' | null, default: null },
})

const emit = defineEmits<{
  (e: 'batch-clear'): void
  (e: 'batch-mark-unqualified', reason: string): void
  (e: 'select-all'): void
  (e: 'clear-selection'): void
}>()

/** 批量标记不合格 - 弹窗选原因 (复用 rejectReason enum) */
async function onBatchMarkUnqualified() {
  try {
    const r: any = await ElMessageBox.prompt(
      '请选择不合格原因 (直接回车使用默认: 模糊/难以辨认)',
      '批量标记不合格',
      {
        confirmButtonText: '标记',
        cancelButtonText: '取消',
        inputValue: 'blurry_or_unrecognizable',
        inputPlaceholder: 'blurry_or_unrecognizable / wrong_subject / other',
      }
    )
    emit('batch-mark-unqualified', r?.value || 'blurry_or_unrecognizable')
  } catch {
    /* 用户取消 */
  }
}
</script>

<style scoped>
.batch-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  margin-bottom: 12px;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  font-size: 13px;
}
.batch-bar__label { color: #606266; }
.batch-bar__count {
  color: #303133;
  font-variant-numeric: tabular-nums;
  margin: 0 2px;
}
.batch-bar__count--accent { color: #409eff; }
.batch-bar__right { display: flex; align-items: center; gap: 4px; }
.batch-bar :deep(.el-divider--vertical) { height: 18px; margin: 0 4px; }
</style>
