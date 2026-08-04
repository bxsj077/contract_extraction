# 前后向合同智能解析与履约风险审查系统

当前版本已从单合同“文档解析与证据定位层”升级为项目级前后向合同审查平台。原 `contract-extract` 入口继续保留。

## 项目级审查快速开始

推荐目录结构如下。一级目录名称就是前向合同编号；前向可包含主合同及附件，后向允许任意多份合同：

```text
合同根目录/
├─ JSNJA2513970CGN00/
│  ├─ 前向/
│  │  ├─ 前向主合同.pdf
│  │  └─ 技术附件.pdf
│  └─ 后向/
│     ├─ 后向合同A.pdf
│     └─ 后向合同B.pdf
└─ JSNJA2600045CGN00/
   ├─ 前向/
   └─ 后向/
```

批量处理并导出七张工作表：

```powershell
cd C:\Users\keyan\Documents\contract_extraction
python -m pip install -e .
contract-review --input "C:\合同根目录" --output "C:\合同审查结果"
```

启动 Web 系统：

```powershell
$env:CONTRACT_ROOT="C:\合同根目录"
$env:CONTRACT_OUTPUT="C:\合同审查结果"
python scripts\run_server.py
```

浏览器访问 `http://127.0.0.1:8000`，API 文档位于 `http://127.0.0.1:8000/docs`。

首页可直接输入前向合同编号，并多选上传前向附件和多份后向合同。未设置环境变量时，文件和结果默认保存为：

```text
C:\Users\keyan\Documents\contract_extraction\data\contracts\<前向合同编号>\前向\*.pdf
C:\Users\keyan\Documents\contract_extraction\data\contracts\<前向合同编号>\后向\*.pdf
C:\Users\keyan\Documents\contract_extraction\data\review_output\contract_review.db
C:\Users\keyan\Documents\contract_extraction\data\review_output\项目明细\<项目编码>\
```

首页会显示本次服务实际使用的上传目录、结果目录和数据库路径，避免误扫描代码目录。

项目列表最右侧提供“删除项目”按钮。确认后会永久删除该项目上传的前后向合同 PDF、OCR 缓存、项目明细、任务、复核事项及人工纠正记录；正在解析的项目必须等待任务结束后才能删除。

人工纠正“工期数值”时既可输入纯数字，也可直接输入“供货要求等合同文件另有约定”等文字。纯数字写入工期数值；文字会自动将工期数值置空，并同步写入“工期提取结论”，避免把引用其他合同文件的约定误当成数值工期。

系统包括：项目完整性扫描、前后向分别解析和缓存、SQLite 状态库、设备/数量/单位比较、实际时间区间及安全缓冲比较、实施范围和责任强度比较、风险分级、时间轴、人工复核接口、JSON/Excel 导出及 Web 看板。最终风险仅由版本化程序规则生成。

## 第一层兼容入口

面向“一个合同号一个文件夹、文件夹内一个或多个 PDF”的本地批处理项目。源 PDF 只读，OCR、缓存、JSON 和 Excel 全部写入独立输出目录。

## 已实现

- 原生文本层质量检查；质量不足时整页 300DPI 本地 OCR。
- 签章尾页自动执行 600DPI 局部增强思路：利用红色通道削弱红章，提高被印章遮挡日期的识别率。
- 合同性质仅输出 `运维类`、`集成实施类` 或空值；运维类停止工期和条款深度抽取。
- 集成实施类抽取工期、工期起算方式、具体日期、预计结束日期、到货/安装/部署/调试/上线/试运行/验收/交付/质保节点、服务内容、乙方义务和关键条款。
- 甲方、乙方、其他方签约日期分别留列，并给出合同签约日期。
- 三方合同只有部分签约日期时，可按配置用已识别日期参与“签约起算”推算，但在说明和证据中明确其不代表全部盖章生效日。
- 低置信度或印章遮挡日期自动标为人工复核。
- 每个非空抽取字段尽量保留来源文件、PDF 页码、原文、OCR/字段置信度与冲突说明。
- 合同级 JSON 断点续跑、OCR 缓存、单合同重跑、强制重跑。

## 安装

需要 Python 3.10+。OCR 全程本地执行，不调用在线识别服务。

```powershell
cd C:\Users\keyan\Documents\contract_extraction
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

## 目录约定

```text
合同根目录/
├─ HT2026001/
│  ├─ 主合同.pdf
│  └─ 附件.pdf
├─ HT2026002/
│  └─ 合同扫描件.pdf
└─ _合同提取结果/       # 建议输出到这里；不会当作合同扫描
```

## 执行

```powershell
contract-extract `
  --input "C:\path\合同根目录" `
  --output "C:\path\合同根目录\_合同提取结果"
```

也可不安装命令入口：

```powershell
python scripts\extract_contracts.py --input "C:\path\合同根目录" --output "C:\path\输出目录"
```

只处理指定合同号：

```powershell
contract-extract --input "C:\path\合同根目录" --output "C:\path\输出目录" --contracts HT2026001 HT2026003
```

强制重跑（忽略 JSON 断点与 OCR 缓存）：

```powershell
contract-extract --input "C:\path\合同根目录" --output "C:\path\输出目录" --force
```

## 输出

- `合同信息提取_YYYYMMDD_HHMMSS.xlsx`
  - `合同提取结果`
  - `字段证据`
  - `待人工复核`
- `contracts/<合同号>.json`：单合同结构化结果和证据，便于断点续跑。
- `ocr_cache/<合同号>/...`：普通 OCR 与签章增强 OCR 缓存。
- `run_summary.json`：本次运行摘要。

## 日期规则

默认配置位于 `config/default.json`：

- `signing_date_policy = latest_recognized`：多个签约日期候选中采用最晚的已识别日期作为合同签约日期。
- `allow_partial_signing_date = true`：允许部分签约方日期参与签约起算推算。
- 部分日期缺失会保留说明，不声称该日期就是三方全部盖章后的法律生效日。
- 签章增强 OCR 置信度低于阈值时，`签约日期需人工确认=是`。

规则抽取是可审计的初筛工具，不替代人工法律审查。建议重点复核签章页、生效条件、工作日计算和模糊扫描件。

## 开发验证

```powershell
python -m unittest discover -s tests -v
python -m compileall src scripts
```
# 查看后台运行状态

网页的“最近处理任务”会持续显示任务状态，任务记录保存在 SQLite 中，服务重启后仍可查询。PowerShell 也可直接查看：

```powershell
python scripts/check_status.py --output "C:\Users\keyan\Documents\contract_extraction\data\review_output" --project JSNJA2513970CGN00
Get-Content "C:\Users\keyan\Documents\contract_extraction\data\review_output\logs\contract_review.log" -Wait
```

## 人工纠正

网页首页的“人工纠正识别结果”支持按项目、合同和字段保存人工确认值。合同标识使用：

- `前向`：前向合同及附件合并结果；
- `后向:001`、`后向:002`：按后向目录文件名排序后的第1、第2份后向合同。

纠正记录保存在 `contract_review.db` 的 `field_corrections` 表中，重新OCR不会覆盖。保存或删除纠正后，系统会基于OCR缓存自动重新计算审查结果。工期没有明确数字时，“工期数值”保持为空，并通过“工期提取结论”和“工期计算状态”反馈“随道路建设周期”“双方另行确认”或“按招标文件及投标文件执行”等非量化约定。
