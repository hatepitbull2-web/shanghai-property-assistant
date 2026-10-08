# 上海房产销售助理

面向经纪人的可运行首版 Skill：客户需求整理、房源初筛、客户比较报告、带看复盘、首付与月供测算、数据覆盖审计。代码及原创文档按 MIT 开源，示例均为合成数据。发行包不包含小胖看房团完整数据库、第三方图文、真实客户或渠道佣金资料。

## 在 WorkBuddy 使用

1. 打开“专家·技能·连接器 → 技能 → 添加技能 → 上传技能”，选择本 Skill ZIP。
2. 安装后，指定你的房源JSON和客户条件。先使用合成示例验证运行，再连接经纪人的实际数据库。
3. 可直接说：“使用上海房产销售助理，先把我的客户需求整理成条件，按我提供的库存筛选3至5个候选，生成客户比较PDF；缺失信息列为待核实。”

WorkBuddy官方结构说明：https://open.workbuddy.cn/docs/skill 。安装成功只证明宿主识别该技能，完整业务运行还需要Python环境、数据文件和报告依赖。豆包目前不提供本包已验证的直接导入路径。

## 本地快速验证

Python 3.9+。筛选、测算、复盘及审计只使用标准库。以下命令从本目录执行，输出到 `generated/`：

```bash
python3 scripts/assistant.py match --inventory examples/synthetic_inventory.json --customer examples/customer.json --output generated/match.json
python3 scripts/assistant.py mortgage --price-wan 1000 --down-payment-wan 400 --annual-rate 3 --years 30 --method equal-payment --output generated/mortgage.json
python3 scripts/assistant.py viewing --input examples/viewing.json --output generated/viewing.json
python3 scripts/inventory_tools.py audit --inventory examples/synthetic_inventory.json --as-of 2026-10-08 --output generated/audit.json
python3 -m unittest discover -s tests -v
```

演示利率3%、总价1000万元、首付400万元只是计算输入，不是市场报价或首付政策。等额本金使用 `--method equal-principal`。当前测算为单笔贷款，组合贷可分别计算后合并逐月现金流，不能将不同利率简单平均。

## PDF 与可编辑 PPT

PDF唯一额外Python依赖：`reportlab>=4.0`。用虚拟环境安装 `requirements.txt`，然后运行：

```bash
python3 scripts/render_reports.py --inventory examples/synthetic_inventory.json --customer examples/customer.json --match-json generated/match.json --mortgage-json generated/mortgage.json --viewing-json generated/viewing.json --output-dir generated --work-dir generated/build --demo-recap
```

中文字体可用 `--font /path/to/font.ttf` 指定；没有可用字体时使用渲染器的CID后备字体。正式客户需要使用实际客户JSON及对应匹配结果，移除演示标记前必须核实客户信息。

可编辑PPT使用 `python-pptx>=1.0`，已列入 `requirements.txt`，读取PDF生成时保存的同一份报告模型：

```bash
python3 scripts/render_pptx.py --model generated/build/report-model.json --output-dir generated
```

还提供一个可选的精细排版导出器 `render_reports.mjs`，需要宿主提供 `@oai/artifact-tool`，不承诺可从公共npm安装：

```bash
node scripts/render_reports.mjs --model generated/build/report-model.json --output-dir generated
```

没有该运行时的WorkBuddy环境，使用上面的Python导出器即可，也可调用宿主的“PPT 演示文稿”技能。独立PDF与Python版PPT不依赖专有运行时。已交付的演示PPT可直接打开编辑。

带看结束后，先用 `assistant.py viewing` 整理反馈，再直接生成复盘PDF，无需楼盘库：

```bash
python3 scripts/render_viewing.py --input generated/viewing.json --output generated/带看复盘报告.pdf
```

输入必须明确 `is_simulation`。使用真实反馈时填 false；演示填 true。按项目保留喜欢、不满意、问题、待核事项、客户原话、后续行动及偏好变化，长内容自动分页。

## 接入实际库存

把业务数据保存在包外或 `private-data/`，通过 `--inventory` 指定路径。支持当前采集格式的 `楼盘` 数组。字段不完整时输出条件候选，不把参考总价区间当成具体可售房号；只有同一套已核实房源同时满足硬条件且数据有效，才标已确认。

`confirmed` 是已提供证据下的筛选结论，不是购房资格、产权或贷款审批。学区、通勤、噪声、佣金、个人资格和实际利率尚未建立可靠来源时保留待核。二手房引擎可以接收同格式房源，但此版没有上海二手成交数据库。

批量补采记录格式：

```json
{
  "source": "授权导出文件或可见页面来源",
  "observed_at": "2026-10-08",
  "updates": [{"name": "目录中已有项目", "batch": "与原记录相同的批次", "fields": {"物业费": "已核实值"}}]
}
```

```bash
python3 scripts/inventory_tools.py merge --inventory private-data/inventory.json --observations private-data/updates.json --output private-data/inventory-new.json
```

工具保留观察历史、来源和日期，同批幂等；不覆盖输入文件，不把新销售批次混入旧批次。跨批次应先建立独立批次快照。

## 当前边界与开源范围

- 数据覆盖以 `inventory_tools.py audit` 实际结果为准，不能据本包认定上海楼盘全量。
- 小胖看房团的批量接口尚未打通。原小程序采集依赖可见页面及有效登录；来源研究收录在 `references/data-sources.json`。
- 政策索引在 `references/policy-sources.json`，使用前根据业务日期复核原文及适用条件。不会自动承诺个人资格或利率。
- 本包不主动联系客户、发送报告、认购或办理贷款。数据与报告由经纪人核实后使用。
- 发布代码不等于取得第三方内容再分发许可。MIT仅覆盖本包原创程序、说明和合成示例；真实业务文件由各使用者自行管理。

## 验收方式

用同一客户条件和同一数据版本重复执行，检查硬条件失败是否排除、未知是否明确保留、推荐是否附来源、月供能否独立复算、PDF是否无溢出。测试覆盖跨房源拼接、未知预算、佣金隔离、零利率、贷款结清、批次冲突和输入文件保护。
