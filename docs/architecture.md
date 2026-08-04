# 处理流程

```text
一级合同号文件夹
  -> PDF清单和SHA256指纹
  -> 逐页原生文本质量检查
      -> 可用：保留原生文本
      -> 不可用：300DPI整页OCR
  -> 尾页/签章页600DPI去红章增强OCR
  -> 合并页级文本与置信度
  -> 合同性质判定
      -> 运维类：停止深度抽取
      -> 集成实施类：工期/起算/节点/条款抽取
  -> 签约日期独立抽取（所有合同）
  -> 字段证据和人工复核标记
  -> 单合同JSON断点
  -> Excel三表汇总
```

抽取规则集中在 `src/contract_extraction/rules.py`，日期识别及工期日期运算在
`src/contract_extraction/date_utils.py`，PDF/OCR 和签章增强在 `src/contract_extraction/pdf_io.py`。
