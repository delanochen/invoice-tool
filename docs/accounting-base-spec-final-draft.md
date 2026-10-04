# 会计底座规格・最终草案（评审稿）

> **版本标识：**
> `ACCT-SPEC-DRAFT-v1.4`
> **（WorkBuddy 复核修订，2026-10-03）**
> 　｜　
> **同一文件连续修订**
> ，本版替代 v1.2 /v1.3 文本（未新建文件）
> **v1.2 未通过的原因**
> （本轮已处理）：v1.2/v1.3 的复核章节只把五个问题
> **指向**
> 既有条款，正文并未落实；且正文存在与这五个场景
> **直接冲突**
> 的旧条款（见 §9.0 删除清单 D1–D7）。本轮
> **直接替换正文与验收、删除冲突旧条款**
> ，不再把未修正内容写成 "已确认"。
> 基线快照（
> **仅供参考，不得沿用**
> ）：核对时 HEAD
> `ab33167`
> ；
> `migrations/postgresql/`
> 当前最大编号
> `0297`
> （新迁移自
> `0298`
> 起顺延，实施时再取当时最大值 +1）。
> **实施前必须重新核对当前仓库**
> ：所有
> `文件:行号`
> 与迁移编号都是快照，
> **不作为施工依据**
> ；开工前按 §0.1 清单重取并回写本文。
> **本文是评审产物，不是开发授权。未写任何代码。**
> 冻结数据（C4 correction group 2 /replacement SL / 旧 superseded SL / 历史 missing 组件 / Tax Review / PB-2610-0001）与 P7（2026-09-28~10-11）保持不变；
> **不执行生产查询、不执行迁移、不动冻结数据、不动 P7、暂不编码、暂不合并**
> 。
> 两个抵扣方案
> **并列保留**
> ，本轮
> **不要求用户裁决**
> ；裁决点见附件三，仅作为实施前确认项。
> 标注：【事实】= 已核代码；【修订】= 本轮相对上一版改动；【待决】= 需用户裁决（不阻塞本稿评审）。



***

## 0. 术语与方案命名



| 名称         | 短码                  | 含义                                                           |
| ---------- | ------------------- | ------------------------------------------------------------ |
| **即时抵扣方案** | `OFFSET_AT_READY`   | 借款抵扣在付款单 \*\* 进入待付款（`pending_payment`）\*\* 时生效并过账；取消时以反向凭证释放 |
| **预留抵扣方案** | `OFFSET_AT_PAYMENT` | 待付款阶段仅**预留**（表外、可审计、不进总账）；抵扣在 \*\* 实际付款（`paid`）\*\* 时生效并过账   |

不再使用 A/B 称呼。

**【事实・批次路径的既有行为】** `_create_advance_application` 在单张流程由 `transition` 的 ready 分支调用（`employee_finance.py:1557``-1558`）；批次发放路径只对 `status='approved'` 的付款单补调用，随后**同一循环内**立即置 `paid`（`1777-1786`）。即：**批次路径下抵扣与付款本就同时发生**，两方案的差异只在「单张流程的待付款窗口」显现。

### 0.1 实施前核对清单（行号与迁移编号不得沿用本文快照）

开工前逐项重取，并把结果回写到本文（替换 §2/§3 / 附件二中的旧值）：



| # | 核对项                                                                                                                                                                                                      | 取数方式                                             |
| - | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| 1 | 当前 `git rev-parse --short HEAD` 与 `VERSION`                                                                                                                                                              | 替换本文基线快照                                         |
| 2 | `employee_finance.py` 中 `_advance_balances` / `_create_advance_application` / `_reverse_advance_application` / `transition_employee_payment` / 批次发放 / `void_payment_batch` / `bank_reconciliation` 的当前行号 | 全文检索函数名                                          |
| 3 | `app.py` 中 `update_invoice_from_form` / `mark_invoice_paid` / `unmark_invoice_paid` / `approve_expense` / `reset_workflow` / `invoice_totals` 的当前行号                                                      | 同上                                               |
| 4 | `migrations/postgresql/` 当前最大编号（本次核对为 `0297`）                                                                                                                                                            | 新迁移取 max+1                                       |
| 5 | `database.py` 中 `REQUIRED_PRODUCTION_TABLES` 与 `pg_advisory_xact_lock` 行号                                                                                                                                | 同上                                               |
| 6 | 关键约束现状：`employee_payment_orders`（`source_key` / `payment_number` /net 恒等式）、`bank_transactions`（双 `matched_*` / `import_fingerprint`）、`employee_advance_applications`（`entry_type` CHECK、无业务唯一键）          | 只读 SQL 查 `pg_constraint`                         |
| 7 | 运行角色是否拥有底座表属主与 `TRIGGER` 权限                                                                                                                                                                              | `pg_class.relowner` + `has_table_privilege`      |
| 8 | 新表名不与既有表冲突（已核：`receipt_allocations` / `customer_receipts` / `customer_prepayments` / `voucher_settlement_lines` / `advance_reservations` / `posting_events` 当前**均不存在**）                                  | 检索 `migrations/` 与 `tests/schema_postgresql.sql` |



***

## 1. 六项基础修订（沿用 v1.1 已一致部分，本轮仅在冲突处改写）

### R1 付款事实 与 银行匹配 分离

**【事实】** 付款事实在 `mark_paid` 时**已完整落库**，与银行流水无关：`transition_employee_payment` 的 `mark_paid` 分支写入 `bank_account_id` / `payment_method` / `external_transaction_id` / `paid_by` / `paid_at`（`employee_finance.py:1564``-1571`）；批次路径写入 `batch_id` + 同组字段（`1779-1786`）。**银行匹配发生在之后**（`bank_reconciliation`，`2043-2105`），只改 `bank_transactions.matched_*` + `sync_status` 与付款单 `status='reconciled'` + `reconciled_at/by`。



| 概念       | 触发                              | 产生的东西                                                     | 是否出分录              |
| -------- | ------------------------------- | --------------------------------------------------------- | ------------------ |
| **付款事实** | `payment.paid` / `batch.issued` | 付款凭证（Dr 应付 / Cr 银行）                                       | **是**              |
| **银行匹配** | `bank.matched`                  | 匹配关联（`bank_transactions.matched_*` + `sync_status`），可回链凭证 | **否**（仅建立关联）       |
| **解除匹配** | `bank.unmatched`                | 清除关联 + 回退对账状态                                             | **否**（不新增、不撤销任何分录） |



* 付款凭证**不因未匹配而不出**，也**不因解除匹配而被撤销**。银行科目余额 = 付款事实合计；未匹配只影响对账状态，不影响账面。

* 三件事不可共用一个动作：**取消支付安排**（未实付，冲抵扣、债务保留）/ **解除银行匹配**（只解关联）/ **实际退款**（真实资金回流，出退款凭证）。

* 验收：解除匹配后 `vouchers`/`voucher_entries` 新增行数 == 0；不存在 `status='reconciled'` 且 `matched_* IS NULL` 的付款单或批次。

### R2 事件版本 与 内容哈希 分离



| 字段               | 定义                                                                                  | 变化意味着                  |
| ---------------- | ----------------------------------------------------------------------------------- | ---------------------- |
| `source_version` | **业务对象版本**：付款单金额版本（`gross/net/advance_offset` 的修订序号；reopen 会改金额）、报销 ER 重开次数、发票快照版本号 | **新的合法业务事实** → 允许产生新凭证 |
| `payload_hash`   | **过账内容哈希**：分录（科目 + 方向 + 金额 + 业务日期 + 会计日期 + 币种 + 来源行）规范化序列化后的 SHA-256                | 同一键下内容变了 → **冲突，阻断**   |

二者**不可互换、不可合并**。判定顺序：



1. 按 `(source_type, source_id, source_version, event_type, occurrence_no)` 查 `posting_events`；

2. 命中 → 比较 `payload_hash`：**相同 → 返回既有凭证 id（不新增、不报错）**；**不同 → 阻断并报&#x20;**`payload_conflict`；

3. 未命中 → 校验业务事实锚点（R3）后过账。

### R3 重复请求不得通过新序号重复过账



* `occurrence_no` **由业务状态推导，不由请求次数推导**。定义为该业务键上「已发生的业务事实次数」—— 抵扣取该 `(advance_id, payment_order_id)` 已存在的 `entry_type='application'` 行数 + 1；收款取该发票已有收款记录数 + 1。**投递次数不影响序号。**

