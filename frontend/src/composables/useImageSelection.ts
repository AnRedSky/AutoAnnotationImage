import { computed, ref, type Ref, type ComputedRef } from 'vue'

/**
 * useImageSelection - 图像多选/全选状态管理
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 设计:
 * - 维护 selectedIds: 当前选中的图片 id 列表
 * - 提供 toggleSelect / toggleSelectAll / clear 等基础操作
 * - 提供 allOnPageSelected 计算属性 (基于当前可见图像列表)
 * - 提供 onSelectionChange 给 el-table @selection-change 事件用
 * - 提供 onSelectionEmpty 在分页/筛选/加载后清理选中 (避免跨页误操作)
 *
 * 父组件调用 onSelectionEmpty() 在 load() 后清空选中
 */
export interface UseImageSelectionOptions {
  /** 当前可见图像列表的 ref (用于计算 allOnPageSelected) */
  visibleImages: Ref<any[]>
}

export function useImageSelection(options: UseImageSelectionOptions) {
  const { visibleImages } = options

  // 当前选中的图片 id 列表
  const selectedIds = ref<number[]>([])

  // 当前页是否全选
  const allOnPageSelected: ComputedRef<boolean> = computed(
    () => visibleImages.value.length > 0 &&
          visibleImages.value.every((img: any) => selectedIds.value.includes(img.id))
  )

  /**
   * 切换单张图片的选中状态
   */
  function toggleSelect(imgId: number) {
    if (selectedIds.value.includes(imgId)) {
      selectedIds.value = selectedIds.value.filter((id) => id !== imgId)
    } else {
      selectedIds.value = [...selectedIds.value, imgId]
    }
  }

  /**
   * 全选 / 取消全选
   * - 全部已选 → 取消全选 (只清掉当前可见的, 保留其他页已选的)
   * - 部分或未选 → 全选当前页可见的图
   */
  function toggleSelectAll() {
    if (allOnPageSelected.value) {
      const visibleIds = new Set(visibleImages.value.map((img: any) => img.id))
      selectedIds.value = selectedIds.value.filter((id) => !visibleIds.has(id))
    } else {
      const set = new Set(selectedIds.value)
      visibleImages.value.forEach((img: any) => set.add(img.id))
      selectedIds.value = Array.from(set)
    }
  }

  /**
   * el-table @selection-change 事件回调: rows → ids
   */
  function onSelectionChange(rows: any[]) {
    selectedIds.value = rows.map((r) => r.id)
  }

  /**
   * 清空选中 (在分页/筛选/load 之后调用, 避免跨页误操作)
   */
  function clearSelection() {
    selectedIds.value = []
  }

  /**
   * 从选中中移除指定 id (单张删除/去标后调用)
   */
  function removeId(id: number) {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  }

  return {
    selectedIds,
    allOnPageSelected,
    toggleSelect,
    toggleSelectAll,
    onSelectionChange,
    clearSelection,
    removeId,
  }
}
