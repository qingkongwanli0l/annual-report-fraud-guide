# 单份年报财务舞弊风险排查包

供支持文件访问、PDF 原页查看和代码执行的生产 Agent 使用。核心为 14 个专题、101 条规则及 14 项指标；没有绑定模型供应商、Agent 框架、API Key 或在线知识库。运行时仅使用指定的一份 PDF 中的公司事实。

## 接入

Python 3.10 或以上，在生产任务环境安装声明依赖：

```bash
python -m pip install -r requirements.txt
```

支持 Agent Skills 的宿主加载整个 `annual-report-fraud-review` 目录。其他宿主将 `SKILL.md` 作为任务执行说明，通过自身文件工具读取引用的资料；把下列命令作为受限工具提供给 Agent。工作目录不同也可使用脚本绝对路径，知识文件始终相对脚本定位。

启动任务指令：

> 按本目录 SKILL.md 和分析方法，对指定的单份年报 PDF 完成财务舞弊风险排查。仅使用该 PDF 的公司事实，覆盖全部规则和报告重要披露。保存原页证据、事实、复算、逐项检查及反证，先呈现重要发现，完整显示未完成项和资料缺口。禁止把资料不足或经营困难直接认定为舞弊。

此段是交付给使用者的任务入口。实际方法和接口以版本化知识文件及底稿契约为准，不依靠一段角色提示完成分析。

## 命令

从本目录执行，输出写到单独的任务目录 `run/`：

```bash
python scripts/review.py rules
python scripts/review.py rules --topic 01
python scripts/review.py rules --id 001
python scripts/review.py neighbors --id n001 --depth 1
python scripts/review.py extract annual-report.pdf --output run/document.json --tables
python scripts/review.py search run/document.json --query 应收账款 关联方 保理
python scripts/review.py render annual-report.pdf --pages 25 26 --output run/pages
python scripts/review.py init run/document.json --output run/workpaper.json
```

`rules` 无参数返回全部规则的索引；按专题/ID读取详细步骤、所需资料、正常解释和完成条件。`neighbors` 返回指定概念的相邻关系，不会将概念推理写成公司证据。

Agent 依据 [底稿契约](references/contract.md) 编辑 `workpaper.json`，在实际阅读和核查后填写证据、事实、计算、覆盖结果与发现，再执行：

```bash
python scripts/review.py calculate run/workpaper.json --output run/calculations.json
python scripts/review.py validate run/workpaper.json --pdf annual-report.pdf
python scripts/review.py export run/workpaper.json --output run/report
```

产物包括 `workpaper.json`、`calculations.json`、`report.md`、`report.docx`。全文索引 `document.json` 与源 PDF 的 SHA256 对应。宿主应保留输入 PDF 和正式底稿；按自身权限管理企业资料。脚本不调用模型、不发网络请求、不自动判定规则通过。

`validate` 的错误表示结构、ID、版本、来源或计算定义存在问题；提示表示未阅读、未完成、证据未核实或需要人工判断。结构通过不等于取数正确、准则判断正确或不存在舞弊。资料不足的报告仍可导出，进度与缺口会写入报告。

## 验证与使用边界

自动化用例覆盖已知金额、口径错配、零分母、负数增长、历史比例敏感性、引用错误、不同页的表格列序变化、扫描页标记及报告导出。测试资料为明确标记的合成资料，包含异常与正常解释；它们不能提供真实企业的检出率或误报率。

真实生产启用前，使用经人工复核的年报样本对取数准确性、重要疑点遗漏、误报、引文支持程度和复算一致性进行验收。当前交付不包含经独立财务专家标注的真实企业基准，不声称已经达到审计或鉴证水平。

开发验证：`python -m pip install -r tests/requirements.txt`，然后 `python -m unittest discover -s tests -v`。

## 版本与许可

知识与底稿格式版本：`1.0.0` / `1.0`。知识包包含规则和图谱的 SHA256；更新后旧底稿须明确重新核查，不能静默改版本。图谱是概念关系，不是目标企业的交易关系。

维护者：qingkongwanli0l。方法、规则及图谱采用 CC BY-SA 4.0，代码采用 MIT，分别见随包许可文件。包内不包含企业真实资料。原始报告的权利仍归其权利人。