* 每次过账请求必须携带**业务事实锚点** `business_anchor`（本事务中新落库的业务行 id：application 行 /receipt 行 / 分配行 / 核销行）。

* 客户端**不得**自定义 `occurrence_no`；重试语义由 `Idempotency-Key` 承载，只作用于同一请求的重投。

> 本条判定、业务行零新增规则与验收以
> **§8.1**
> 为最终条款。

### R4 分配按累计剩余余额校验

新增分配一律按下式校验（不是与单笔目标额比较）：



```
本次分配金额 ≤ 目标总额 − Σ(该目标上 active 分配) − Σ(本事务内尚未提交的分配)
```



* 四类目标：发票收款（目标 = `invoice_amount_snapshots.total`）、**应付核销（目标 = 被核销确认分录金额，见 §2.5）**、批次成员（目标 = 付款单 `net_amount`）、**预收核销（目标 = 该客户预收余额，见 §8.6）**。

* 事务起始对目标行加锁（`SELECT ... FOR UPDATE` 或沿用 `pg_advisory_xact_lock`，`database.py:184`），避免并发双花。

* 分配行**不可修改**：调整走「原分配行冲销 + 新增分配行」，保留历史。

* `CHECK(amount > 0)`；同一收款对同一发票只允许一条 active 分配行（部分唯一索引）。

> 本条规则与验收以
> **§8.2**
> 为最终条款。

### R5 已过账凭证不得绕过状态保护

保护对象：`vouchers`、`voucher_entries`、`voucher_source_links`、`voucher_attachment_links`、`voucher_settlement_lines`。

**各表的保护形态不同，见 §8.3**（`vouchers` 不是 "禁 UPDATE"，而是 "禁 DELETE + 除状态单向迁移与审计列外禁改"）。



```
-- 分录表：posted 凭证禁 INSERT / UPDATE / DELETE
create function protect_posted_entries() returns trigger as $$
begin
  if exists (select 1 from vouchers
             where id = coalesce(NEW.voucher_id, OLD.voucher_id)
               and status = 'posted') then
    raise exception 'posted voucher entries are immutable';
  end if;
  return coalesce(NEW, OLD);
end $$ language plpgsql;
create trigger voucher_entries_protect
  before insert or update or delete on voucher_entries
  for each row execute function protect_posted_entries();

-- 状态守卫：posted 只能单向到 reversed
create function guard_voucher_status() returns trigger as $$
begin
  if OLD.status = 'posted' and NEW.status not in ('reversed') then
    raise exception 'illegal voucher status transition: % -> %', OLD.status, NEW.status;
  end if;
  return NEW;
end $$ language plpgsql;
create trigger vouchers_status_guard
  before update of status on vouchers
  for each row execute function guard_voucher_status();
```

**防绕过**



1. 状态变更只经 `post_voucher()` / `mark_reversed()` 两个函数，触发器兜底；

2. 每次状态变更写 `posting_audit`（操作人、时间、前值、后值、原因码）；

3. 【事实】运行角色 `invoice_app` **无 DDL 权限**；【待验证】其非表属主，`ALTER TABLE ... DISABLE TRIGGER` 应被拒绝 —— 实施前须用只读 SQL 核验 `pg_class.relowner` 与角色权限，若不成立需改由属主授权模型；

4. 更正与冲销一律**新增凭证**，永不改既有行。

### R6 预留事件可审计，不要求所有操作都进入总账



* `posting_events.requires_gl boolean not null`：显式声明该事件是否产生分录。

* 只有**预留抵扣方案**需要 `requires_gl=false` 的预留事件；它仍写入 `posting_events` 与 `posting_audit`（操作人、时间、金额、关联借款与付款单），并提供非账查询入口（"某借款在某时点被哪些付款单预留多少"）。

* 通用规则：`requires_gl=false` 的事件**不得影响任何科目余额**。验收：事件前后全科目余额快照完全一致；且该事件不出现在任何凭证分录中。



***

## 2. 过账事件（确认 / 抵扣 / 付款 / 匹配 四类分别定义）

四类事件的**业务键、版本语义、是否入账、分录**各不相同，**不得混用同一套键，也不得互相代偿**：

抵扣类永不产生银行分录；付款类永不产生借款分录；匹配类永不产生任何分录。

> 下表的触发点写
> **函数名**
> （行号与迁移编号按 §0.1 在实施前重取，本文旧值不沿用）。

### 2.0 过账范围（白名单）

首期过账范围：**客户发票及收款、工资确认、报销审批、员工付款与借款**；USD 单币种。

**明确不过账**：日报、AI 日报草稿、付款草稿、报价单（quotation）、客户结算草稿；**P7（2026-09-28\~10-11）挂起期间不产生任何凭证**（冻结要求，相关路由后凭证新增 == 0）。

### 2.1 确认类（Recognition）—— 确认债权 / 债务 / 预收负债



* 业务键：`(source_type, source_id, source_version, event_type, 1)`；同一版本下确认只发生一次，序号恒为 1。

* `source_version` = 金额版本（发票快照版本号 / 付款单 gross-net 修订序号 / ER 重开次数）。



| 事件                                         | 触发点                                      | `requires_gl` | 分录                              |
| ------------------------------------------ | ---------------------------------------- | ------------- | ------------------------------- |
| `invoice.confirmed`                        | `app.py` `update_invoice_from_form`      | 是             | Dr 应收 / Cr 收入 + 销项税（**取快照**）    |
| `invoice.receipt{n}`                       | `mark_invoice_paid`（需改造为可多次）             | 是             | Dr 银行 / Cr 应收                   |
| `invoice.receipt_reversed`                 | `unmark_invoice_paid`（需补门禁与事件）           | 是             | 反向                              |
| `customer.prepayment_received`             | **新增**（收款但无已确认应收）                        | 是             | **Dr 银行 / Cr 预收账款（合同负债）；不确认收入** |
| `prepayment.applied`                       | **新增**（发票确认后以预收抵应收）                      | 是             | **Dr 预收账款 / Cr 应收**             |
| `payroll.confirmed`                        | `employee_finance.py` `generate-payroll` | 是             | 按组件→科目映射分类 Dr / Cr 应付员工 gross   |
| `payroll.corrected`                        | 新增                                       | 是             | 差额调整（**不含银行科目**）                |
| `expense.approved`                         | `approve_expense`                        | 是             | 费用 / 资产类 Dr / Cr 应付员工           |
| `expense.reopened`                         | `reset_workflow`（需加状态白名单）                | 是             | 全额冲销                            |
| `opening.established` / `opening.adjusted` | 新增                                       | 是             | 期初与调整                           |

**权责发生制硬规则**：资金到账（`customer.prepayment_received` / `invoice.receipt{n}`）**永不直接确认收入**；收入只在 `invoice.confirmed` 确认。

### 2.2 抵扣类（Offset）—— 以借款清偿应付



* 业务键：`(advance_id, payment_order_id, source_version, event_type, occurrence_no)`。

* `occurrence_no` = 该 `(advance_id, payment_order_id)` 上已存在的 `entry_type='application'` 行数 + 1（**由业务状态推导，不由投递次数推导**）。



| 事件                                | 触发点                            | `requires_gl`   | 分录                |
| --------------------------------- | ------------------------------ | --------------- | ----------------- |
| `advance.offset_applied`          | `_create_advance_application`  | 是               | Dr 应付员工 / Cr 员工借款 |
| `advance.offset_reversed`         | `_reverse_advance_application` | 是               | Dr 员工借款 / Cr 应付员工 |
| `advance.offset_reserved`         | 仅预留方案：ready 分支                 | **否**（可审计，不进总账） | —                 |
| `advance.offset_reserve_released` | 仅预留方案：取消 / 作废 / 超时清理           | **否**（可审计，不进总账） | —                 |

### 2.3 付款类（Payment）—— 付款事实与资金回流



* 业务键：单张 `(payment_order_id, source_version, event_type, occurrence_no)`；批次 `(batch_id, source_version, event_type, occurrence_no)`。

* **与匹配类完全独立**：付款凭证由付款事实触发，不因未匹配而不出、不因解除匹配而撤销（R1）。



