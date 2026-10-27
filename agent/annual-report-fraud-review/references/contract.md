# 底稿与工具契约

`schemas/workpaper.schema.json` 是字段契约。使用 `init` 生成完整骨架，再按实际工作填充；不要删去未执行的规则。所有文件 UTF-8，金额是无千位分隔的十进制字符串，未知为 `null`。

## 输入与来源

`extract` 接收一个本地 PDF，输出 `document.json`：`report` 保存文件名、SHA256 和物理页数；`pages` 保存每页文本、候选表格与图像存在标记。抽取成功只表示获得候选内容，不能证明表头、列位或完整性正确。扫描页没有文本时标记需要视觉/OCR，不自动填入识别数值。

`init` 把物理页和 101 项规则全部设为未完成，并绑定规则及图谱的规范化内容 SHA256（解析JSON后按键排序、UTF-8、不转义非ASCII字符、紧凑分隔符），避免操作系统换行差异造成知识不一致。PDF及工作底稿来源哈希针对实际文件字节。公司名、年报年度、合并范围、币种和会计基础须从 PDF 填写；从文件名猜测不能代替正文确认。

一个底稿只对应这一份 PDF。`evidence.page` 是从 1 开始的物理页；`locator` 写章节/附注/表名，`quote` 保存实际支持判断的原文。表格证据另填 `table_header`、`row` 和 `column`。跨页表头和数据行应分别设证据并共同引用。状态：`unverified` 未核实、`text_checked` 已核文本、`page_checked` 已回看原页。数值事实设 `verified=true` 前，所引证据须已回看原页。

## 共同财务口径

每个事实和计算都有 `context`：

| 字段 | 含义 |
| --- | --- |
| entity / scope | 法人或集团，以及合并/母公司等范围，不能用同一简称混同 |
| basis | PDF声明的会计基础与原报/正式重述/分析调整口径 |
| currency | 金额币种，如 CNY；无量纲或数量用 null |
| unit / scale | 原单位与换到基本单位的倍数，如 万元 / "10000"；百分数 % / "0.01" |
| start / end | YYYY-MM-DD；时点余额 start=null，期间流量有真实起止日 |
| kind | balance 余额、flow 流量、ratio 比例、days 天数、quantity 实物量、unit_rate 单位数量金额 |

`concept` 是一致的经济科目标识，如 `trade_receivables_gross`。平均余额和增长率输入须为同科目。标签 `label` 保留原科目名称，不能把投资收益总额改称权益法收益。

示例事实（所引证据须另行登记）：

```json
{
  "id": "F001",
  "label": "应收账款账面余额",
  "concept": "trade_receivables_gross",
  "value": "240",
  "context": {
    "entity": "示例集团", "scope": "合并", "basis": "报告声明口径 原报",
    "currency": "CNY", "unit": "万元", "scale": "10000",
    "start": null, "end": "2025-12-31", "kind": "balance"
  },
  "evidence_ids": ["E001"],
  "verified": true
}
```

这是字段示例，不是任何真实企业事实。

## 计算与勾稽

`calculations` 按依赖顺序排列，输入 `terms[].ref` 只能引用事实或前序计算；每项有真实目的 `rationale`。不得使用任意代码或字符串表达式代替受支持的运算。

| op | 定义 |
| --- | --- |
| sum | Σ 输入 × 对应 weight；支持滚动表正负项 |
| difference | 第一个输入 - 第二个输入 |
| ratio | 第一个输入 / 第二个输入 |
| growth | (本期 - 前期) / 前期；非正基数或亏损变化不给常规增长率 |
| average_balance | 真实期初期末余额的算术平均，仅适用于所定义的简单平均口径 |
| product | 同口径金额或数量 × 无量纲比例；不生成未经支持的数量维度 |

sum 之外 `weight` 均为 "1"。结果在统一基本单位运算后乘 `multiplier`，再除以输出 `scale` 供显示。百分数转换用 `scale="0.01"`、`multiplier="1"`，后续引用仍保持正确比例，不能另乘100。周转天数使用 `multiplier` 等于所分析期间的实际天数。单位数量金额的输出单位明确为如“万元/店”，其 scale 与金额倍数一致。

`denominator` 取 `positive`（默认建议）或经解释的 `nonzero`；0 分母始终不算。常规增长率不因设置 nonzero 而允许负基数。`null`、未核实输入及不可用前序结果会向下游传播为不可计算。

