---
name: xlsx-builder
description: 生成格式化的 Excel（.xlsx）表格，支持多工作表、表头样式、列宽自适应、数字格式、公式、冻结首行、筛选和汇总行，也可附带柱状/折线/饼图。用户要求"做个表格/导出 Excel/整理成表"时使用。
---

# xlsx-builder：生成 Excel

## 流程
1. 整理数据为下面的 JSON 规格（数据量大时可先 `write_file` 到 `data.json`）。
2. 调用 `run_skill_script`：
   - name: `xlsx-builder`
   - script: `scripts/build_xlsx.py`
   - args: `["输出文件名.xlsx"]`，或 `["输出.xlsx", "--spec", "data.json"]`
   - stdin: JSON 规格字符串
3. 成功后简要说明表格结构。

如果用户上传了 CSV/Excel 需要处理，先用 `run_python`（pandas）读取 `uploads/` 下的文件并整理，再生成规格或直接用 pandas 输出。

## JSON 规格
```json
{
  "sheets": [
    {
      "name": "销售明细",
      "headers": ["月份", "销售额", "成本", "毛利"],
      "rows": [["1月", 12000, 8000, "=B2-C2"], ["2月", 15000, 9000, "=B3-C3"]],
      "number_formats": {"销售额": "#,##0", "成本": "#,##0", "毛利": "#,##0"},
      "totals": {"label": "合计", "sum": ["销售额", "成本", "毛利"]},
      "chart": {"type": "bar", "title": "月度销售", "x": "月份", "y": ["销售额", "毛利"]}
    }
  ]
}
```

说明：
- rows 中以 `=` 开头的字符串会作为公式写入。
- number_formats 的键是表头名，值是 Excel 格式码，如 `0.00%`、`yyyy-mm-dd`。
- chart.type 可选 `bar`、`line`、`pie`（pie 只取 y 的第一列）。
- totals 会在末尾追加一行求和公式。
