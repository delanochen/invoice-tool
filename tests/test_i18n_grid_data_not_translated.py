import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_grid_cell_content_uses_full_key_translation_only():
    """表格单元格镜像容器 .grid-cell-content 的直接文本只能整词精确翻译。

    防止 MutationObserver 对数据值（合同名称、客户/员工姓名、厂家、日期、金额等）
    做子串部分翻译："宁德时代" 被 "时"→"when" 拆成 "宁德when代"，
    "2026新协议" 被 "新"→"New" 拆成 "2026New协议"。
    """
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")
    assert "target.classList.contains(\"grid-cell-content\")" in source
    assert 'parent.classList?.contains("grid-cell-content")' in source


def test_partial_translation_would_mangle_data_values():
    """演示为什么网格数据值禁止部分翻译（回归护栏，防止将来放开）。"""
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")
    # 若将来有人把网格数据改回 allowPartial=true，这两个断言会立刻失败。
    assert '"时": "when"' in source  # 字典仍存在单字映射，数据绝不能走部分翻译
    assert 'const partial = !target.classList.contains("grid-cell-content")' in source


def test_translate_full_key_protects_data_values():
    """translate(value, allowPartial=false) 下：数据原样、UI 状态词仍翻译。"""
    script = (
        "const m=require('./static/ui-i18n.js');"
        "const out=[];"
        "for(const s of ['宁德时代','2026新协议','售后服务协议'])"
        "out.push(m.translate(s,false)===null);"
        "for(const s of ['支出','启用','未匹配']){"
        "const r=m.translate(s,false); out.push(r!==null && r!==s);}"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "[true,true,true,true,true,true]"
