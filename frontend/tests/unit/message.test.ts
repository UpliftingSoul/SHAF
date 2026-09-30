/** 展示安全回归：模型输出不能注入脚本，引用仍能显示真实来源。 */
import { mount, flushPromises } from '@vue/test-utils'
import { expect, it } from 'vitest'
import MessageContent from '../../src/components/chat/MessageContent.vue'

it('保留 Markdown 表格并移除脚本、事件处理器和危险链接', () => {
  const wrapper = mount(MessageContent, {
    props: {
      text: '|字段|内容|\n|---|---|\n|订单|1001|\n\n<img src=x onerror="alert(1)"><script>alert(1)</script>\n\n[危险](javascript:alert(1))',
      citations: [],
      decorate: true,
    },
  })
  expect(wrapper.find('table').exists()).toBe(true)
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.html()).not.toContain('onerror')
  expect(wrapper.html()).not.toContain('href="javascript:')
  wrapper.unmount()
})

it('点击实际来源角标打开浮层，代码中的编号保留原样', async () => {
  const wrapper = mount(MessageContent, {
    props: {
      text: '满99元包邮[1]，示例代码 `[1]`',
      citations: [{ n: 1, answer: '真实政策', section_path: '运费' }],
      decorate: true,
    },
    attachTo: document.body,
  })
  await wrapper.find('[data-citation]').trigger('click')
  await flushPromises()
  expect(document.querySelector('.cite-pop')?.textContent).toContain('真实政策')
  expect(wrapper.find('code').text()).toBe('[1]')
  wrapper.unmount()
  expect(document.querySelector('.cite-pop')).toBeNull()
})
