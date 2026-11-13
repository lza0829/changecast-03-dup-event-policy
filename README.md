# 04-changecast（变投影）

Changecast — 变更日志投影：源变更流 → 按序投影 → 乱序/晚到策略钩子 → 快照可查互证。

与 Feedstitch（多源 Feed 归并）、Batchgate（作业依赖编排）、Syncledger（双端对账）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

## 主链

配置投影流 → 读取 CDC 夹具并按 seq 应用到投影表 → 批次计数与快照互证

## 种子投影流

- **member_profile**：会员档案变更（`member_id`，字段 `display_name` / `tier` / `region`）
- **store_archive**：门店档案变更（`store_id`，字段 `name` / `status` / `city`）
- **channel_attr**：渠道属性变更（`channel_code`，字段 `label` / `active`）

底座默认按 **事件 seq 升序** 投影；乱序缓冲表空挂，深逻辑留给后题。对外文案不用「工单」「CRM」。

## 重复 event_id 策略（event_idempotency）

每条投影流配置互斥的重复策略，开跑入口读取并在本趟冻结（批次进行中改策略不改写本趟结果，批次行留冻结快照）。重复判定先对载荷做键序无关的规范化 JSON + SHA-256 摘要：

- **策略甲 · 幂等（`idempotent`）**：重复 event_id
  - 摘要相同 → 跳过并计「跳过重复」，**不得二次改投影**；
  - 摘要不同 → 记「摘要冲突」，按流配置的冲突结局（二选一写死贯彻）：
    - `rollback_batch`：整批失败，投影回到本趟前，水位不前移；
    - `quarantine`：冲突事件隔离（入 `conflict_events` 留痕、不进投影），本趟其余事件继续。
- **策略乙 · 整批拒绝（`reject_batch`）**：凡重复 event_id 一律整批拒绝、投影回到本趟前，**不区分摘要**。

拒绝/回滚在单事务内提交账本（批次 + 事件留痕 + 冲突留痕）但不写投影，杜绝半截成功冒充完成；跳过数、摘要冲突数、整批拒绝次数、投影终态与留痕互证。**不是请求队列入队键去重。**

### 页面演示（dup_demo 流，夹具 `dup_demo.json`）

夹具内含相同载荷重投（`dd-002`/`dd-001`）与同 event_id 不同载荷（`dd-003` 被篡改）：

1. 默认「幂等 + 隔离」跑一趟 → 看到跳过重复、冲突隔离、其余照常投影；
2. 「投影流」页切到「幂等 + 整批回滚」再跑 → 整批拒绝，投影快照零变化；
3. 切到「整批拒绝」再跑 → 首个重复 event_id 即拒绝，与摘要无关。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 测试

```bash
cd backend && pytest tests          # 12 项：引擎策略 / 服务事务 / 策略快照冻结
```

## 0-1 切口（未实现，见出题规划）

`arrival_vs_event_order` / `ooo_window` / `tombstone_policy` / `watermark_rewind` / …