| 事件                                | 触发点                               | `requires_gl` | 分录                                                                                                                 |
| --------------------------------- | --------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------ |
| `payment.ready`                   | `transition` ready 分支             | **否**（仅状态）    | —                                                                                                                  |
| `payment.paid` / `batch.issued`   | `transition` mark\_paid 分支 / 批次发放 | 是             | Dr 应付员工 **net** / Cr 银行 **net**                                                                                    |
| `payment.cancelled`               | `transition` cancel 分支            | 视是否已过账        | **未实付**：已过账的**抵扣**凭证必须出冲销凭证（Dr 员工借款 / Cr 应付员工）；**应付确认凭证不得冲销（债务保留）**；无任何已过账凭证时仅状态回退、零凭证。**已实付（paid/reconciled）：禁止** |
| `payment.voided` / `batch.voided` | 见 **§8.4**                        | 视是否已过账        | 未实付：冲销抵扣凭证（若有），应付债务保留；已实付：**拒绝**                                                                                   |
| `payment.refunded`                | 新增（须有退款依据）                        | 是             | Dr 银行 / Cr 应付或员工往来                                                                                                 |

**取消 / 作废永不产生银行反向分录**—— 未实付时本就不存在银行分录。

### 2.4 匹配类（Matching）—— 银行流水与业务的关联



* 业务键：`(bank_transaction_id, target_type, target_id, event_type, occurrence_no)`。

* `requires_gl`**&#x20;恒为否**：匹配与解除匹配均不产生、也不撤销任何分录，只维护关联与对账状态。

* **解除匹配不改变 "已实付" 事实**：`bank.unmatched` 后单据仍是已实付，仍不可取消 / 作废，资金回流只能走 `payment.refunded`。



| 事件               | 触发点                   | `requires_gl` | 分录 |
| ---------------- | --------------------- | ------------- | -- |
| `bank.matched`   | `bank_reconciliation` | 否             | —  |
| `bank.unmatched` | **新增路由**              | 否             | —  |

### 2.5 核销行（Settlement Line）—— 清偿对确认分录的占用

**【修订・新增】** 核销（应收 / 应付被清偿）必须以**核销行**显式记录，不能只由分录隐含。

`voucher_settlement_lines(id, voucher_id, voucher_entry_id, target_type, target_id, amount, status, reversed_by, redirected_to, currency, source_event_id, created_at)`



* `voucher_entry_id` = **被核销的确认分录**（应付确认的贷方行 / 应收确认的借方行）；`target_type ∈ {payable, receivable, prepayment}`。

* 约束：`CHECK(amount > 0)`；`UNIQUE(voucher_entry_id, source_event_id)`（同一事件对同一分录只一条核销行，重投不新增）。

* **目标额 = 被核销确认分录的金额**（应付 500 / 应收 1000 / 预收 600）。

* 校验走 §8.2 累计剩余余额；**超额整笔拒绝**，不部分吸收。

* **抵扣与付款各写各的核销行，互不重复计数**：`advance.offset_applied` 写一条（金额 = 抵扣额），`payment.paid` 写一条（金额 = net 实付额），两者目标同一应付分录，累计占用 = 目标额。

* 余额口径：应付余额 = 确认额 − Σ active 核销额；应收余额 = 确认额 − Σ active 核销额。

* 核销行**不可 UPDATE**：调整 = 原行标 `reversed` + 新增行；重定向 = 原行标 `redirected_to` + 新增行。

* 核销行属已过账保护范围（R5 / §8.3）；冲销核销行须与冲销凭证**同一事务**。

**组件→科目映射**：扩展 `payroll_component_tax_config(component_code, tax_category)`（`invoice_tool/payroll/``services.py`）为 `component_account_map(component_code, tax_category, account_code, effective_from)`。**不默认 gross 全是费用、net 全是应付。**



***

## 3. 借款抵扣：两方案对照（仅五个维度）

统一例子：期初借款 500、应付 500（V1 确认）、抵扣 300、银行实付 200；阶段序列 **待付款 → 取消 → 重新待付款 → 实际付款**。

口径记号：`U` = 抵扣前上限 500，`B` = 账面余额，`A` = 可用余额（定义见 §8.5）。



| 维度         | 即时抵扣方案 `OFFSET_AT_READY`                                                      | 预留抵扣方案 `OFFSET_AT_PAYMENT`                                                                                                               |
| ---------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **业务生效语义** | 进入待付款即「以借款清偿应付」的债务抵消生效，借款权利**即时减少**；取消须以反向凭证释放                                | 待付款只是支付安排，**债务抵消在实际付款时生效**；取消只释放预留，无账可冲                                                                                                  |
| **余额展示**   | 待付款窗口：`U`500 / `B`**200** / `A`**200**（预留恒 0，故 `A ≡ B`）；批次路径抵扣与付款同点发生，此处差异不显现 | 待付款窗口：`U`500 / `B`**500** / 预留 300 / `A`**200**；付款后 `B`200 / 预留 0 / `A`200。`A = B − Σ 有效预留`                                              |
| **历史兼容**   | 现有 `application` 行的语义与时点**完全不变**；legacy 行可直接锚定期初（`legacy_anchor`），无需分类        | 现有 application 行**时点不统一**：单张流程在 `transition` 的 ready 分支写入，批次流程在批次发放处写入且与 `paid` 同点。统一切到 "付款才抵扣" 后，启用日仍在待付款的历史单需判定为「已抵扣」或「回填预留」，否则启用后账面跳变 |
| **事务安全**   | 抵扣与状态转换同事务，失败即回滚；余额校验在 `_create_advance_application` 内部、事务中完成                 | **两段事务**：ready 写预留、paid 写抵扣；中间态需一致性对账与过期清理；并发下预留与抵扣需各自防重复（加锁口径见 §8.2）                                                                    |
| **实际改造范围** | 见附件二清单（不改 `_advance_balances`、不改借款页口径）                                        | 见附件二清单（需改 `_advance_balances` 口径、改借款页显示、新增预留表与清理任务、legacy 分类回填）                                                                          |

**两方案终态完全相同**：借款 200 / 应付 0 / 银行 −200 / 费用 500。

**旧 application 兼容（两方案通用）**

【事实】`employee_advance_applications` 8 列，仅 PK + 2 FK + `CHECK(amount<>0)` + `CHECK(entry_type IN ('application','reversal'))`，**无业务唯一键**。



| 路径             | 处理                                                                                              |
| -------------- | ----------------------------------------------------------------------------------------------- |
| 执行（启用后）        | 写 application 行 + `posting_event_id` + `occurrence_no`；出抵扣凭证 + **核销行**（§2.5）                    |
| 撤销（非 legacy）   | 出反向凭证，`reversal_of=<原 voucher_id>`；写负额 reversal 行；**同事务冲销对应核销行**                                |
| 撤销（**legacy**） | **仍出反向凭证**（Dr 借款 / Cr 应付），挂 `corrects_opening_line` + 原因码 `legacy_reversal`—— 该抵扣已被期初吸收，不冲则双向虚增 |
| 重新执行           | 新 application 行，`occurrence_no` +1，新 `event_uid` → 新凭证；**不覆盖旧行**                                |

硬规则：`reversal_of` 与 `corrects_opening_line` **必填其一**；**已实付（paid/reconciled）单据的抵扣不可单独撤销**—— 须经 `payment.refunded` 或更正凭证配套，不直接 reverse；已过账抵扣的撤销 = 冲销凭证 + 负额 reversal 行 + 核销行冲销，**同一事务**。

**迁移与回填属写入**；`backfill_advance_posting_anchors.py` 默认 **dry-run 零写入**，`--apply` 才写；**不为 legacy 行补生成凭证**，其累计由期初借款科目一次性吸收。



***

## 4. 期初、关账、附件



* **期初**：`accounting_opening_lines`（`UNIQUE(origin_type, origin_id)`）+ `cutover_date`；原期初凭证不可改；**方向按科目&#x20;**`normal_balance`：资产类（应收 / 员工借款 / 银行）**借 科目 / 贷 期初结转**，负债与权益类（应付员工款 / 税款负债 /**预收账款（合同负债）**/ 权益）**借 期初结转 / 贷 科目**；**增加应付必须贷记应付**（Dr 期初调整 / Cr 应付），资产增加镜像（Dr 科目 / Cr 期初调整），**禁止科目侧反转**；原因码必填 `prior_period_error` / `post_cutover_new` / `cutover_reclassification`，不得仅凭重开日期归类。**期初必须包含启用日的未核销预收余额**。

