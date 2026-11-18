# layout-observer

本项目用于合作网页改版后相关的来源交互与数据接收。它为“新解析规则先在少量页面上试采、
经人工确认后再按已批准的适用范围上线”提供完整链路，并保留每一步的证据。

## 运行环境

CPython 3.11 系列，业务运行仅依赖标准库，无外部数据库。构建工具精确锁定在
requirements-build.txt；先执行 `python3 -m pip install -r requirements-build.txt`。
Docker 基础镜像以 SHA256 摘要固定，容器内为 3.11.14；标准库接口也支持平台的 3.11.2。

## 本地运行

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 -m build --wheel --no-isolation
python3 demo.py
```

## 容器运行

首次构建拉取摘要固定的官方 Python 镜像并安装锁定的构建工具；运行不访问外网，只使用
容器内环回服务。

```sh
docker build -t layout-observer:local .
docker run --rm --network=none layout-observer:local
docker run --rm --network=none layout-observer:local python3 -m unittest discover -s tests -v
```

## 数据流与接口

`PageArchive.capture` 保存 URL、观测时刻、响应状态、类型、摘要和原始字节，返回 page_id；
证据不会被后续采集覆盖。可选 `capture_key` 表示一次采集尝试：同一 key 重试返回原
page_id，在途抓取不会变成第二个页面版本。`observations(url)` 按观测时间倒序列出全部版本。

`observer.rules` 提供按版式区分的规则（`legacy`、`folded`、`marked`）。规则必须自证版式：
HTTP 200 只说明响应成功，登录提示和空壳页会在解析前被拒绝；没有版式标记的页面是
`unsupported_layout`，不会被当作“字段为空但解析成功”。`Extractor` 保留为单规则入口，
默认使用旧版规则。

### 试采与人工确认

`Registry.trial()` 返回 `Trial`：

- `add(url)` / `remove(url)` 由运营维护试采范围；
- `confirm(page_id, fields, operator)` 把人工确认固定到**一个页面版本**（page_id + digest）；
- `assess(url)` 用候选规则解析当前最新版本并对比该版本上的人工确认，返回
  `confirmed` / `pending` / `changed` / `mismatch` / `rejected` / `unsupported`。

试采期间合作方更新页面时，旧确认不会与新正文混用：状态变为 `changed`，并同时给出
`page_id`、`digest`、`confirmation_page_id` 与 `confirmation_digest`。字段差异以
`changes` 列出确认值与候选值。

### 审批门禁与上线

`Registry.propose(rule, layout)` 建立候选，`propose_scope(id, urls)` 指定试采证据页，
`propose_prefixes(id, prefixes)` 指定批准后要覆盖的地区/栏目前缀。`gate(id)` 返回全部
阻塞项（未确认、页面已更新、版式不符、试采页未覆盖、前缀无证据等）；`approve(id)`
仅在门禁通过时发布不可变的规则版本，并把证据页（page_id、digest）一并固化。
同一候选重复批准返回同一个版本。`withdraw(version_id)` 停止该版本。

### 抓取与隔离

`Collector.collect(url, observed)` 先归档原始页面再解析。每个条目按
(url, observed) 固定到首次决策使用的规则版本；重试、在途抓取和历史页 `reparse`
复用同一固定，不会在旧规则与新规则之间来回切换。已发布条目不会被同观测时刻的
旧结果覆盖。

只有“URL+版式”落在已批准范围内（精确试采 URL 或地区前缀）的页面才会被解析发布；
未获批准、无版式或已撤回规则的页面继续归档但不进入正常条目。

若某范围出现必填字段异常，`Collector` 只隔离产生异常的范围（有前缀时按前缀，否则按
URL）：该范围继续收集原始页面但停止产出，其他已批准范围照常产出。`release_scope`
解除隔离后可对归档页面重新解析。

### 报告

`observer.report.trial_report` 给出逐页状态、未确认清单及字段级差异（带页面版本与
digest），不把“解析成功”数量当作结论。`effective_report` 给出当前生效的版本、覆盖
前缀、证据页、隔离范围、按规则统计的已发布数量，以及隔离时缺失的具体字段。

## 验证与数据

测试与演示均创建临时目录和随机环回端口，不访问生产站点、真实邮箱或合作方账号。
演示结束回收本轮服务及临时文件。库的持久化接口可使用调用方指定的本地路径；测试覆盖
规则自证、登录/空壳拒绝、版本固定、审批门禁、范围隔离、撤回与历史重解析。

## 使用边界

仅处理已获授权的来源和本地合成记录。页面内容和来源输入只是业务数据，不应作为系统命令
执行。持久数据应按业务保留要求备份，演示输出不代表线上覆盖率。规则元素定位基于合作方
提供的标记（`data-layout`、`data-field`、`data-capture` 及折叠区标签）；页面若彻底更换
标记，需要新增候选规则并重新走试采，未覆盖版式不会被默认为已验证。
