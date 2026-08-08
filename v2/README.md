# 合同解析系统 V2

V2 是与仓库根目录 V1 **物理隔离**的本地合同解析系统。它沿用 V1 已验证的前后向解析、结构化规则、风险识别、人工纠正、Web 和 Excel 导出逻辑，仅在 V2 副本中升级 OCR、页面路由、表格结构恢复和清单输入质量。

页面顶部会明确显示：`合同解析系统 V2`、`OCR：PP-OCRv6`。V2 默认使用 `127.0.0.1:8001`，数据库为 `data/review_output/contract_review_v2.db`，不会读写 V1 的数据库或 OCR 缓存。

## 安装

建议使用 Python 3.10 或 3.11 的独立虚拟环境：

```powershell
cd C:\Users\keyan\Documents\contract_extraction\v2
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

RapidOCR 3.9 及以上默认使用 PP-OCRv6；普通安装包含 `rapidocr` 和 `onnxruntime`，全部在本机执行。

## 启动 Web

```powershell
cd C:\Users\keyan\Documents\contract_extraction\v2
python scripts\run_server_v2.py
```

浏览器打开：<http://127.0.0.1:8001>

V2 独立环境变量：

```powershell
$env:CONTRACT_V2_ROOT = "C:\合同根目录"
$env:CONTRACT_V2_OUTPUT = "C:\合同审查结果V2"
python scripts\run_server_v2.py
```

未设置时默认目录为：

- `v2/data/contracts/`
- `v2/data/review_output/`
- `v2/data/ocr_cache/`
- `v2/data/review_output/contract_review_v2.db`

V2 不读取 `CONTRACT_ROOT`、`CONTRACT_OUTPUT`。

## CLI

```powershell
contract-extract-v2 --input "C:\合同根目录" --output "C:\合同抽取结果V2"
contract-review-v2 --input "C:\合同根目录" --output "C:\合同审查结果V2"
```

命令名不会覆盖 V1 的 `contract-extract`、`contract-review`。

## OCR 流水线说明

```text
PDF
 ↓
原生文本提取
 ↓
文本/结构质量判断
 ↓
页面分类
 ├─正文
 │   └─原生文本 / 300DPI OCR
 │
 ├─低质量页
 │   └─300DPI → 质量评价 → 450DPI Retry
 │
 ├─签章页
 │   └─600DPI增强 → OCR结果融合
 │
 └─表格页
     ├─OCR bbox结构恢复
     └─PP-StructureV3（可选）
 ↓
统一PageOcrResult
 ↓
结构化合同解析
 ↓
前后向履约风险规则
```

### 页面路由

- 正文页：原生文本内容和结构都合格时直接使用，不执行 OCR。
- 低质量页：先执行 300 DPI RapidOCR。平均置信度不足、关键字段不完整、文本过短、字符/数字/型号碎裂或表格无法成行时，才触发 450 DPI 重试。
- 表格页：保留每个 OCR 块的文字、置信度和 bbox，根据 y 中心、框高、纵向重叠及 x 排序恢复视觉同行，并将结果独立保存为 `page.table_rows`。
- 签章页：普通 OCR 与 600 DPI 去红章增强 OCR 按空间重叠、文本相似度和置信度融合，同一位置只保留一份；不再把原生文本、普通 OCR、增强 OCR 简单追加。

### OCR 诊断与缓存

每页诊断包含页面类型、原生文本质量、结构质量、OCR 是否使用、DPI、重试原因、平均置信度、签章增强、表格引擎和表格行数。缓存键包含 PDF SHA256、页码、引擎、引擎版本、模型、DPI、预处理、OCR pass 和 enhanced 标志；模型升级后不会错误命中旧缓存。

## PP-StructureV3 可选增强

普通用户无需安装 Paddle。默认配置为：

```json
{
  "table_engine": "auto",
  "enable_ppstructure": false
}
```

需要增强表格识别时，在 Python 3.10/3.11 Windows CPU 环境执行：

```powershell
python -m pip install -e ".[table]"
```

然后在 `config/default.json` 中把 `enable_ppstructure` 改为 `true`。如果 PaddleOCR/PaddlePaddle 未安装、初始化失败或运行异常，V2 自动回退到 `RapidOCR + ocr_boxes`，Web、CLI 和普通合同解析仍可启动。`table_engine` 支持 `auto`、`ocr_boxes`、`ppstructure`、`off`。

## 清单解析优先级

1. PP-StructureV3 表格结果（启用且成功时）；
2. RapidOCR bbox 恢复的 `table_rows`；
3. PDF 原生文本表格；
4. V1 已验证的纯文本/正则规则回退。

统一去重键包含清单类型、标准化名称、标准化型号、单位、数量、来源文件和页码。同一 PDF 同一页的多路 OCR 重复只保留一条；不同合同中的相同设备不会跨文件去重。

`软件部署实施`、`硬件部署实施及系统集成`、`系统集成`、`安装调试`、`系统迁移`、`数据迁移`、`培训`、`技术服务`、`驻场服务`、`实施服务`、`维保服务`继续归入服务/实施内容，不会仅因出现在报价表中就变成采购设备。

## 本地优先和数据安全

V2 不包含任何云 OCR、在线 VLM 或合同上传接口。PDF、页面图像、OCR 缓存、SQLite 数据库和 Excel 结果都保留在本机配置目录中。

## 测试

```powershell
cd C:\Users\keyan\Documents\contract_extraction\v2
python -m pip install -e ".[test]"
python -m pytest
```

测试覆盖文本/结构质量、页面分类、RapidOCR 3.x 返回适配、签章融合去重、bbox 同行恢复、清单去重、服务与设备分类，以及复制自 V1 的业务回归用例。

## 已知限制

- 无框、跨页、合并单元格非常复杂的表格仍可能需要 PP-StructureV3 或人工复核；
- 超大扫描 PDF 首次 OCR 会消耗较多时间和内存，后续会命中独立缓存；
- 手写字、低分辨率复印件、严重红章遮挡可能无法完全恢复；
- PP-StructureV3 的 Paddle 运行时体积较大，Windows 安装兼容性取决于 Python 和 Paddle 官方可用轮子；失败时自动回退，不影响基础能力。
