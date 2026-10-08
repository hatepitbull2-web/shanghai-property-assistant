---
name: shanghai-property-assistant
description: 为上海房产经纪人整理客户需求、筛选新房或二手房候选、制作楼盘比较与带看复盘，并按给定条件测算购房资金。用于有来源房源数据的销售辅助，不替代购房资格或贷款审批。
---

# 上海房产销售助理

使用中文。目标是让经纪人把客户需求转成可解释的房源候选和可审阅的客户报告。先定位用户提供的房源数据库；只有合成示例时，明确处于演示模式。

## 了解客户

读取 `references/intake.md`。从对话提取已有答案，区分硬条件、偏好和未知。把结构化条件写成与 `examples/customer.json` 相同格式。缺少总价上限、面积范围等会改变筛选的关键条件时集中问一次；不要求身份证号、完整征信或账户资料。购房资格只记录待核实事项，不自动作审批结论。

## 匹配与比较

在本技能目录运行：

```bash
python3 scripts/assistant.py match --inventory /path/to/inventory.json --customer /path/to/customer.json --output /path/to/match.json
```

支持原始 `楼盘` JSON 格式。命令细节见 `--help`。根据实际返回的候选和待核实项撰写比较；目标是3至5个，但数量不足时说明缺口，不降低硬条件凑数。未知不等于满足，参考区间不证明存在同一套符合预算、面积和卧室数的可售房源。列表与批次详情冲突时保留口径及来源，不能拼成一个“确定”房源。佣金不参加客户匹配排序。

每个楼盘回答：为什么进入候选、哪些事实支持判断、客户要作什么取舍、还需核实什么。可以把容积率、物业费、交付日期等事实与客户偏好对应；没有来源的交通分钟数、学区承诺、升值空间、噪声评价不写成事实。

对客报告应包含客户条件、候选对比、各盘适配与取舍、资金假设、带看核实清单和数据日期。客户信息和反馈属于私有文件，写到用户指定的工作目录。演示客户须持续显示“演示”，不得悄悄变成真实客户档案。

## 报告输出

使用 `scripts/render_reports.py --help` 查看独立PDF导出接口，使用 `scripts/render_pptx.py --help` 查看可编辑PPT接口。先运行PDF脚本得到报告及 report-model.json，再由PPT脚本读取该模型。PDF需要ReportLab，PPT需要python-pptx，均列于requirements.txt。可选的render_reports.mjs依赖专有宿主运行时，默认使用Python导出器。遇到环境不支持时使用宿主的演示文稿能力生成可编辑PPT，再转换并检查PDF，不把HTML改扩展名作为PPT/PDF。

读取匹配JSON作为报告事实输入，保留confirmed/conditional及核实事项。输出后抽取文本检查关键数字、渲染检查分页与溢出；表格应可编辑。用户未要求发送时，交付文件给经纪人审阅。

## 带看复盘

按 `examples/viewing.json` 整理文字或转写后的反馈。逐盘保留客户喜欢、不喜欢、提出的问题、经纪人已回答与尚未回答的事项；没有反馈时不编造满意度。

```bash
python3 scripts/assistant.py viewing --input /path/to/viewing.json --output /path/to/review.json
python3 scripts/render_viewing.py --input /path/to/review.json --output /path/to/带看复盘报告.pdf
```

独立复盘PDF需要ReportLab，不依赖楼盘数据库。输入中的 `is_simulation` 必须明确为布尔值；正式记录使用 false，虚构演示使用 true。长反馈自动分页，逐项保留客户原话、问题、待核实事项和下一步。明确哪些偏好已改变、哪些硬条件保持、下一轮需要验证的项目。对经纪人内部的佣金、报备、客户归属、谈判底线，不放入客户报告。

## 购房测算

先确认成交总价、首付金额、贷款期限、利率、还款方式；利率和首付比例不设为“上海现行政策”默认值。以下仅是自定义参数示例：

```bash
python3 scripts/assistant.py mortgage --price-wan 1000 --down-payment-wan 400 --annual-rate 3 --years 30 --method equal-payment --output /path/to/mortgage.json
```

报告列明贷款本金、首付、月供、总利息和输入假设。税费、评估费、中介费、车位等另列，不用首付代替全部现金支出。按官方来源和个人情况核对资格、贷款及材料；材料参考见 `references/transaction-checklist.md`，政策来源见 `references/policy-sources.json`。

## 数据维护

用 `scripts/inventory_tools.py audit` 查看覆盖率、缺失和冲突。每条更新保留来源、采集时间、批次及核实状态；全量目录和完整详情分别统计。具体套数、佣金、优惠、认购、限购和利率是时效字段，对客使用前重新核实。

数据源说明见 `references/data-sources.json`。本包的合成数据仅用来验证流程；原始小程序文本、图片、客户资料和渠道文件不随开源包发布。需要授权接口/导出或当前可见页面才能取得的数据，不通过猜测填满。
