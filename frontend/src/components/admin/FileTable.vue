<!-- 产物文件清单：保留路径、存在性、条数、大小和写入时间的统一显示口径。 -->
<script setup lang="ts">
import type { FileStat } from '../../types/acceptance'
import { bytes, time } from '../../utils/format'
defineProps<{ files: FileStat[] }>()
</script>
<template>
  <div class="scroll-x">
    <table class="tbl">
      <thead>
        <tr>
          <th>产物</th>
          <th>条数</th>
          <th>大小</th>
          <th>最后写入</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="file in files" :key="file.path" :class="{ bad: !file.present }">
          <td>{{ file.path }}</td>
          <td class="num">{{ file.present ? (file.lines ?? '—') : '缺失' }}</td>
          <td class="num">{{ file.present ? bytes(file.bytes) : '—' }}</td>
          <td>{{ file.present ? time(file.mtime) : '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
