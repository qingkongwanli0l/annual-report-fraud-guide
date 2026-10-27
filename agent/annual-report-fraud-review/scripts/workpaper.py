"""Single-report evidence references and decimal financial calculations."""
import hashlib
import json
import platform
import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
STATUS = {
    "finding": "已检查且发现疑点",
    "no_exception": "已检查但本范围未见异常",
    "not_applicable": "有依据的不适用",
    "insufficient_evidence": "证据不足",
    "not_completed": "未完成",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def content_digest(data):
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def knowledge():
    return read(ROOT / "knowledge/rules.json")


def knowledge_identity():
    return {"version": knowledge()["version"],
            "rules_sha256": content_digest(knowledge()),
            "graph_sha256": content_digest(read(ROOT / "knowledge/graph.json"))}


def initialize(document):
    report = dict(document["report"])
    report.update(dict.fromkeys(["company", "year", "scope", "currency", "accounting_basis"]))
    return {
        "schema_version": "1.0", "knowledge": knowledge_identity(), "report": report,
        "pages": [{"page": i, "status": "not_reviewed", "notes": ""}
                  for i in range(1, report["page_count"] + 1)],
        "sections": [], "evidence": [], "facts": [], "calculations": [], "reconciliations": [],
        "coverage": [{"rule_id": r["id"], "status": "not_completed", "reason": "",
                      "procedures": [], "evidence_ids": [], "calculation_ids": [],
                      "finding_ids": [], "limitations": []} for r in knowledge()["rules"]],
        "findings": [], "summary": "", "limitations": [],
    }


def dimension(ctx):
    return ctx["currency"], ctx["kind"]


def context_error(calc, inputs):
    op, out = calc["op"], calc["context"]
    contexts = [x["context"] for x in inputs]
    if not contexts or (op != "sum" and len(inputs) != 2):
        return "输入数量不符合运算要求"
    if any(tuple(c[k] for k in ("entity", "scope", "basis")) !=
           tuple(out[k] for k in ("entity", "scope", "basis")) for c in contexts):
        return "主体、合并范围或会计基础不同，先建立有证据的口径桥"
    if op != "sum" and any(Decimal(t["weight"]) != 1 for t in calc["terms"]):
        return "权重只用于 sum"
    if op in ("sum", "difference", "average_balance"):
        bridge = op == "sum" and calc["period_rule"] == "rollforward" and out["kind"] == "balance"
        if any(c["currency"] != out["currency"] or
               (c["kind"] not in ("balance", "flow") if bridge else c["kind"] != out["kind"]) for c in contexts):
            return "加减或平均运算的币种和度量不同"
        if out["kind"] == "quantity" and any(c["unit"] != out["unit"] for c in contexts):
            return "实物量单位不同"
    if op in ("ratio", "growth"):
        unit_rate = op == "ratio" and contexts[0]["kind"] in ("balance", "flow") and contexts[1]["kind"] == "quantity"
        if unit_rate and (out["kind"] != "unit_rate" or out["currency"] != contexts[0]["currency"]):
            return "金额除数量应输出同币种的单位数量金额"
        if not unit_rate and (out["kind"] not in ("ratio", "days") or out["currency"] is not None):
            return "除法结果应为无币种的比率或天数"
        if not unit_rate and contexts[0]["currency"] != contexts[1]["currency"]:
            return "分子分母币种不同"
        monetary = all(c["kind"] in ("balance", "flow") for c in contexts)
        same_measure = dimension(contexts[0]) == dimension(contexts[1])
        if not unit_rate and not monetary and not same_measure:
            return "该比率不支持不同度量；先明确分子分母定义"
        if contexts[0]["kind"] == "quantity" and contexts[0]["unit"] != contexts[1]["unit"]:
            return "数量比率单位不同"
    if op == "product":
        quantities = [c for c in contexts if c["kind"] != "ratio"]
        if len(quantities) > 1 or (quantities and dimension(quantities[0]) != dimension(out)):
            return "乘法限同口径量乘以无量纲比例"
        if not quantities and out["kind"] != "ratio":
            return "两个比例相乘仍须输出比例"
    start, end = out["start"], out["end"]
    if out["kind"] == "days" and (not start or Decimal(calc["multiplier"]) !=
            Decimal((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)):
        return "周转天数乘数必须等于本期间实际天数"
    if op == "average_balance":
        if not start or any(c["kind"] != "balance" or c["start"] is not None for c in contexts):
            return "平均余额需要真实期初期末余额和明确期间"
        bounds = sorted(c["end"] for c in contexts)
        day_before = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        if bounds[0] not in (day_before, start) or bounds[1] != end or bounds[0] >= bounds[1]:
            return "平均余额输入不是本期间期初期末"
        if inputs[0]["concept"] != inputs[1]["concept"]:
            return "平均余额输入科目不同"
        return ""
    rule = calc["period_rule"]
    if rule == "sensitivity":
        amounts = [c for c in contexts if c["kind"] != "ratio"]
        rates = [c for c in contexts if c["kind"] == "ratio"]
        if op != "product" or len(amounts) != 1 or len(rates) != 1:
            return "敏感性测算限当前金额或数量乘以已披露比例"
        if (amounts[0]["start"], amounts[0]["end"]) != (start, end) or rates[0]["end"] > end:
            return "敏感性基数须为本期，比例不能来自未来期间"
    if rule == "same" and any((c["start"], c["end"]) != (start, end) for c in contexts):
        return "期间或时点不同"
    if rule == "comparison":
        if len(contexts) != 2 or contexts[0]["end"] <= contexts[1]["end"]:
            return "比较需按本期、前期输入"
        if (start, end) != (contexts[0]["start"], contexts[0]["end"]):
            return "比较结果期间须为本期"
        a, b = contexts
        if dimension(a) != dimension(b) or bool(a["start"]) != bool(b["start"]):
            return "比较期度量或时点/期间不同"
        if a["start"]:
            lengths = [(date.fromisoformat(c["end"]) - date.fromisoformat(c["start"])).days for c in contexts]
            if abs(lengths[0] - lengths[1]) > 1:
                return "比较期间长度不同，需另作有依据的可比分析"
    if rule in ("rollforward", "balance_flow"):
        if not start:
            return "滚动或余额与流量比较需要起始日期"
        beginning = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        for c in contexts:
            if c["kind"] == "balance" and c["start"] is None:
                allowed = (beginning, start, end) if rule == "rollforward" else (end,)
                if c["end"] not in allowed:
                    return "余额时点不在本分析期间"
            elif (c["start"], c["end"]) != (start, end):
                return "期间流量不在本分析期间"
    if op == "growth" and (rule != "comparison" or inputs[0]["concept"] != inputs[1]["concept"]):
        return "增长率需要同科目可比期间"
    return ""


def evaluate(w):
    records = {f["id"]: f for f in w["facts"]}
    values = {f["id"]: Decimal(f["value"]) * Decimal(f["context"]["scale"])
              if f["value"] is not None and f["verified"] else None for f in w["facts"]}
    results = []
    for c in w["calculations"]:
        missing = [t["ref"] for t in c["terms"] if t["ref"] not in records]
        inputs = [records[t["ref"]] for t in c["terms"] if t["ref"] in records]
        reason = "引用不存在或后置的计算: " + ", ".join(missing) if missing else context_error(c, inputs)
        v = [values.get(t["ref"]) for t in c["terms"]]
        if not reason and None in v:
            reason = "输入缺失、未核实或前序计算不可用，不以零替代"
        value = None
        if not reason:
            op = c["op"]
            if op in ("ratio", "growth") and (v[1] == 0 or (c["denominator"] == "positive" and v[1] < 0)):
                reason = "分母为零或不满足正分母条件"
            elif op == "growth" and (v[1] <= 0 or v[0] < 0):
                reason = "亏损或非正基数不作常规增长率，使用绝对变动说明"
            elif op == "sum":
                value = sum((x * Decimal(t["weight"]) for x, t in zip(v, c["terms"])), Decimal(0))
            elif op == "difference":
                value = v[0] - v[1]
            elif op == "average_balance":
                value = (v[0] + v[1]) / 2
            elif op == "product":
                value = v[0] * v[1]
            elif op == "ratio":
                value = v[0] / v[1]
            elif op == "growth":
                value = (v[0] - v[1]) / v[1]
            if value is not None:
                value *= Decimal(c["multiplier"])
        values[c["id"]] = value
        records[c["id"]] = c
        results.append({"id": c["id"], "label": c["label"], "op": c["op"], "terms": c["terms"],
                        "multiplier": c["multiplier"], "context": c["context"],
                        "period_rule": c["period_rule"], "rationale": c["rationale"],
                        "value": format(value / Decimal(c["context"]["scale"]), "f") if value is not None else None,
                        "normalized_value": format(value, "f") if value is not None else None,
                        "status": "calculated" if value is not None else "not_calculated", "reason": reason})
    reconciliations = []
    for r in w["reconciliations"]:
        a, b = records.get(r["actual"]), records.get(r["expected"])
        compatible = a is not None and b is not None and all(
            a["context"][k] == b["context"][k] for k in ("entity", "scope", "basis", "currency", "kind", "end"))
        if compatible and a["context"]["kind"] != "balance":
            compatible = a["context"]["start"] == b["context"]["start"]
        av, bv = values.get(r["actual"]), values.get(r["expected"])
        residual = (av - bv) / Decimal(a["context"]["scale"]) if compatible and av is not None and bv is not None else None
        status = "not_calculated" if residual is None else "within_declared_tolerance" if abs(residual) <= Decimal(r["tolerance"]) else "unexplained_difference"
        reconciliations.append({**r, "residual": format(residual, "f") if residual is not None else None,
                                "unit": a["context"]["unit"] if a else None, "status": status,
                                "reason": "口径不同或输入不可用" if residual is None else ""})
    return {"schema_version": "1.0", "engine_version": "1.0.0", "knowledge": w["knowledge"],
            "runtime": {"python": platform.python_version(), **{p: version(p) for p in ("pdfplumber", "python-docx", "jsonschema")}},
            "report_sha256": w["report"]["sha256"],
            "calculations": results, "reconciliations": reconciliations}


def coverage_counts(w):
    counts = Counter(x["status"] for x in w["coverage"])
    return {s: counts[s] for s in STATUS}


def validate(w, pdf=None, check_conclusions=True):
    schema = read(ROOT / "schemas/workpaper.schema.json")
    errors = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
              for e in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(w)]
    warnings = []
    if errors:
        return {"errors": errors, "warnings": warnings}
    if w["knowledge"] != knowledge_identity():
        errors.append("知识版本或 SHA256 与本包不一致，须明确迁移及重新核查")
    if pdf and digest(pdf) != w["report"]["sha256"]:
        errors.append("输入 PDF 的 SHA256 与底稿不一致")
    if pdf:
        import pdfplumber
        with pdfplumber.open(pdf) as source:
            if len(source.pages) != w["report"]["page_count"]:
                errors.append("底稿声明页数与源 PDF 实际物理页数不一致")
    expected_pages = set(range(1, w["report"]["page_count"] + 1))
    actual_pages = [p["page"] for p in w["pages"]]
    if set(actual_pages) != expected_pages or len(actual_pages) != len(expected_pages):
        errors.append("逐页登记须完整且不得重复")
    groups = ("evidence", "facts", "calculations", "reconciliations", "findings")
    all_ids = [r["id"] for g in groups for r in w[g]]
    if len(set(all_ids)) != len(all_ids):
        errors.append("证据、事实、计算、勾稽和发现的 ID 必须全局唯一")
    sets = {g: {r["id"] for r in w[g]} for g in groups}
    evidence = {e["id"]: e for e in w["evidence"]}
    rules = {r["id"] for r in knowledge()["rules"]}
    covered = [x["rule_id"] for x in w["coverage"]]
    if set(covered) != rules or len(covered) != len(rules):
        errors.append("coverage 必须完整列出 101 条规则且不重复")

    def refs(ids, valid, label):
        if set(ids) - valid:
            errors.append(f"{label} 的引用不存在: {sorted(set(ids) - valid)}")

    for section in w["sections"]:
        if not section["pages"] or not set(section["pages"]).issubset(expected_pages):
            errors.append(f"章节 {section['title']} 的页码无效")
    for e in w["evidence"]:
        if e["page"] not in expected_pages:
            errors.append(f"证据 {e['id']} 页码越界")
    for row in w["facts"] + w["calculations"]:
        ctx = row["context"]
        if Decimal(ctx["scale"]) <= 0 or (ctx["start"] and ctx["start"] > ctx["end"]):
            errors.append(f"{row['id']} 的单位倍数或日期无效")
        if ctx["kind"] in ("balance", "flow") and not ctx["currency"]:
            errors.append(f"{row['id']} 的金额缺少币种")
        if ctx["kind"] in ("ratio", "days", "quantity") and ctx["currency"] is not None:
            errors.append(f"{row['id']} 的比例、天数或实物量不应带币种")
        if ctx["kind"] == "ratio" and ctx["unit"] == "%" and Decimal(ctx["scale"]) != Decimal("0.01"):
            errors.append(f"{row['id']} 的百分数单位倍数须为 0.01")
    for f in w["facts"]:
        refs(f["evidence_ids"], sets["evidence"], f["id"])
        if not f["evidence_ids"]:
            errors.append(f"事实 {f['id']} 缺少 PDF 证据")
        if f["verified"] and any(evidence.get(i, {}).get("verification") != "page_checked" for i in f["evidence_ids"]):
            errors.append(f"已核实事实 {f['id']} 的证据尚未原页核对")
    available = set(sets["facts"])
    for c in w["calculations"]:
        refs([t["ref"] for t in c["terms"]], available, c["id"])
        available.add(c["id"])
    for r in w["reconciliations"]:
        refs([r["actual"], r["expected"]], available, r["id"])
        if Decimal(r["tolerance"]) < 0:
            errors.append(f"{r['id']} 容差不可为负")
    for row in w["coverage"]:
        refs(row["evidence_ids"], sets["evidence"], row["rule_id"])
        refs(row["calculation_ids"], sets["calculations"], row["rule_id"])
        refs(row["finding_ids"], sets["findings"], row["rule_id"])
        if row["status"] != "not_completed" and not row["reason"].strip():
            errors.append(f"规则 {row['rule_id']} 缺少处理理由")
        if row["status"] in ("finding", "no_exception", "not_applicable") and (not row["evidence_ids"] or not row["procedures"]):
            errors.append(f"规则 {row['rule_id']} 关闭前需提供实际程序和证据")
        if row["status"] == "finding" and not row["finding_ids"]:
            errors.append(f"规则 {row['rule_id']} 未引用对应发现")
        if row["status"] in ("finding", "no_exception", "not_applicable") and any(
                evidence.get(i, {}).get("verification") == "unverified" for i in row["evidence_ids"]):
            errors.append(f"规则 {row['rule_id']} 引用的证据尚未核实")
        findings_by_id = {f["id"]: f for f in w["findings"]}
        if any(row["rule_id"] not in findings_by_id.get(i, {}).get("rule_ids", []) for i in row["finding_ids"]):
            errors.append(f"规则 {row['rule_id']} 与所引用发现的规则不一致")
    for f in w["findings"]:
        refs(f["rule_ids"], rules, f["id"])
        refs(f["evidence_ids"] + f["counter_evidence_ids"] + f["review"]["evidence_ids"], sets["evidence"], f["id"])
        refs(f["calculation_ids"], sets["calculations"], f["id"])
        if not f["evidence_ids"] or not f["alternative_explanations"]:
            errors.append(f"发现 {f['id']} 缺少支持证据或替代解释检验")
        if f["review"]["status"] == "verified" and (not f["review"]["evidence_ids"] or not f["review"]["notes"].strip()):
            errors.append(f"发现 {f['id']} 原页复核缺记录")
        if f["review"]["status"] == "verified" and any(evidence.get(i, {}).get("verification") != "page_checked" for i in f["review"]["evidence_ids"]):
            errors.append(f"发现 {f['id']} 的复核证据尚未原页核对")
    if errors:
        return {"errors": errors, "warnings": warnings}
    results = evaluate(w)
    unusable = {f["id"] for f in w["facts"] if not f["verified"] or f["value"] is None}
    unusable.update(c["id"] for c in results["calculations"] if c["value"] is None)
    for row in w["coverage"]:
        if row["status"] in ("finding", "no_exception", "not_applicable") and set(row["calculation_ids"]) & unusable:
            failed = [c for c in results["calculations"] if c["id"] in row["calculation_ids"] and c["value"] is None]
            message = f"规则 {row['rule_id']} 引用的计算不可用: " + "；".join(f"{c['id']} {c['reason']}" for c in failed)
            (errors if check_conclusions else warnings).append(message)
    narrative = [w["summary"], w["limitations"], w["coverage"], w["findings"]]
    placeholders = set(re.findall(r"\{\{([^{}]+)\}\}", json.dumps(narrative, ensure_ascii=False)))
    refs(placeholders, available, "正文数值")
    if placeholders & unusable:
        warnings.append("正文引用了未核实或不可计算值，导出将标为未确定: " + ", ".join(sorted(placeholders & unusable)))
    pending = sum(p["status"] != "reviewed" for p in w["pages"])
    if pending:
        warnings.append(f"{pending} 页未完成阅读或不可读")
    if not w["sections"]:
        warnings.append("尚未建立实际章节覆盖")
    if any(x["status"] != "reviewed" for x in w["sections"]):
        warnings.append("存在未完成的章节阅读")
    counts = coverage_counts(w)
    if counts["not_completed"]:
        warnings.append(f"{counts['not_completed']} 条规则未完成")
    if counts["insufficient_evidence"]:
        warnings.append(f"{counts['insufficient_evidence']} 条规则证据不足")
    if any(f["review"]["status"] != "verified" for f in w["findings"]):
        warnings.append("存在尚未完成原页复核的发现")
    if any(c["status"] != "calculated" for c in results["calculations"]):
        warnings.append("部分计算未完成，具体原因见计算结果")
    if any(e["verification"] == "unverified" for e in w["evidence"]):
        warnings.append("存在未核实证据")
    if not w["summary"].strip() or any(w["report"][k] is None for k in ("company", "year", "scope", "accounting_basis")):
        warnings.append("摘要或报告基本口径尚未填写完整")
    return {"errors": errors, "warnings": warnings, "coverage": counts}
