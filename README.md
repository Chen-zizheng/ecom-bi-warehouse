# 电商销售 BI 报表

电商销售 BI 工程：合成电商数据 → 星型模型仓库 → 12 条质量规则 → 指标视图 → FastAPI 接口 → 原生 SVG 看板。

![看板](docs/dashboard.png)

使用 MySQL 8.4、FastAPI 和原生 JS / SVG 看板。运行需要 Python 3.11+，以及可创建表和视图的 MySQL 数据库。

## 目录

- `src/bidata/schema.py`：6 张源表、5 张业务维表、角色配置表、1 张事实表、4 张 ETL 元数据表
- `src/bidata/generate.py`：生成合成数据并注入异常样本
- `src/bidata/etl.py`：全量与增量装载，脏行进 `etl_reject` 不中断
- `src/bidata/quality.py`：12 条规则，结果落 `dq_result`
- `src/bidata/metrics.py`：13 个视图，KPI 口径写在这里
- `src/bidata/api.py`：12 个接口，带身份参数
- `web/`：看板，原生 JS 与 SVG
- `tests/`：装载、质量规则与接口测试，跑在 SQLite 上

## 星型模型

| 层 | 表 | 用途 |
| --- | --- | --- |
| 源 | `src_orders` / `src_order_items` / `src_products` / `src_customers` / `src_stores` / `src_returns` | 订单、明细及基础资料 |
| 业务维度 | `dim_product` / `dim_customer` / `dim_store` / `dim_channel` / `dim_date` | 商品、客户、门店、渠道、日期；区域是 `dim_store.region` 属性 |
| 角色配置 | `dim_role` | 接口查询范围配置 |
| 事实 | `fact_sales` | 订单明细粒度的销售事实 |
| 元数据 | `etl_state` / `etl_run` / `etl_reject` / `dq_result` | 水位线、运行日志、拒收记录、质量检查结果 |

`dim_date` 除了年月日，还带连续日期序号 `day_seq`，用于计算日期窗口，兼容 MySQL 与测试使用的 SQLite。

## 指标口径

事实表保留全部 5 种订单状态（`paid` / `shipped` / `completed` / `closed` / `refunded`）。看板的有效销售基于 `v_sales_wide`：

```sql
-- metrics.py 的 v_sales_wide
WHERE f.order_status NOT IN ('closed', 'refunded')
```

| 指标 | 看板口径 |
| --- | --- |
| 有效订单 | 过滤后的去重订单数 |
| 净销售额 | `SUM(net_amount)`，即原价额减折扣额；未再扣减退货金额 |
| 客单价 | 净销售额 / 有效订单 |
| 退货率 | 有效销售明细的退货件数 / 销售件数 |
| 动销率 | 有效销售涉及的 SKU 数 / `dim_product` 商品总数 |
| 折扣率 | 折扣额 / 原价额 |
| 月内复购率 | 当月下单 ≥2 次的人数 / 当月下单人数 |

月内复购率每月重新计算买家分母；未覆盖完整月份的数据应单独比较。

注意视图的统计范围并不完全相同：`v_kpi_total`、`v_kpi_daily` 直接汇总 `fact_sales`，包含全状态；`v_sales_wide` 及基于它的品类、区域、渠道等视图排除关闭和退款订单。看板 `/api/kpi` 从 `v_sales_wide` 查询。复核时需使用相同的数据源和过滤条件，定义见 [`metrics.py`](src/bidata/metrics.py) 与 [`api.py`](src/bidata/api.py)。

## 质量规则

ETL 记录异常数据，质量检查再核对源表、事实表、维表和水位线。规则定义见 [`quality.py`](src/bidata/quality.py)。

| # | 规则 |
| --- | --- |
| D1 | 每条源明细有事实记录或拒收记录 |
| D2 | 事实主键不重复 |
| D3 | 事实引用的维度都存在 |
| D4 | `gross - discount = net`，差额不超过 0.01 |
| D5 | 退货量不超过购买量 |
| D6 | 状态值在枚举内 |
| D7 | 金额非负 |
| D8 | 下单日期在 `dim_date` 里 |
| D9 | 日期维度连续，没有断天 |
| D10 | 源-仓按单对账，差额不超过 0.05 |
| D11 | 合法状态的源订单有事实记录或明细拒收记录 |
| D12 | 增量水位线不超过源表最大值 |

