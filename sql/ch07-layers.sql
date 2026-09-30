-- =============================================================
-- ch07 · 分层上下文 · 建表 DDL
-- 增加层1原文起点锚点，并建立只追加的分段摘要表。
--
-- 两个消息 id 将历史划成三段，数据本身不搬动，只改变后续渲染方式：
--   id ≤ summary_upto_msg_id          已进入摘要，不再作为滑窗消息注入
--   summary_upto < id ≤ layer1_from   层2：用户原话保留，其余内容半压缩
--   id > layer1_from                  层1：近期消息原样保留
--
-- 分层降级只需移动边界，无需给消息额外记录“是否压缩过”；
-- 同一份 State 按当前边界重复渲染，结果保持确定。
-- =============================================================

SET NAMES utf8mb4;

ALTER TABLE conversations
  ADD COLUMN layer1_from_msg_id BIGINT UNSIGNED NULL
    COMMENT '层1(原文)起点;此 id 之后原样,之前渲染成半压形态'
    AFTER summary_upto_msg_id;

-- 分段摘要一段一行、只追加：每段原文只经历一次有损压缩，
-- 避免滚动重写时旧事实被反复摘要而逐轮丢失。
CREATE TABLE IF NOT EXISTS conversation_summaries (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  conversation_id BIGINT UNSIGNED NOT NULL,
  seq             INT             NOT NULL COMMENT '第几段,从 1 开始',
  from_msg_id     BIGINT UNSIGNED NOT NULL COMMENT '这段覆盖的消息区间,闭区间',
  upto_msg_id     BIGINT UNSIGNED NOT NULL,
  content         TEXT            NOT NULL,
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),

  -- 同一会话内 seq 唯一，既确定分段顺序，也便于按序读取最近若干段。
  UNIQUE KEY uk_conv_seq (conversation_id, seq),

  -- 支持按会话 + 覆盖终点定位摘要段，反查某条消息已落入哪一段摘要。
  KEY idx_conv_upto (conversation_id, upto_msg_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分段摘要,一段一行只追加';