* **关账**：自然月，与 14 天工资网格解耦；`open → closed` 后禁止新增过账（含冲销）；更正记入开放期间；重开账需独立权限 + 留痕。

* **冲销报表口径**：冲销凭证会计期间 = 当前开放期间（不重开已关期间）；报表按**期间净额**列示（原凭证行正、冲销行负、同科目合计净）；跨期更正差异全部记入开放期间，必带原因码与来源凭证引用；报表金额从 `source_snapshots` 取因子精度计算（行金额最终舍入、合计 =Σ 行），不重算可修改的来源表。

* **附件**：【事实】8 张 `*_attachments` 表中**仅&#x20;**`expense_attachments`**&#x20;有&#x20;**`file_sha256`**/**`image_dhash`。统一加 `content_sha256` / `row_version` / `deleted_at`；删除改标记删除 + 后台清理；`voucher_attachment_links` 保存**快照式证据**（sha256、stored\_filename、byte\_size、row\_version、captured\_at），不靠可改的源表还原历史。



***

## 5. 启用与结构自检（零写入）



| 状态         | 判定                               | 行为                                                   |
| ---------- | -------------------------------- | ---------------------------------------------------- |
| 未启用        | `accounting_base_enabled <> '1'` | 完全保留旧业务行为，不引用底座表                                     |
| 已启用 + 结构正常 | 开关 = 1 且自检通过                     | 正常过账                                                 |
| 已启用 + 结构异常 | 开关 = 1 且自检失败                     | **在业务写事务内 raise 阻止提交**，明确提示缺失项；不静默跳过、不降级为 "业务成功但漏记账" |



* `check_accounting_base.py` **只读**（`information_schema` + `pg_constraint` + `pg_trigger`），**零数据库写入**；输出 stdout 或 `--json <path>` 落盘。

* **保存报告是独立操作**：`save_accounting_base_report.py`` --from <json>` 才写 `accounting_base_check_report`；**绝不修改&#x20;**`accounting_base_enabled`。

* 底座表**禁止**进 `REQUIRED_PRODUCTION_TABLES`（`database.py:14``-21`，加则启动失败）。

* 生产只读核验须显式 `BEGIN READ ONLY` 并回读 `SHOW transaction_read_only`。

* 借贷平衡：`CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`，在 COMMIT 时校验**实际分录行** `sum(debit)=sum(credit)`。



***

## 6. 尾差（统一算法）



1. 行金额 = `ROUND_HALF_UP(qty × rate)`（因子保留来源精度，只在最后量化一次）；**合计 = 行金额精确求和，不再二次量化**。

2. 目标金额分配：向下取分 → **按小数余数降序**分配剩余分（负差额按**余数升序**扣减）→ 稳定字段（科目码 → `service_date` → `source_line_id`）**仅用于并列**。

   实测（8.25%，137.42 / 268.19 / 394.39，合计 800.00）：精确值 11.33715 / 22.125675 / 32.537175；floor 11.33 / 22.12 / 32.53 = 65.98；余数（分）0.7150 (行 1) / 0.5675 (行 2) / 0.7175 (行 3) → 降序取行 3、行 1 各加 1 分 → **11.34 / 22.12 / 32.54 = 66.00**；负差目标 65.97 → 扣最小余数行 2 → 22.11；目标 66.05 需 7 分 > 3 行 → **阻断**。

3. **目标必须有依据**；无法解释的源金额差异 → 阻断 `rounding_unexplained`，**不进尾差科目自动吸收**。

4. **手续费不是尾差**：独立入 "银行手续费" 真实费用科目（Dr 手续费 / Cr 银行）。



***

## 7. 延期候选（保留，重新打开需给理由）



| 候选         | 现状                                                                              | 重新打开的条件（须给出以下任一理由）                                                |
| ---------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **员工部分付款** | 单 `net_amount`；现实多为整单付；已用「两张工资单 600 已付 / 400 更正为 300」验收                         | ①出现 "同一付款单分两次实付" 的真实业务；②出现批次合计与成员单 net 不一致的对账需求；③出现未付余额需跨期保留的审计要求 |
| **银行分拆匹配** | 严格 1:1（双 UNIQUE + 金额严格相等 `employee_finance.py:2069-2071`）；已规划 unmatch + 手续费独立入账 | ①出现一笔流水核销多张非批次单据；②出现带手续费 / 差额的核销需求；③出现一单一笔以上流水的分拆收款               |



***

## 8. 修订清单（原条款 → 修改条款 → 验收条件）

> 只修订本文件既有条款，
> **不另起规格**
> 。
> 行号与迁移编号均为旧基线快照，实施前按 §0.1 重取。

### 8.1 重复请求（优先级 1）



* **原条款**：§1-R3 只写了 "`occurrence_no` 由业务状态推导、请求须带 `business_anchor`"，未规定：事件键命中后如何处理" 自带更大序号 " 的重投；`Idempotency-Key` 与业务键的关系；并发下两个请求同时计算序号会怎样；**业务行会不会被重复写入**。

* **修改条款**：

1. **三段判定**：先按业务键查 `posting_events` → 命中则**忽略请求自带序号**，直接比较 `payload_hash`（同 → 返回既有 `voucher_id`、零新增；异 → 阻断 `payload_conflict`）；

2. 未命中才计算 `occurrence_no = 业务事实计数 + 1`，**且必须由&#x20;**`business_anchor`**（本事务新落库的业务行 id）佐证**，无锚点 → 阻断 `no_business_fact`；

3. 序号计算与写入在**同一事务**内完成，并对业务对象取 `pg_advisory_xact_lock`，禁止两个并发请求取得同一序号；

4. `Idempotency-Key` 只用于 HTTP 层重投去重，**不参与业务键、不用于生成新序号**；

5. **【修订・新增】业务行也必须零新增**：业务行写入与过账**同一事务**；事务内先按业务键查 `posting_events`——**命中同哈希则不写任何业务行**，直接返回既有 `voucher_id` 并结束事务（净写入为空）；未命中才写业务行 → 过账 → 写事件。若写完业务行后过账判定发现事件已被并发事务抢先提交，**整事务回滚**（业务行一并撤销），客户端收到既有 `voucher_id`；

6. **【修订・新增】业务行自身必须带业务唯一键**，杜绝重投产生第二行：application `UNIQUE(advance_id, payment_order_id, occurrence_no)`；receipt `UNIQUE(customer_id, receipt_no)`；allocation 与核销行用**部分唯一索引**（`WHERE status='active'` / `UNIQUE(voucher_entry_id, source_event_id)`），保证冲销 / 重定向后仍可重做；唯一键冲突时返回既有行 id，不抛 500。

* **验收条件**：


  * 同键同哈希重投 100 次 → `vouchers` +0、`voucher_entries` +0、`posting_events` +0、**业务行（application /receipt/allocation/settlement\_line）+0**，返回同一 `voucher_id`；

  * 同键异哈希 → 事务回滚并报 `payload_conflict`，不产生凭证、**不产生业务行**；

  * 请求自带 `occurrence_no=9` 但无新业务行 → 阻断 `no_business_fact`；

  * 并发 10 个同内容请求 → 只产生 1 张凭证、**业务行 1 行**（不是 10 行），其余返回同一 id。

### 8.2 分配余额（优先级 2）



* **原条款**：§1-R4 给出公式，但未定义 "已确认分配" 的口径、冲销 / 重定向后占用如何释放、目标行的加锁时机，也未规定部分超额是整笔拒绝还是部分吸收；第 7 点把 "收款未分配余额" 一律判为阻断（**与 §8.6 客户预付款冲突，已删除**）。

* **修改条款**：

1. **已确认分配 = 状态为&#x20;**`active`**&#x20;的分配行之和**；被冲销（`reversed`）或已重定向（`redirected_to` 非空）的行**不计入占用**；

2. 校验恒用**累计剩余余额**：`本次 ≤ 目标总额 − Σ active − Σ 本事务未提交`；

3. **超出即整笔拒绝并回滚**，不做部分吸收、不静默截断；

