<script setup lang="ts">
/**
 * 转让所有权弹窗 (v3.3.1)
 * ========================
 *
 * 二次确认: 用户必须输入团队名才能点确认
 */
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi, type TeamMemberItem } from '@/api'

const props = defineProps<{
  modelValue: boolean
  teamId: number
  teamName: string
  members: TeamMemberItem[]
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'transferred'): void
}>()

const selectedMemberId = ref<number | undefined>()
const confirmText = ref('')
const submitting = ref(false)

/** 可接收所有权的成员 (非 viewer) */
const eligibleReceivers = computed(() =>
  props.members.filter((m) => m.role !== 'viewer')
)

/** 是否可以提交: 选了接收方 + 确认文字匹配 */
const canSubmit = computed(
  () =>
    !!selectedMemberId.value && confirmText.value.trim() === props.teamName
)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      selectedMemberId.value = undefined
      confirmText.value = ''
    }
  }
)

const close = () => emit('update:modelValue', false)

const onSubmit = async () => {
  if (!canSubmit.value || !selectedMemberId.value) return
  submitting.value = true
  try {
    await teamApi.transferOwnership(
      props.teamId,
      selectedMemberId.value,
      true
    )
    ElMessage.success('所有权转让成功')
    emit('transferred')
    close()
  } catch (e: any) {
    ElMessage.error('转让失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="转让团队所有权"
    width="480px"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>
        <strong>此操作不可撤销</strong>
      </template>
      转让后,接收方将自动成为「可管理」角色,您将失去创建者身份。
    </el-alert>

    <el-form label-position="top">
      <el-form-item label="选择接收方">
        <el-select
          v-model="selectedMemberId"
          filterable
          placeholder="仅可选择 manager / editor 角色"
          style="width: 100%"
        >
          <el-option
            v-for="m in eligibleReceivers"
            :key="m.user_id"
            :label="`${m.username} (${m.role})`"
            :value="m.user_id"
          />
        </el-select>
        <div v-if="!eligibleReceivers.length" class="form-hint">
          没有可接收的成员(需先提升某成员为 manager/editor)
        </div>
      </el-form-item>
      <el-form-item label="二次确认">
        <p class="confirm-hint">
          请输入团队全名 <code>{{ teamName }}</code> 以确认操作:
        </p>
        <el-input v-model="confirmText" :placeholder="teamName" />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="danger" :disabled="!canSubmit" :loading="submitting" @click="onSubmit">
        确认转让
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.confirm-hint {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.confirm-hint code {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 3px;
  font-weight: 600;
  color: var(--color-warning);
}
</style>