`period_rule`：`same` 要求同期间/时点；`comparison` 按本期、前期比较同长度期间；`rollforward` 允许期初余额、期间流量及期末余额；`balance_flow` 连接同期间流量和期末余额/本期间平均余额。跨范围及币种合成前须有有据的调整事实，不通过改标签绕过口径检查。

滚动计算的输出可为 `kind=balance`，但 `start` 须填写滚动期间起点，`end` 是期末；这是分析期间标记，原始时点余额仍使用 `start=null`。余额净变动用 `difference + comparison`，输出保持 `balance/start=null`；该差额不会因此变成已证实的现金流。计算实际现金影响须另有非现金、并购等调整证据。

历史准备率应用于本期应收等敏感性测算使用 `product + sensitivity`：一个输入为本期金额/数量，另一个为该 PDF 已披露或可复算的当期或历史比例，输出期间与本期基数相同。`rationale` 说明保持该比例的分析假设；结果不等于应计提额。该规则不允许使用未来比例或无证据的自造参数。

计算完整示例（F002须为同范围同期间收入）：

```json
{
  "id": "C001", "label": "期末应收周转天数", "concept": "receivable_days_end",
  "op": "ratio", "terms": [{"ref": "F001", "weight": "1"}, {"ref": "F002", "weight": "1"}],
  "context": {
    "entity": "示例集团", "scope": "合并", "basis": "报告声明口径 原报",
    "currency": null, "unit": "天", "scale": "1",
    "start": "2025-01-01", "end": "2025-12-31", "kind": "days"
  },
  "period_rule": "balance_flow", "multiplier": "365", "denominator": "positive",
  "rationale": "观察同范围期末客户债权，与使用平均余额的周期指标分开。"
}
```

`reconciliations` 用 `actual`、`expected` 引用事实/计算；`tolerance` 是 actual 的显示单位，`basis` 说明披露精度和容差依据。残差为 actual−expected。容差内仅表示数值接近；结果不得自动关闭财务疑点。计算结果由工具生成，不手填进事实以冒充披露。

## 覆盖与发现

每条 `coverage` 保存 `rule_id`、`status`、理由、实际程序、证据/计算/发现 ID 和限制。5种状态如下：

- `finding`：已执行并有疑点；必须引用具体发现。
- `no_exception`：在已执行的范围未见异常，不代表排除舞弊。
- `not_applicable`：有业务及披露证据证明规则不适用。
- `insufficient_evidence`：已尝试程序但关键资料不足；写清具体缺口和已读范围。
- `not_completed`：尚未执行或未做完，不能假装资料不足。

前三类须有实际程序、已核实证据和理由；工具不替 Agent 判断这些内容是否充分。证据不足与未完成分列，不与已执行项合成完成率。

`findings` 保存独立发现；`rule_ids=[]` 表示清单外发现。字段包括：标题、事实、机制、支持证据、反证、相关计算、替代解释及检验结果、当前判断、可能影响、核查优先级、证据强度、尚缺资料、关闭条件、重复影响组和复核记录。缺少反证用空列表并在文字中说明未取得，不能造一条“反证不存在”。

`review.status=verified` 仅在重新查看原页后填写，需保留原页证据和复核说明。首次核对与再次复核不是同一动作；实质判断依然由 Agent 与使用者负责。不要用多个模型意见相同代替原页复核。

## 输出与验证

`summary`、发现和覆盖理由中的判断数字用 `{{F001}}`、`{{C001}}` 引用；工具代入相同底稿的数值、单位与ID。未确定数值显示“未确定”。其他叙述里的手写数字不会被工具自动识别，交付复核须检查其是否也已接入底稿。禁止输入未经验证的舞弊概率和自造综合风险分。

`validate --pdf` 对照源文件 SHA256 和实际物理页数；结构、引用、口径定义错误返回非零退出码。未阅读、未完成、不可计算及未复核状态作为提示保留。工具不能鉴定引文是否支持财务结论，也不能代替表格列位和交易真实性核验。

`calculate` 产生确定性结果并附工作底稿 SHA256，逐项写出不可计算原因；即使覆盖结论需根据这些失败结果修正，也先生成诊断结果。`validate`/`export` 会要求修正已关闭规则对不可用计算的引用。`export` 同时生成机器底稿、计算结果、Markdown 和 Word，标明阅读及规则覆盖，展示所有资料缺口。不要求把证据不足改成通过才能导出。

正式留存：输入 PDF、document.json、最终 workpaper.json、calculations.json 和报告。可清理的只是重复提取、临时页图与缓存；不能为了清理中间文件删除支撑最终结论的底稿或证据。工具不自动删除任何用户文件。