4. 分配行**不得 UPDATE**：调整 = 原行标 `reversed` + 新增行；重定向 = 原行标 `redirected_to` + 新增行，原行保留；

5. 同一目标上的分配**不得跨币种、不得为负**，且 `CHECK(amount > 0)`；

6. 先对目标行 `SELECT ... FOR UPDATE`（或 `pg_advisory_xact_lock`）**再读累计值**，杜绝并发双花；

7. **【修订】收款余额三去向**（替代原 "必须全额分配否则阻断"）：①分配到已确认应收；②**无分配目标 → 挂预收账款（合同负债）**，须有客户 + 原因码并生成 `customer_prepayments` 行（§8.6）；③手续费 / 尾差按 §6 独立入账。**三者都不成立**（无目标、无客户 / 原因码、非手续费）才阻断 `unallocated_receipt`；

8. **【修订・新增】应付核销与预收核销同样适用本条**（目标分别为被核销确认分录金额、该客户预收余额，见 §2.5 / §8.6）。

* **验收条件**：


  * 发票快照 1000，先收 600（通过）再收 600 → 第二笔**整笔拒绝**，应收余额 400、分配累计 600；

  * 冲销其中 600 的分配后，该发票可分配额度回到 1000；

  * 并发两笔各 600 对同一 1000 目标 → 仅一笔成功，另一笔整体回滚；

  * 任一时刻 `Σ active 分配 ≤ 目标总额` 恒成立（可用 SQL 全表断言）；

  * **应付核销**：确认 500 后，抵扣核销 300 + 付款核销 200 = 500 通过；再核销 100 → 整笔拒绝；冲销付款核销行后可再核销 200；

  * **预收核销**：预收 600 时核销 700 → 整笔拒绝；

  * 无目标、无客户 / 原因码的收款余额 → 阻断 `unallocated_receipt`。

### 8.3 凭证保护（优先级 3）



* **原条款**：§1-R5 只对 `voucher_entries` 加了触发器与状态白名单，未覆盖三类关联表，也未把 "保护触发器缺失" 纳入结构自检，未定义防止关闭 / 绕过触发器的核验；且**把&#x20;**`vouchers`**&#x20;也写成 "posted 后禁 INSERT/UPDATE/DELETE"**—— 这与 `mark_reversed()` 的 `posted → reversed` 迁移直接矛盾（**已删除该表述**）。

* **修改条款**：

1. **保护范围（按表分别定义）**：

* `voucher_entries` / `voucher_source_links` / `voucher_attachment_links` / `voucher_settlement_lines`：所属凭证为 `posted` 时禁 `INSERT` / `UPDATE` / `DELETE`；

* `vouchers`：**禁&#x20;**`DELETE`；`posted` 后**仅允许&#x20;**`status`**&#x20;单向迁移（**`posted → reversed`**）与审计列（**`reversed_at/by/reason_code`**）写入**，其余列一律禁改；`INSERT` 不受限（新凭证必然是 `draft`）。

1. `vouchers.status` 只允许 `draft → posted → reversed` **单向**，`posted → draft` 一律拒绝（含借道其他字段的间接触发）；

2. 状态变更只经 `post_voucher()` / `mark_reversed()`，每次写 `posting_audit`（操作人、时间、前值、后值、原因码）；

3. **防绕过**：运行角色不得拥有底座表属主与 `TRIGGER` 权限；实施前核验 `pg_class.relowner` 与 `has_table_privilege(..., 'TRIGGER')`；

4. **结构自检新增项**：四张子表的保护触发器 + `vouchers` 的删除禁止触发器 + 状态守卫触发器是否齐备；缺失即判为 "结构异常"，阻止业务提交。

* **验收条件**：


  * 对 posted 凭证执行分录与三类关联（含核销行）的 INSERT/UPDATE/DELETE → 全部 raise，且 `posting_audit` 无成功记录；

  * `update vouchers set status='draft'` → 拒绝；`delete from vouchers where status='posted'` → 拒绝；

  * `mark_reversed()` 正常执行（不被保护触发器误伤），并写 `posting_audit`；

  * 人为 `DROP TRIGGER` 后跑自检 → 报结构异常，报销审批 / 付款过账被阻止且事务回滚；

  * 更正与冲销前后，`vouchers` 与 `voucher_entries` **行数只增不减**。

### 8.4 付款作废（优先级 4）



* **原条款**：正文未定义作废规则，仅在附件二改造清单里写 "作废改为出冲销凭证"，未区分未实付 / 已实付 / 已对账三种情形；且原第 2 点写 " 已对账先 `bank.unmatched` 再判定 "（**暗示 unmatched 后可取消，与 R1 冲突，已删除**）、原第 3 点写 "已过账的应付 / 抵扣出冲销凭证"（**含糊，会被读成冲销应付债务，已删除**）。

* **修改条款**：

1. **作废 / 取消只适用于未实付**：付款单 `status ∉ {paid, reconciled}`，批次 `status = 'issued'` 且**无任何已实付成员**；

2. **已实付一律禁止作废 / 取消**（对账与否同判）→ 走 `payment.refunded`（真实资金回流，须有依据）。`bank.unmatched`**&#x20;只清除关联，不改变已实付事实，不得作为恢复可作废状态的手段**；

3. 作废的账务：**只有已过账的抵扣凭证出冲销凭证**（Dr 员工借款 / Cr 应付员工）；**应付确认凭证不得冲销 —— 债务保留**；已占用借款抵扣必须写 reversal；批次作废**逐单判定，任一成员已实付则整批拒绝**；

4. 作废**不得删除**任何凭证、分录、事件、附件、核销行；原凭证保留且 `reversed=false`，新增冲销凭证；

5. **取消 / 作废永不产生银行反向分录**（未实付本就无银行分录）；

6. 事件键：`(payment_order_id, source_version, 'voided', occurrence_no)`；批次用 `(batch_id, ...)`；`requires_gl` 视是否已过账。

* **验收条件**：


  * 对 `status='paid'` 的付款单调作废 → 拒绝，状态与金额不变；

  * `status='reconciled'` 单：执行 `bank.unmatched` 后仍拒绝取消，并提示走 `payment.refunded`；

  * 未实付作废后：**应付余额 = 确认额（债务保留，不变）**、借款余额回到抵扣前、银行科目无变动、原凭证仍在、新增一张冲销凭证（抵扣已过账时）或零凭证（未过账时）；

  * 含已实付成员的批次作废 → 整批拒绝，且无成员状态被改动；

  * 作废前后 `vouchers` / `voucher_entries` / `posting_events` / `voucher_settlement_lines` 行数只增不减；

  * 作废后不存在 " 已作废但仍 `matched_*` 非空 " 的流水关联。

### 8.5 借款余额三口径（两方案通用）+ 预留余额（仅预留方案）



* **原条款**：§8.5 标题为 " 预留余额（**仅预留抵扣方案**）"，却把两方案通用的**账面余额公式**放在该标题下；验收写成 "`Σ 预留 + Σ 抵扣 ≤ 账面`恒成立 "——**数值上错误**（账面已扣除抵扣：抵扣 300 时账面 = 200，`300 ≤ 200` 不成立）。**上述两处已删除。**

* **修改条款**：

1. **三个口径（两方案通用）**：

* `U`（抵扣前上限）= 期初 + 发放 − 归还 ± 分类调整；

* `B`（账面余额）= `U − Σ 有效抵扣`；

* `A`（可用余额）= `B − Σ 有效预留`；**即时方案预留恒为 0，故&#x20;**`A ≡ B`。

1. 有效抵扣 = `entry_type IN ('application','reversal')` 行的**代数和**（reversal 为负）；有效预留 = `status='reserved'`，`released` / `consumed` 不计入。

2. **不变式（替代旧条款）**：`Σ 有效抵扣 + Σ 有效预留 ≤ U`（等价于 `A ≥ 0`），且 `B ≥ 0`；事务内校验 + advisory lock。

3. 预留是**表外、可审计、不进总账**（`requires_gl=false`，§2.2），**不得影响任何科目余额**；三种终态：付款时转抵扣（`consumed`）、取消 / 作废时释放（`released`）、超时清理（`released`，**必须留痕**）。

4. 借款页面**同时展示&#x20;**`B`**&#x20;与&#x20;**`A`，两者不得混用。