每次运行将检查行数、失败行数和状态写入 `dq_result`，可通过 CLI 输出或 `/api/dq` 查看。

## 增量装载

水位线保存在 `etl_state.last_updated_at`；订单头或订单明细的 `updated_at` 大于水位线时抽取相应明细。MySQL 时间字段使用 `DATETIME(6)` 保留微秒，避免把同一秒内不同时间的更新压成相同值，定义见 [`schema.py`](src/bidata/schema.py)：

```python
TS = DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')
```

ETL 的 `run_id` 使用微秒时间戳（`%Y%m%d%H%M%S%f`），区分同一秒内启动的任务。

## 接口

`/api/kpi`、`/api/trend`、`/api/category`、`/api/region`、`/api/channel`、`/api/returns`、`/api/repeat`、`/api/months`、`/api/runs`、`/api/dq`、`/api/roles`、`/api/health`。

`/api/kpi` 和 `/api/trend` 接受 `role` 参数。初始化角色包括 `analyst`（全部数据）、`east_ops`（华东）、`apparel_pm`（服饰）。页面下拉框用于切换查询范围；这是接口层的筛选演示，不是登录鉴权，也不影响所有接口。

## 怎么跑

先创建 MySQL 数据库，并为连接用户授予建表、建视图及读写权限。以下命令在仓库根目录执行，以 Bash 为例：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yml config.yml   # 改里面的 MySQL 连接串
export PYTHONPATH=src

python -m bidata.cli gen              # 生成源数据
python -m bidata.cli etl --mode full  # 首次全量装载
python -m bidata.cli dq               # 检查质量规则
python -m bidata.cli views            # 创建指标视图
python -m bidata.cli serve            # 接口与看板：http://127.0.0.1:8010
```

后续使用 `python -m bidata.cli etl --mode incr` 增量装载；`incr` 也是默认模式。需要追加源数据时可运行 `python -m bidata.cli gen --append-orders 100`，再执行增量装载和质量检查。`dq` 每次生成新的运行记录，看板显示最新一批结果。

PowerShell 中将 `cp` 换为 `Copy-Item`，将 `export PYTHONPATH=src` 换为 `$env:PYTHONPATH="src"`；虚拟环境激活命令为 `.venv\Scripts\Activate.ps1`。

## 测试

```bash
python -m pytest -q
```

用例跑在 SQLite 上，不依赖 MySQL，覆盖装载、拒收、水位线、规则失败路径和接口的分页与角色参数。测试中使用 SQLite 兼容处理；完整装载与视图流程仍需按上面的步骤在 MySQL 上验证。

## 异常记录与默认数据

异常处理见 [`etl.py`](src/bidata/etl.py)：无法装载的订单明细写入 `etl_reject`；退货量超过购买量时，按购买量截断并记录异常，该明细仍可进入事实表。因此 `rejected` 是异常记录数，不等于 `rows_in - rows_out`。D1 逐条检查源明细是否有去向。

[`config.example.yml`](config.example.yml) 默认使用 `scale=1.0`、日期范围 2025-01-01 至 2026-11-10、随机种子 `20261113`。对应的基础生成规模为 30 家店、2,000 个商品、20,000 个客户和 300,000 个订单，另会注入异常样本；明细与退货记录按生成逻辑产生。规模定义见 [`generate.py`](src/bidata/generate.py)，实际行数、装载耗时和检查结果以本次运行输出为准。

## 已知限制

- 使用合成数据，适合验证装载、指标和看板流程；销售分布由生成器参数决定。
- 角色参数可由调用方修改，不构成权限隔离；当前服务应仅在可信环境运行。
- 增量依赖源表正确维护 `updated_at`；尚未接入 CDC，也不自动同步仅发生在退货表中的变更。
- 通过 CLI 手动执行，尚未接入调度。
- 看板使用固定宽度布局，窄屏体验有限。
