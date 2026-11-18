# 002 跨月抄表（带时区累计读数）

## 新增表（SQLAlchemy 启动自动创建；已存在的库请先备份再手工执行等价 DDL）

- `meters`：表计主数据。`code` 唯一；`tz_name` 为 IANA 时区；`replaces_id` 自引用指向被换表；`rebuild_generation` 单调递增，作为重算代次。
- `meter_readings`：累计读数。`read_at` 为 `timestamptz`（统一存 UTC），`tz_name` + `utc_offset_min` 保存读数时刻的表时区墙钟与偏移快照；`cumulative` 为 `numeric(18,6)`；`kind ∈ regular/change_out/change_in`；`(meter_id, read_at)` 唯一。
- `month_factors`：按月冻结因子。`month` 唯一（YYYY-MM）；`frozen` 随封账置真，冻结后拒绝覆盖。
- `month_closes`：月份封账标记，主键即月份。
- `meter_month_allocations`：表×月分摊。`(meter_id, month)` 唯一；`raw` 月原量、`factor`、`emissions`；`generation/job_id` 记录由哪一代哪份作业写入。
- `allocation_adjustments`：封账差额冲补。`idem_key` 全局唯一（`meter|source_month|job|generation`）保证重试不重复；记录来源月、承接月、差额与原因。
- `meter_jobs`：重算作业队列。`status ∈ queued/running/completed/failed`，`generation`、`claimed_by`、`attempts`、乐观 `version`。

## 关键口径

1. 月界在表时区本地生成，相邻读数差按 UTC 真实秒数加权拆月；夏令时 23/25 小时自动计入。Decimal 6 位量化，余量归入时长最大片，各片之和严格等于段差。
2. 累计量倒退、时刻非严格递增、缺换表出/换表入边界一律拒绝入账（422），事务回滚不留半截数据。
3. 开放月重算直接替换原核算；已封账月原值冻结，差额经冲补表结转到下一开放月；承接月随后封账则历史冲补锁定，再更正只补剩余差额（收敛，不逐代累加）。
4. 并发：同表作业以 `pg_advisory_xact_lock` 串行（SQLite 降级为行锁），认领用 `FOR UPDATE SKIP LOCKED`；代次过期的执行者写入被跳过；崩溃作业由超时回收器放回队列；冲补唯一键幂等。

升级仅新增表，不改既有表；回滚需先删除上述七张表（按外键逆序）。