5. 借款科目余额 ≡ `B`（对账断言）。

* **验收条件**：见 §9 W1。

### 8.6 客户预付款（新增，来自 W3）



* **原条款**：无。原 §8.2 第 7 点 "收款必须全额分配，未分配余额阻断" 会**直接误伤客户预付款**（无发票可分配），已按 §8.2 第 7 点改写。

* **修改条款**：

1. **预付款到账 = 收到资金但尚无已确认应收**（无发票或发票未确认）→ `customer.prepayment_received`：**Dr 银行 / Cr 预收账款（合同负债）**，**不确认收入**（权责发生制，§2.1）；

2. 收入只在 `invoice.confirmed` 时确认；发票确认后以预收抵应收 → `prepayment.applied`：**Dr 预收账款 / Cr 应收**，金额走 §8.2 累计校验，**不得跨客户**；

3. 预收余额 = Σ `customer_prepayments` 未核销行；核销走**核销行**（§2.5，`target_type='prepayment'`），预收科目期末余额须与之相等；

4. 预收款在报表列示为**负债**，不得计入收入；期初必须包含启用日的未核销预收余额（§4）。

* **验收条件**：见 §9 W3。

### 8.7 应付核销（新增，来自 W2）



* **原条款**：§2.2 / §2.3 只给出分录方向，未定义 "应付被清偿到什么程度" 的可校验载体。

* **修改条款**：核销一律以 `voucher_settlement_lines` 记录（定义与约束见 §2.5）；抵扣核销与付款核销**分别记账、互不重复计数**；应付余额 = 确认额 − Σ active 核销额；校验与取舍规则同 §8.2。

* **验收条件**：见 §9 W2。



***

## 9. 复核对照（问题编号 — 修改后原文 — 对应验收）

> 编号 W1–W5；「修改后原文」为本文
> **正文当前文本**
> （已替换，非引用旧稿）；「已删除的冲突旧条款」见 §9.0。

### 9.0 本轮删除 / 替换的冲突旧条款



| 编号 | 被删除的旧条款                                           | 冲突对象                                           | 替换为                            |
| -- | ------------------------------------------------- | ---------------------------------------------- | ------------------------------ |
| D1 | §8.5 标题 "预留余额（仅预留抵扣方案）" 承载两方案通用的账面余额公式            | 即时方案无预留，公式无处归属                                 | §8.5 第 1 点（三口径两方案通用）           |
| D2 | §8.5 不变式 "`Σ 预留 + Σ 抵扣 ≤ 账面`"                     | 数值错误（账面已扣抵扣，抵扣 300→账面 200，300 ≤ 200 不成立）       | §8.5 第 3 点 `Σ 抵扣 + Σ 预留 ≤ U`   |
| D3 | §8.2 第 7 点 "收款必须全额分配，未分配余额一律阻断"                   | 客户预付款无发票可分配 → 被误阻断                             | §8.2 第 7 点（三去向）+ §8.6          |
| D4 | §8.4 第 2 点 " 已对账先 `bank.unmatched` 再判定 "          | 暗示 unmatched 后可取消，违反 R1（付款事实与匹配分离）             | §8.4 第 2 点（已实付一律禁止，走 refunded） |
| D5 | §8.4 第 3 点 " 已过账的**应付**/ 抵扣出冲销凭证 "                | 会被读成冲销应付债务，与 "债务保留" 矛盾                         | §8.4 第 3 点（只冲抵扣，应付确认凭证不冲）      |
| D6 | §8.3 第 1 点 "五张表 posted 后一律禁 INSERT/UPDATE/DELETE" | 与 `mark_reversed()` 的 `posted → reversed` 自相矛盾 | §8.3 第 1 点（按表分别定义）             |
| D7 | §9 旧文本（只指向既有条款、未改正文，含 "已确认" 表述）                   | 未修正内容写成已确认                                     | 本节 W1–W5 + 上述 D1–D6            |

### W1 500 / 300 抵扣后的余额

**修改后原文**（§8.5 第 1–3、6 点；§3 对照表 "余额展示"；§2.2 `advance.offset_applied`）：

> 三个口径（两方案通用）：
> `U`
> （抵扣前上限）= 期初 + 发放 − 归还 ± 分类调整；
> `B`
> （账面余额）=
> `U − Σ 有效抵扣`
> ；
> `A`
> （可用余额）=
> `B − Σ 有效预留`
> ；即时方案预留恒为 0，故
> `A ≡ B`
> 。
> 有效抵扣 =
> `entry_type IN ('application','reversal')`
> 行的代数和；有效预留 =
> `status='reserved'`
> 。
> 不变式：
> `Σ 有效抵扣 + Σ 有效预留 ≤ U`
> （等价
> `A ≥ 0`
> ），且
> `B ≥ 0`
> 。借款科目余额 ≡
> `B`
> 。
> 抵扣分录：
> `advance.offset_applied`
> \=
> **Dr 应付员工 / Cr 员工借款**
> 。

**对应验收**（统一例子 `U`=500、应付 500、抵扣 300、实付 200）：



| 阶段    | 即时方案 `OFFSET_AT_READY`           | 预留方案 `OFFSET_AT_PAYMENT`                  |
| ----- | -------------------------------- | ----------------------------------------- |
| 待付款   | `U`500 / `B`**200** / `A`**200** | `U`500 / `B`**500** / 预留 300 / `A`**200** |
| 取消后   | `B`500 / `A`500（冲销凭证 1 张）        | `B`500 / 预留 0 / `A`500（凭证 0 张）            |
| 重新待付款 | `B`200 / `A`200                  | `B`500 / 预留 300 / `A`200                  |
| 实际付款后 | 借款 200 / 应付 0 / 银行 −200          | 借款 200 / 应付 0 / 银行 −200                   |



* 两方案终态相同：借款 **200** / 应付 **0** / 银行 **−200** / 费用 500；

* 不变式在每一阶段成立：`300 + 0 ≤ 500`（即时，抵扣后）、`0 + 300 ≤ 500`（预留，预留后）；

* 预留事件前后**全科目余额快照完全一致**（`requires_gl=false`）；

* 借款科目余额与 `B` 的差额恒为 0（SQL 断言）。

### W2 确认应付后新增核销

**修改后原文**（§2.5 全文 + §8.7；配合 §2.1 `payroll.confirmed`、§2.2 `advance.offset_applied`、§2.3 `payment.paid`）：

> 核销一律以
> `voucher_settlement_lines`
> 记录：
> `voucher_entry_id`
> \= 被核销的
> **确认分录**
> ；目标额 = 该确认分录金额（应付 500）；约束
> `CHECK(amount>0)`
> 与
> `UNIQUE(voucher_entry_id, source_event_id)`
> ；校验走累计剩余余额，
> **超额整笔拒绝**
> 。
> **抵扣与付款各写各的核销行，互不重复计数**
> ：
> `advance.offset_applied`
> 一条（= 抵扣额 300），
> `payment.paid`
> 一条（= net 实付额 200），累计 500 = 目标 500。
> 应付余额 = 确认额 − Σ active 核销额。核销行不可 UPDATE；冲销核销行须与冲销凭证同一事务。

**对应验收**：



* 应付确认 500 → 抵扣核销 300 → 付款核销 200：应付余额 **0**；Σ active 核销 = **500** = 目标；

* 凭证行合计：**Dr 应付 500 = Cr 员工借款 300 + Cr 银行 200**；抵扣凭证银行科目行数 **0**，付款凭证银行科目 Cr **200**（= net，不含抵扣）；

* 再新增一笔 100 的核销 → **整笔拒绝并回滚**（累计已达目标 500，剩余 0）；

* 冲销付款核销行后：应付余额回到 **200**，可再核销 200；

* 四分离对账（费用 / 应付 / 抵扣 / 银行）恒等；抵扣额**不重复计入**银行实付额。

### W3 客户预付款到账

**修改后原文**（§8.6 全文 + §2.1 新增两行 + §8.2 第 7 点）：

