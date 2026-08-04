<script setup lang="ts">
/**
 * 编辑团队弹窗 (v3.3.1)
 * ======================
 *
 * Props:
 *  - modelValue: boolean
 *  - team:      { id, name, description, max_members } | null
 *
 * Emits:
 *  - update:modelValue
 *  - updated     编辑成功回调
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi } from '@/api'

const props = defineProps<{
  modelValue: boolean
  team: { id: number; name: string; description: string | null; max_members: number } | null
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'updated'): void
}>()

const form = ref({ name: '', description: '', max_members: 20 })
const submitting = ref(false)

/** 弹窗打开时回填表单 */
watch(
  () => props.modelValue,
  (open) => {
    if (open && props.team) {
      form.value = {
        name: props.team.name,
        description: props.team.description || '',
        max_members: props.team.max_members,
      }
    }
  }
)

const close = () => {
  emit('update:modelValue', false)
}

const onSubmit = async () => {
  if (!props.team) return
  if (!form.value.name) {
    ElMessage.warning('团队名称不能为空')
    return
  }
  submitting.value = true
  try {
    await teamApi.update(props.team.id, {
      name: form.value.name,
      description: form.value.description || undefined,
      max_members: form.value.max_members,
    })
    ElMessage.success('团队信息已更新')
    emit('updated')
    close()
  } catch (e: any) {
    ElMessage.error('更新失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="编辑团队"
    width="460px"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <el-form v-if="team" label-position="top" :model="form">
      <el-form-item label="名称">
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="团队描述(可选)"
        />
      </el-form-item>
      <el-form-item label="成员上限">
        <el-input-number v-model="form.max_members" :min="2" :max="100" />
        <div class="form-hint">降低上限不能小于当前成员数</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onSubmit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
</style>
