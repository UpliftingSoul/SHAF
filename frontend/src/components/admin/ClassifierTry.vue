<!-- 单句分类实验：后端返回阈值、标签和独立分数；卸载取消请求，禁止重复提交。 -->
<script setup lang="ts">
import { onMounted, onScopeDispose, ref, shallowRef } from 'vue'
import { post } from '../../api/http'
import type { ClassificationResult } from '../../types/acceptance'
import { score } from '../../utils/format'
const props = defineProps<{ online: boolean }>()
const text = ref('买大了想退')
const result = shallowRef<ClassificationResult | null>(null)
const busy = ref(false)
const error = ref('')
let controller: AbortController | undefined
const presets = [
  '买大了想退',
  '猫粮颗粒大不大，牙口不好的猫嚼得动吗',
  '积分可以拿来抵运费吗，要是退货运费又是谁承担',
  '旧猫笼你们回收吗',
]
async function classify(preset?: string) {
  if (busy.value) return
  if (preset) text.value = preset
  error.value = ''
  if (!text.value.trim()) {
    error.value = '先输入一句话'
    return
  }
  busy.value = true
  result.value = null
  controller = new AbortController()
  try {
    result.value = await post<ClassificationResult>(
      '/api/acceptance/classify',
      { text: text.value.trim() },
      controller.signal,
    )
  } catch (cause) {
    if (!controller.signal.aborted) error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
onMounted(() => {
  if (props.online) void classify()
})
onScopeDispose(() => controller?.abort())
</script>
<template>
  <section class="panel">
    <h2>
      单句试分类：多标签机制现场看
      <span class="pill" :class="online ? 'pass' : 'missing'"
        >:8110 {{ online ? '在线' : '离线' }}</span
      >
    </h2>
    <p class="lede">17 类各自独立过线，过几个打几个。全部不过线时取最高分标签兜底。</p>
    <form class="tryrow" @submit.prevent="classify()">
      <input
        v-model="text"
        aria-label="试分类问题"
        placeholder="输入一句用户问题，比如：买大了想退"
        :disabled="busy"
      /><button class="btn go" :disabled="busy">{{ busy ? '打分中…' : '试分类' }}</button>
    </form>
    <div class="presets">
      <button
        v-for="preset in presets"
        :key="preset"
        class="btn sm"
        :title="preset"
        :disabled="busy"
        @click="classify(preset)"
      >
        {{ preset.length > 14 ? preset.slice(0, 14) + '…' : preset }}
      </button>
    </div>
    <div v-if="error" class="miss" role="alert">试分类失败：{{ error }}</div>
    <template v-if="result"
      ><div class="verdict">
        <b>命中标签：{{ result.labels.join(' + ') || '（无）' }}</b>
        <div>
          判定阈值 {{ result.threshold ?? '—' }}；{{
            result.fallback ? '17 类全部不过线，已走「取最高分兜底」' : '过线的类全部打上'
          }}
        </div>
      </div>
      <div class="sc">
        <div
          v-for="item in result.scores"
          :key="item.label"
          class="scrow"
          :class="{ hit: item.hit }"
        >
          <span class="nm">{{ item.label }}</span
          ><span class="track"
            ><span
              class="fill"
              :style="{ width: `${Math.max(1, Math.round(item.score * 100))}%` }" /><span
              v-if="result.threshold != null"
              class="line"
              :style="{ left: `${result.threshold * 100}%` }"
              :title="'判定阈值 ' + result.threshold" /></span
          ><span class="v">{{ score(item.score) }}</span>
        </div>
      </div></template
    >
  </section>
</template>
<style scoped>
.tryrow {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.tryrow input {
  flex: 1;
  min-width: 200px;
  font: inherit;
  font-size: 13px;
  border: 3px solid var(--ink);
  padding: 7px 10px;
  background: var(--paper);
}
.presets {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  margin: 10px 0;
}
.verdict {
  margin-top: 12px;
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.8;
}
.sc {
  margin-top: 10px;
}
.scrow {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 5px;
  font-size: 12px;
}
.nm {
  width: 115px;
  flex-shrink: 0;
}
.track {
  flex: 1;
  position: relative;
  height: 12px;
  border: 2px solid var(--ink);
  background: var(--paper);
}
.fill {
  display: block;
  height: 100%;
  background: var(--fur);
}
.hit .fill {
  background: var(--online);
}
.line {
  position: absolute;
  top: -4px;
  bottom: -4px;
  border-left: 2px solid var(--error);
}
.v {
  width: 45px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