> 预付款到账 = 收到资金但尚无已确认应收 →
> `customer.prepayment_received`
> ：
> **Dr 银行 / Cr 预收账款（合同负债），不确认收入**
> 。
> 收入只在
> `invoice.confirmed`
> 确认；发票确认后
> `prepayment.applied`
> ：
> **Dr 预收账款 / Cr 应收**
> ，不得跨客户，金额走累计校验。
> 收款余额三去向：①分配已确认应收；②无目标 → 挂预收账款（须有客户 + 原因码，生成
> `customer_prepayments`
> 行）；③手续费 / 尾差按 §6 独立入账；三者都不成立才阻断
> `unallocated_receipt`
> 。
> 预收余额 = Σ 未核销
> `customer_prepayments`
> ；核销走核销行（
> `target_type='prepayment'`
> ）；报表列示为
> **负债**
> 。

**对应验收**：



* 收到 600、无发票 → Dr 银行 600 / Cr 预收账款 600；**收入 0、应收 0**；

* 发票确认 1000（Dr 应收 1000 / Cr 收入 + 销项税）→ 核销预收 600（Dr 预收 600 / Cr 应收 600）→ **应收 400 / 预收 0**；

* 该发票再收 600 → 分配 600 > 剩余 400 → **整笔拒绝并回滚**，应收仍 400、分配累计 600；

* 预收核销 700 > 预收余额 600 → **整笔拒绝**；

* 并发两笔 600 对同一 1000 目标 → 仅一笔成功；

* 期末：预收科目余额 == Σ 未核销预收行金额（SQL 全表断言）；无客户 / 原因码的无目标收款 → 阻断 `unallocated_receipt`。

### W4 重复请求不新增业务行

**修改后原文**（§8.1 第 5–6 点）：

> 业务行写入与过账
> **同一事务**
> ；事务内先按业务键查
> `posting_events`
> ——
> **命中同哈希则不写任何业务行**
> ，直接返回既有
> `voucher_id`
> 并结束事务（净写入为空）；未命中才写业务行 → 过账 → 写事件。若写完业务行后过账判定发现事件已被并发事务抢先提交，
> **整事务回滚**
> （业务行一并撤销），客户端收到既有
> `voucher_id`
> 。
> 业务行自身必须带业务唯一键：application
> `UNIQUE(advance_id, payment_order_id, occurrence_no)`
> ；receipt
> `UNIQUE(customer_id, receipt_no)`
> ；allocation / 核销行用部分唯一索引（
> `WHERE status='active'`
> /
> `UNIQUE(voucher_entry_id, source_event_id)`
> ）；唯一键冲突时返回既有行 id，不抛 500。

**对应验收**：



* 同键同哈希重投 100 次 → `vouchers` +0、`voucher_entries` +0、`posting_events` +0、**业务行（application /receipt/allocation/settlement\_line）+0**，返回同一 `voucher_id`；

* 同键异哈希 → 回滚并报 `payload_conflict`，**凭证与业务行均 +0**；

* 请求自带 `occurrence_no=9` 但无新业务行 → 阻断 `no_business_fact`；

* 并发 10 个同内容请求 → 1 张凭证、**业务行 1 行**（不是 10 行）；

* 业务行唯一键冲突 → 返回既有行 id，不产生 500、不产生第二行。

### W5 取消支付仍保留债务

**修改后原文**（§8.4 第 2–3、5 点 + §2.3 `payment.cancelled` + §2.4 末段 + §3 硬规则）：

> 取消 / 作废
> **只适用于未实付**
> ；
> **已实付（paid/reconciled）一律禁止作废 / 取消**
> ，走
> `payment.refunded`
> 。
> `bank.unmatched`
> **&#x20;只清除关联，不改变已实付事实，不得作为恢复可作废状态的手段。**
> 作废的账务：
> **只有已过账的抵扣凭证出冲销凭证**
> （Dr 员工借款 / Cr 应付员工）；
> **应付确认凭证不得冲销 —— 债务保留**
> ；已占用借款抵扣必须写 reversal。
> **取消 / 作废永不产生银行反向分录**
> （未实付本就无银行分录）。
> 只有取消
> **业务债务**
> （
> `expense.reopened`
> 全额冲销 / 更正凭证 / 发票作废）才消债。

**对应验收**（应付确认 500、抵扣 300、取消时未实付）：



* 取消后：**应付余额 = 500（不变，债务保留）**；银行科目无变动；借款回到 500（即时方案：新增冲销凭证 1 张）或预留释放（预留方案：新增凭证 0 张）；

* 已 `paid` 单取消 → 拒绝，状态与金额不变；

* 已 `reconciled` 单：执行 `bank.unmatched` 后**仍拒绝取消**，提示走 `payment.refunded`；unmatched 前后凭证新增 **0**；

* 取消前后 `vouchers` / `voucher_entries` / `posting_events` / `voucher_settlement_lines` **行数只增不减**；

* 取消后不存在 " 已取消但仍 `matched_*` 非空 " 的流水关联；

* 批次作废仅 `issued` 且无已实付成员；成员债务一律保留。



***

# 附件一　已一致条款及验收标准

> 仅列
> **本文正文已落实**
> 的条款；正文未落实的一律不写 "已确认"。



| #  | 条款                                                                              | 验收标准                                                                    |
| -- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 1  | 凭证与分录持久化，报表实时查询；凭证独立编号                                                          | 凭证号唯一、按前缀取最大流水号（非 `order by id desc`）                                   |
| 2  | 首期范围：客户发票及收款、工资确认、报销审批、员工付款与借款；USD 单币种（§2.0）                                    | 其余域不产生凭证                                                                |
| 3  | 三日期并存：业务日期 / 会计日期 / 实际付款日期                                                      | 三列非空且语义可区分                                                              |
| 4  | 金额三规则：因子保精度 → 行金额 ROUND\_HALF\_UP → 合计 = 行金额求和                                  | 回归：37.8 × 0.675 → 25.52；三行示例合计 39.43                                    |
| 5  | 精度分列：金额 `NUMERIC(14,2)`、数量 `NUMERIC(14,4)`、单价 `NUMERIC(14,6)`、税率 `NUMERIC(9,6)` | 建表 DDL 断言                                                               |
| 6  | 工资纠偏走统一有效金额读层，其他业务各自来源                                                          | `payroll_correction_integrity_check`（`correction.py:521`）闭合             |
| 7  | 日报 / AI 草稿 / 付款草稿 / 报价单不过账；P7 挂起（§2.0）                                          | 相关路由后凭证新增 == 0                                                          |
| 8  | 更正粒度：金额变化用差额调整；整单语义错误才冲销重记                                                      | 冲销凭证银行科目行数 == 0                                                         |
| 9  | 已过账凭证不可改不可删；更正一律新增凭证（§8.3）                                                      | 触发器实测 UPDATE/DELETE 均 raise；`mark_reversed()` 不被误伤                      |
| 10 | 三种取消分开：取消支付安排 / 取消业务债务 / 解除银行匹配                                                 | 三者调用的路由与事件类型互不相同                                                        |
| 11 | 付款事实与银行匹配分离（R1）                                                                 | 解除匹配后凭证新增 == 0，且无 "已解除但仍 reconciled" 的记录                                |
| 12 | 事件版本与内容哈希分离（R2）                                                                 | 同版本异 hash → 阻断；异版本同 hash → 允许新凭证                                        |
| 13 | 重复请求不新增凭证**且不新增业务行**（§8.1）                                                      | 重投 / 并发均返回同一 voucher\_id，**业务行 0 新增**；无新业务行的新序号请求被阻断 `no_business_fact` |
| 14 | 分配与核销按累计剩余余额校验；收款余额三去向（§8.2 / §8.7）                                             | 超额核销 / 分配事务回滚，目标行金额不变；无目标无原因码的收款阻断                                      |
| 15 | 预留事件可审计、不进总账（§8.5）                                                              | `requires_gl=false` 事件前后全科目余额快照一致                                       |
| 16 | 期初：原凭证不可改；方向按 `normal_balance`（资产借 / 负债权益贷）；增加应付必须贷记应付；**含未核销预收**；原因码必填（§4）     | 本期费用发生额 == 仅 `post_cutover_new` 的金额；资产 / 负债科目方向断言通过                     |
| 17 | 组件→科目走配置映射，不硬编码                                                                 | 无映射的组件阻断过账                                                              |
| 18 | 关账自然月、与 14 天工资网格解耦；关账后禁止过账                                                      | 关账期间过账 raise                                                            |
| 19 | 结构自检零写入；保存报告另设操作（§5）                                                            | 执行前后 `accounting_base_enabled` 不变；第二次执行结果一致                             |
| 20 | 借贷平衡校验实际分录（§5）                                                                  | 人为插入不平衡分录 → COMMIT 失败                                                   |
| 21 | 尾差：目标有依据、最大余额法、无解释差额阻断（§6）                                                      | 目标 66.05 场景阻断                                                           |
| 22 | 冻结数据与 P7 不变                                                                     | correction group 2 /allocations/replacement SL 的 md5 校验不变               |
| 23 | 冲销报表口径：开放期间净额（§4）                                                               | 跨期更正全部记入开放期间；报表 = Σ(正 − 冲销)；冲销凭证银行科目行数 == 0                             |
| 24 | 批次作废门禁：仅未实付成员；**债务保留**（§8.4）                                                    | 已 paid 批次作废被拒；作废不产生银行反向分录；应付余额不变                                        |
| 25 | **客户预付款不确认收入**（§8.6，新增）                                                         | 预收到账后收入发生额 == 0；预收科目余额 == Σ 未核销预收行                                      |
| 26 | **核销以核销行显式记录**（§2.5，新增）                                                         | 应付 / 应收 / 预收余额 == 确认额 − Σ active 核销额；超额整笔拒绝                             |
| 27 | **借款三口径 U/B/A 与不变式**（§8.5，修订）                                                   | `Σ 抵扣 + Σ 预留 ≤ U`；借款科目余额 == `B`；500/300 例各阶段数值见表 W1                     |



