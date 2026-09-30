-- =============================================================
-- ch07 · 会话上下文管理 · 建表 DDL（第一步，共两步）
-- 为 conversations 增加摘要投影及其覆盖边界。
-- 分层所需的 layer1_from_msg_id 与分段摘要表在 ch07-layers.sql，
-- 两个 DDL 文件需配套执行。
-- =============================================================

-- 确保中文 COMMENT 按 utf8mb4 解析，避免 latin1 默认连接导致中文 double-encode。
SET NAMES utf8mb4;

ALTER TABLE conversations
  ADD COLUMN summary             TEXT            NULL COMMENT '最近几段梗概拼成的投影,拼装时跟证据一起挂在用户那句之后' AFTER status,
  ADD COLUMN summary_upto_msg_id BIGINT UNSIGNED NULL COMMENT '摘要已覆盖到哪条消息,滑窗从其后接原文' AFTER summary;
