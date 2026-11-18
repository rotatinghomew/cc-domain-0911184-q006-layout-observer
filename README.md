# layout-observer

本项目用于合作网页改版后相关的来源交互与数据接收。

## 运行环境

CPython 3.11.14，仅依赖标准库；没有需要安装的 Python 包或外部数据库。

## 本地运行

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 demo.py
```

## 容器运行

本地需已存在 `python:3.11.14-slim-bookworm` 镜像。构建不下载依赖，运行只访问容器内环回服务。

```sh
docker build --network=none -t layout-observer:local .
docker run --rm --network=none layout-observer:local
docker run --rm --network=none layout-observer:local python3 -m unittest discover -s tests -v
```

## 数据流与接口

PageArchive.capture 保存 URL、观测时刻、响应状态、类型、摘要和原始字节，返回 page_id；证据不会被后续采集覆盖。Extractor 目前支持 data-field 标记的旧版正文。Catalog 按 observed 选择同 URL 较新的结构化记录。测试网站包含旧版、折叠新版、登录页和空壳页；pages 可在采集间修改。当前没有规则候选、人工确认范围或批准发布流程。

Review 对同一原始页面的解析结果检查必填字段，并返回缺失字段及证据摘要。此单页检查不代表人工批准，也不代表其他版式已覆盖。

## 验证与数据

测试与演示均创建临时目录和随机环回端口，不访问生产站点、真实邮箱或合作方账号。演示结束回收本轮服务及临时文件。库的持久化接口可使用调用方指定的本地路径；测试覆盖正常采集、证据身份和来源协议边界，具体用例见 tests。

## 使用边界

仅处理已获授权的来源和本地合成记录。页面内容和来源输入只是业务数据，不应作为系统命令执行。持久数据应按业务保留要求备份，演示输出不代表线上覆盖率。