***

# 附件二　两种抵扣方案的最小改造清单

## 共同部分（两方案都需）

> 行号均为核对时快照，
> **实施前按 §0.1 重取**
> ；迁移编号按当时
> `migrations/postgresql/`
> 最大编号 +1（本次核对为
> `0297`
> ，即自
> `0298`
> 起）。



1. 迁移（编号实施时顺延）：建 `accounts`（须含**预收账款 / 合同负债**、银行、应收、员工借款、应付员工、费用、银行手续费、期初结转）/`accounting_periods`/`posting_events`/`vouchers`/`voucher_entries`/`voucher_source_links`/`voucher_settlement_lines`**（核销行）**/`voucher_attachment_links`/`accounting_opening_lines`/`accounting_opening_cutover`/`posting_audit`；**收款与预收新增** `customer_receipts` / `receipt_allocations` / `customer_prepayments`；`employee_advance_applications` 加 `posting_event_id` / `voucher_id` / `occurrence_no` / `legacy_anchor` 四列 + 索引。

2. 借贷平衡约束触发器（§5）+ 已过账保护触发器（按 §8.3 分表定义）。

3. 业务行唯一键：application /receipt/allocation/ 核销行（§8.1 第 6 点）。

4. `scripts/``check_accounting_base.py`（只读）+ `scripts/``save_accounting_base_report.py`（写入），并在 `scripts/``debian-auto-deploy.sh` 补一行（现 `0297` 排在 `0296` 之前，建议改为按文件名排序）。

5. 所有过账入口前置 `require_posting_ready()`；过账范围白名单（§2.0）。

6. 新增 `bank.unmatched` 路由（清 `matched_*` + 回退对账状态；**不改变已实付事实**）。

7. 新增客户预付款收款与预收核销入口（§8.6）。

8. `backfill_advance_posting_anchors.py`（默认 dry-run）。

## 即时抵扣方案 `OFFSET_AT_READY`



| # | 位置                                                          | 改动                                                                          |
| - | ----------------------------------------------------------- | --------------------------------------------------------------------------- |
| 1 | `_create_advance_application` `employee_finance.py:472-490` | 写 application 行后，同事务追加 `advance.offset_applied` 过账 + **核销行**                |
| 2 | `_reverse_advance_application` `492-501`                    | 追加 `advance.offset_reversed` 反向过账 + 核销行冲销；legacy 行挂 `corrects_opening_line` |
| 3 | `transition` `1564-1571`（mark\_paid）                        | 追加付款凭证（Dr 应付 net / Cr 银行 net）+ 付款核销行                                        |
| 4 | 批次发放 `1777-1795`                                            | `_create_advance_application` 与 `status='paid'` 后各追加一次过账                    |
| 5 | `void_payment_batch` `1801-1840`                            | 批次作废（仅未实付成员，§8.4）改为：已过账抵扣出冲销凭证；**应付债务保留**；已实付成员禁止作废，逐单走 `payment.refunded`  |
| 6 | `_advance_balances` `188-205`                               | **不改**                                                                      |
| 7 | 借款页面                                                        | **不改**（`A ≡ B`）                                                             |
| 8 | 测试                                                          | 新增：抵扣过账 / 反向 / 重复投递三类用例；既有借款与批次回归**期望值不变**                                  |

## 预留抵扣方案 `OFFSET_AT_PAYMENT`



| #  | 位置                                                                                                                                                                                                         | 改动                                                                                           |
| -- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 1  | 新增 `advance_reservations(id, advance_id, payment_order_id, amount, status, reserved_at, released_at, posting_event_id)` + `CHECK(status IN (...))` + `UNIQUE(advance_id, payment_order_id, occurrence_no)` | 表外预留载体                                                                                       |
| 2  | `_advance_balances` `188-205`                                                                                                                                                                              | 口径改为 `A = U − Σ applications − Σ 有效预留`（**影响借款页显示与既有回归期望值**）                                  |
| 3  | `_create_advance_application` `472-490`                                                                                                                                                                    | 余额校验（`484-485`）改为 `Σ 抵扣 + Σ 预留 ≤ U`                                                          |
| 4  | `transition` ready 分支 `1557-1558`                                                                                                                                                                          | 改为写 reservation（不出分录），不写 application                                                         |
| 5  | `transition` mark\_paid 分支 `1564-1571`                                                                                                                                                                     | 改为写 application + 抵扣过账 + 付款过账 + 两条核销行                                                        |
| 6  | 取消 `1550-1557` / 批次作废 `1801-1840`                                                                                                                                                                          | 改为释放 reservation（无分录，§8.5）；已写 application（已过账）的撤销 = 冲销凭证 + reversal + 核销行冲销**同事务**；已实付单据不可取消 |
| 7  | 新增                                                                                                                                                                                                         | 预留过期清理任务 + 预留与 application 一致性对账                                                             |
| 8  | legacy 兼容                                                                                                                                                                                                  | 历史 application 行需按来源分类（单张 ready 时点 / 批次 paid 时点）；启用日仍在待付款的历史单需判定为已抵扣或回填预留，否则账面跳变             |
| 9  | 借款页面                                                                                                                                                                                                       | 新增 "`B` 账面余额 "与"`A` 可用余额 " 双显示                                                               |
| 10 | 测试                                                                                                                                                                                                         | 借款余额口径相关回归需**全部复核并重写期望值**                                                                    |



***

# 附件三　实施前确认项（不阻塞本稿评审）

> **本轮不要求用户裁决**
> 。两个抵扣方案
> **并列保留**
> 在本文，设计与验收条件各自完整；开工前选定其一即可，未选定前两者均有效。



1. **抵扣生效时点（业务语义）**：待付款期间，借款账面是否应当减少？

* 允许减少 → 采用 `OFFSET_AT_READY`；

* 不允许（须实际付款才减少） → 采用 `OFFSET_AT_PAYMENT`，并接受附件二该方案第 2/9/10 项的口径变更与回归重写。

* **未裁决时**：两方案的设计、事件、验收（含 §8.5、§9 W1）全部保留，不做取舍。

1. **借款归还是否存在真实业务**（现金或银行归还）？【事实】`grep repay` 零命中，`entry_type` CHECK 仅 `application` / `reversal`。决定其是否进首期（独立可选范围）。

2. **期初更正是否强制第二人复核**？决定是否新增复核表与权限位，还是单人过账 + 事后抽查。

3. **客户预付款是否进首期**？若不进首期，`customer.prepayment_received` / `prepayment.applied` 与附件二共同部分第 7 项顺延，但 §8.2 第 7 点的 "三去向" 仍需保留（否则无目标收款会被误阻断）。

> 非设计项：
> `payable_totals(None)`
> 的 P0 回归仍
> **未指派修复人**
> ，与本规格分线处理。