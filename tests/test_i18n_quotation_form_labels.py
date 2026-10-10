import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# (源文案, 英文译文) —— 报价单编辑表单「结算配置 / 定价汇总」区块的 UI 文案。
# 之前整词键缺失：部分被完整漏译（结算配置/固定总价），
# 部分被子串部分翻译拆坏（"里程＋时长" → "Mileage＋when长"，"自驾交通计费方式" → "Self driveTravelbilledway"）。
QUOTATION_FORM_LABELS = {
    "结算配置": "Settlement Configuration",
    "固定总价": "Fixed Total Price",
    "仅时长": "Travel Time Only",
    "里程＋时长": "Mileage + Travel Time",
    "自驾交通计费方式": "Self-driving Transport Billing Method",
    "自驾里程单价（$/mile）": "Self-driving Mileage Unit Price ($/mile)",
    "自驾交通工时单价（$/hour）": "Self-driving Transport Hours Unit Price ($/hour)",
    "折扣金额": "Discount Amount",
}


def test_quotation_form_labels_translate_as_whole():
    """报价单表单缺失的 8 个整词键必须整体翻译（整词模式与部分模式均不得再拆坏）。"""
    script = (
        "const m=require('./static/ui-i18n.js');"
        "const out=[];"
        "for(const s of " + str(list(QUOTATION_FORM_LABELS.keys())) + "){"
        "out.push(m.translate(s,false));"
        "out.push(m.translate(s,true));"
        "}"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = []
    for value in QUOTATION_FORM_LABELS.values():
        expected += [value, value]
    assert json.loads(result.stdout) == expected


def test_no_fragment_remains_in_dictionary_for_mangled_labels():
    """防回归：被拆坏的目标原文（含全角＋的"里程＋时长"）必须作为整词键存在。"""
    source = (ROOT / "static" / "ui-i18n.js").read_text(encoding="utf-8")
    for key in QUOTATION_FORM_LABELS:
        assert f'"{key}":' in source, f"缺少整词键 {key}"
