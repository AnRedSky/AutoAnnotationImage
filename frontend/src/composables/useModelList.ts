/**
 * useModelList - 模型列表加载 + 批量操作 composable
 *
 * v3.0.0 Phase I 拆分: 把 Models/index.vue 中的 load / onActivate /
 * onDeactivate / onBatchSetActive / onDelete / onBatchDelete / onSelectionChange
 * 等「数据 + 操作」逻辑全部抽离, page 只剩 view-model (筛选 / 统计 / 弹窗
 * 状态 / 表格渲染). 保持与 useTrainingListSSE / useSilentRefresh 一致的
 * composable 风格 (useXxx + return refs/cbs), 不破坏既有调用方式.
 */
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { modelApi } from '@/api'

export interface UseModelListOptions {
  /** 数据列表响应式引用 (page 已声明, 这里只做内部赋值) */
  data: ReturnType<typeof ref<any[]>>
  /** loading 状态 */
  loading: ReturnType<typeof ref<boolean>>
  /** 加载完成后回调 (如重置分页等) */
  onLoaded?: () => void
}

/**
 * 返回值: 列表相关 handlers + 批量操作 loading
 * - load: 拉取模型列表
 * - onActivate / onDeactivate / onBatchSetActive: 激活态切换
 * - onDelete / onBatchDelete: 删除单/多
 * - onSelectionChange: 表格多选回调 (封装一下, 避免 page 关心 ref 类型)
 */
export function useModelList(options: UseModelListOptions) {
  const { data, loading, onLoaded } = options

  // 批量激活/删除 loading
  const batchActivating = ref(false)
  const batchDeleting = ref(false)
  // 选中的行 (由 page 透传, 集中管理避免 props drilling)
  const selectedRows = ref<any[]>([])
  const selectedIds = computed(() => selectedRows.value.map((r) => r.id))

  // ============== 加载 ==============
  const load = async () => {
    loading.value = true
    try {
      const res: any = await modelApi.list()
      data.value = res?.items || res || []
      onLoaded?.()
    } catch (e: any) {
      ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      loading.value = false
    }
  }

  // ============== 单个激活/取消激活 ==============
  const onActivate = async (id: number) => {
    try {
      await modelApi.activate(id)
      ElMessage.success('已激活该版本 (允许多激活并存)')
      await load()
    } catch (e: any) {
      ElMessage.error('激活失败: ' + (e?.response?.data?.detail || e?.message))
    }
  }

  const onDeactivate = async (id: number) => {
    try {
      await ElMessageBox.confirm('确认取消该模型的激活状态?', '取消激活', { type: 'warning' })
    } catch { return }
    try {
      await modelApi.deactivate(id)
      ElMessage.success('已取消激活')
      await load()
    } catch (e: any) {
      ElMessage.error('取消激活失败: ' + (e?.response?.data?.detail || e?.message))
    }
  }

  // ============== 批量激活 / 批量取消激活 ==============
  const onBatchSetActive = async (active: boolean) => {
    if (selectedIds.value.length === 0) {
      ElMessage.warning('请先选择模型版本')
      return
    }
    const action = active ? '激活' : '取消激活'
    // 拆出已在目标态 / 需变更 两类, 给用户更精确的反馈
    const need: any[] = []
    const skip: any[] = []
    for (const r of selectedRows.value) {
      if (r.is_active === active) skip.push(r)
      else need.push(r)
    }
    if (need.length === 0) {
      ElMessage.info(`所选 ${skip.length} 个版本已全部为${active ? '激活' : '未激活'}, 无需操作`)
      return
    }
    try {
      await ElMessageBox.confirm(
        `确认${action}选中的 ${need.length} 个版本?` +
          (skip.length ? `另有 ${skip.length} 个版本已为${active ? '激活' : '未激活'}, 会被跳过` : ''),
        `批量${action}`,
        { type: active ? 'success' : 'warning' }
      )
    } catch { return }
    batchActivating.value = true
    try {
      const r: any = await modelApi.batchSetActive(need.map((x) => x.id), active)
      if (r.success) {
        ElMessage.success(`已${action} ${r.ids.length} 个版本`)
        selectedRows.value = []
      } else {
        ElMessage.warning(r.message || `${action}失败`)
      }
      await load()
    } catch (e: any) {
      ElMessage.error(`批量${action}失败: ` + (e?.response?.data?.detail || e?.message))
    } finally {
      batchActivating.value = false
    }
  }

  // ============== 单个删除 ==============
  const onDelete = async (row: any) => {
    const tip = row.is_active
      ? `确定要删除当前已激活的模型版本「${row.name}」(ID=${row.id}) 吗？\n删除即取消激活, 此操作不可恢复, 训练历史会被保留但与该版本解绑。`
      : `确定要删除模型版本「${row.name}」(ID=${row.id}) 吗？此操作不可恢复，相关的训练历史记录会被保留但会与该版本解绑。`
    try {
      await ElMessageBox.confirm(tip, '删除确认', {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      return  // 用户取消
    }
    try {
      const res: any = await modelApi.remove(row.id)
      const extra = res.was_active ? ' (已同步取消激活)' : ''
      const fileMsg = res?.deleted_file ? '（已同时删除权重文件）' : ''
      ElMessage.success(`已删除模型版本「${row.name}」${extra}${fileMsg}`)
      // 清理已选项中已删除的 id
      selectedRows.value = selectedRows.value.filter((r) => r.id !== row.id)
      await load()
    } catch (e: any) {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
    }
  }

  // ============== 批量删除 ==============
  const onBatchDelete = async () => {
    if (selectedRows.value.length === 0) return
    const deletable = selectedRows.value
    const activePicked = deletable.filter((r) => r.is_active)
    const activeHint = activePicked.length > 0
      ? `\n其中 ${activePicked.length} 个为已激活版本, 删除将同步取消激活。`
      : ''

    const preview = deletable.slice(0, 3).map((r) => r.name).join('、')
    const more = deletable.length > 3 ? ` 等 ${deletable.length} 个` : ''

    try {
      await ElMessageBox.confirm(
        `确定要批量删除「${preview}${more}」吗？此操作不可恢复, 相关的训练历史记录会保留但会与这些版本解绑。${activeHint}`,
        '批量删除确认',
        {
          confirmButtonText: `确定删除 ${deletable.length} 个`,
          cancelButtonText: '取消',
          type: 'warning',
        }
      )
    } catch {
      return
    }
    batchDeleting.value = true
    try {
      const res: any = await modelApi.batchRemove(deletable.map((r) => r.id))
      const fd = res?.files_deleted || 0
      const fileMsg = fd > 0 ? `（已同时删除 ${fd} 个权重文件）` : ''
      ElMessage.success(`已批量删除 ${res.deleted_ids.length} 个模型版本${fileMsg}`)
      selectedRows.value = []
      await load()
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message
      ElMessage.error('批量删除失败: ' + detail)
    } finally {
      batchDeleting.value = false
    }
  }

  // ============== 表格多选 ==============
  const onSelectionChange = (rows: any[]) => {
    selectedRows.value = rows
  }

  return {
    // state
    selectedRows,
    selectedIds,
    batchActivating,
    batchDeleting,
    // actions
    load,
    onActivate,
    onDeactivate,
    onBatchSetActive,
    onDelete,
    onBatchDelete,
    onSelectionChange,
  }
}
