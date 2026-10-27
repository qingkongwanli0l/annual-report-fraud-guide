import sys
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fixtures import make_report
from report import export
from review import extract, neighbors, render, search
from workpaper import ROOT, coverage_counts, evaluate, initialize, knowledge, read, validate, write


def context(kind="balance", start=None, end="2025-12-31", unit="千元", scale="1000", currency="CNY"):
    return {"entity": "示例集团", "scope": "合并", "basis": "合成报告口径",
            "currency": currency, "unit": unit, "scale": scale, "start": start, "end": end, "kind": kind}


def fixture_workpaper():
    w = initialize({"report": {"filename": "synthetic.pdf", "sha256": "a" * 64, "page_count": 4}})
    w["evidence"] = [{"id": "E1", "page": 2, "locator": "附注1 应收账款",
                      "quote": "应收账款总额 200 240", "table_header": ["项目", "2024", "2025"],
                      "row": "应收账款总额", "column": "2025", "verification": "page_checked"}]
    w["facts"] = [fact("F1", "240", context()), fact("F2", "200", context(end="2024-12-31"))]
    return w


def fact(identifier, value, ctx, concept="receivables"):
    return {"id": identifier, "label": concept, "concept": concept, "value": value,
            "context": ctx, "evidence_ids": ["E1"], "verified": True}


def calculation(op, refs, ctx, period="same", identifier="C1", concept="receivables", multiplier="1"):
    return {"id": identifier, "label": concept, "concept": concept, "op": op,
            "terms": [{"ref": ref, "weight": "1"} for ref in refs], "context": ctx,
            "period_rule": period, "multiplier": multiplier, "denominator": "positive",
            "rationale": "合成资料的已知口径复算"}


class ReviewTests(unittest.TestCase):
    def test_knowledge_complete_and_related_rules_exist(self):
        b = knowledge()
        ids = {r["id"] for r in b["rules"]}
        self.assertEqual(ids, {f"{i:03}" for i in range(1, 102)})
        self.assertEqual(len(b["topics"]), 14)
        self.assertEqual(len(b["metrics"]), 14)
        for r in b["rules"]:
            for key in ("method", "signals", "location", "checks", "caveat", "applicability",
                        "required_evidence", "procedures", "alternative_explanations", "completion_criteria"):
                self.assertTrue(r[key], (r["id"], key))
            self.assertTrue(set(r["related_rules"]) <= ids)
        graph = read(ROOT / "knowledge/graph.json")
        self.assertEqual((len(graph["nodes"]), len(graph["edges"])), (386, 728))
        nodes = {n["id"] for n in graph["nodes"]}
        self.assertTrue(all(e["source"] in nodes and e["target"] in nodes for e in graph["edges"]))
        self.assertTrue(neighbors("n001", 1)["edges"])

    def test_initialization_does_not_claim_analysis(self):
        w = fixture_workpaper()
        result = validate(w)
        self.assertEqual(result["errors"], [])
        self.assertEqual(coverage_counts(w)["not_completed"], 101)
        self.assertEqual({p["status"] for p in w["pages"]}, {"not_reviewed"})
        self.assertTrue(result["warnings"])

    def test_scale_and_comparable_growth(self):
        w = fixture_workpaper()
        w["facts"][0]["value"] = "24"
        w["facts"][0]["context"].update(unit="万元", scale="10000")
        w["calculations"] = [calculation("growth", ["F1", "F2"],
            context("ratio", unit="%", scale="0.01", currency=None), "comparison")]
        self.assertEqual(evaluate(w)["calculations"][0]["value"], "20")
        self.assertEqual(validate(w)["errors"], [])

    def test_missing_unverified_zero_and_negative_growth(self):
        for value, verified in ((None, True), ("0", True), ("-200", True), ("200", False)):
            w = fixture_workpaper()
            w["facts"][1].update(value=value, verified=verified)
            w["calculations"] = [calculation("growth", ["F1", "F2"],
                context("ratio", unit="%", scale="0.01", currency=None), "comparison")]
            self.assertIsNone(evaluate(w)["calculations"][0]["value"])

    def test_scope_currency_and_period_mismatch(self):
        for key, value in (("scope", "母公司"), ("currency", "USD"), ("end", "2024-06-30")):
            w = fixture_workpaper()
            w["facts"][1]["context"] = context()
            w["facts"][1]["context"][key] = value
            w["calculations"] = [calculation("difference", ["F1", "F2"], context())]
            self.assertIsNone(evaluate(w)["calculations"][0]["value"])

    def test_average_requires_actual_opening_and_closing(self):
        w = fixture_workpaper()
        w["calculations"] = [calculation("average_balance", ["F1", "F2"], context(start="2025-01-01"), "balance_flow")]
        self.assertEqual(evaluate(w)["calculations"][0]["value"], "220")
        w["facts"][1]["context"]["end"] = "2025-12-31"
        self.assertIsNone(evaluate(w)["calculations"][0]["value"])

    def test_days_use_actual_period_length(self):
        w = fixture_workpaper()
        w["facts"][1] = fact("F2", "1200", context("flow", "2025-01-01"), "revenue")
        w["calculations"] = [calculation("ratio", ["F1", "F2"],
            context("days", "2025-01-01", unit="天", scale="1", currency=None), "balance_flow", multiplier="365")]
        self.assertEqual(evaluate(w)["calculations"][0]["value"], "73.0")
        w["calculations"][0]["multiplier"] = "360"
        self.assertIsNone(evaluate(w)["calculations"][0]["value"])

    def test_rollforward_and_reconciliation(self):
        w = fixture_workpaper()
        w["facts"] = [fact("F1", "100", context(end="2024-12-31"), "cash"),
                      fact("F2", "60", context("flow", "2025-01-01"), "cash_change"),
                      fact("F3", "160", context(), "cash")]
        w["calculations"] = [calculation("sum", ["F1", "F2"], context(start="2025-01-01"), "rollforward", concept="cash")]
        w["reconciliations"] = [{"id": "R1", "actual": "F3", "expected": "C1", "tolerance": "0", "basis": "合成数据精确值"}]
        result = evaluate(w)
        self.assertEqual(result["calculations"][0]["value"], "160")
        self.assertEqual(result["reconciliations"][0]["residual"], "0")

    def test_percentage_products_and_unit_rates(self):
        w = fixture_workpaper()
        w["facts"][1] = fact("F2", "5", context("ratio", unit="%", scale="0.01", currency=None), "rate")
        w["calculations"] = [calculation("product", ["F1", "F2"], context())]
        self.assertEqual(evaluate(w)["calculations"][0]["value"], "12.00")
        w["facts"][1] = fact("F2", "10", context("quantity", unit="店", scale="1", currency=None), "stores")
        w["calculations"] = [calculation("ratio", ["F1", "F2"], context("unit_rate", unit="千元/店"))]
        self.assertEqual(evaluate(w)["calculations"][0]["value"], "24")

    def test_invalid_ids_pages_versions_and_false_completion(self):
        for mutation in (
            lambda w: w["coverage"].pop(),
            lambda w: w["evidence"][0].update(page=5),
            lambda w: w["facts"][0].update(evidence_ids=["missing"]),
            lambda w: w["knowledge"].update(version="9.9.9"),
            lambda w: w["coverage"][0].update(status="no_exception", reason="没有找到"),
            lambda w: w.update(summary="收入 {{missing}}"),
        ):
            w = fixture_workpaper()
            mutation(w)
            self.assertTrue(validate(w)["errors"])

    def test_historical_rate_sensitivity_and_future_rate_rejection(self):
        w = fixture_workpaper()
        w["facts"][1] = fact("F2", "5", context("ratio", end="2024-12-31", unit="%", scale="0.01", currency=None), "rate")
        w["calculations"] = [calculation("product", ["F1", "F2"], context(), "sensitivity")]
        self.assertEqual(validate(w)["errors"], [])
        self.assertEqual(evaluate(w)["calculations"][0]["value"], "12.00")
        with tempfile.TemporaryDirectory() as directory:
            export(w, evaluate(w), validate(w), directory)
            self.assertIn("不代表应确认的会计金额", (Path(directory) / "report.md").read_text(encoding="utf-8"))
        w["facts"][1]["context"]["end"] = "2026-12-31"
        self.assertIsNone(evaluate(w)["calculations"][0]["value"])

    def test_calculate_outputs_diagnostics_before_conclusion_correction(self):
        w = fixture_workpaper()
        w["calculations"] = [calculation("difference", ["F1", "F2"], context())]
        w["coverage"][0].update(status="no_exception", reason="错误引用未对齐期间的复算",
                                procedures=["检查差额"], evidence_ids=["E1"], calculation_ids=["C1"])
        self.assertTrue(validate(w)["errors"])
        self.assertEqual(validate(w, check_conclusions=False)["errors"], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "workpaper.json", w)
            command = [sys.executable, str(ROOT / "scripts/review.py")]
            result = subprocess.run(command + ["calculate", str(root / "workpaper.json"), "--output", str(root / "calculations.json")],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("期间或时点不同", read(root / "calculations.json")["calculations"][0]["reason"])
            result = subprocess.run(command + ["export", str(root / "workpaper.json"), "--output", str(root / "report")],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "report/report.docx").exists())

    def test_pdf_tables_reversed_columns_scan_and_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = make_report(root / "report.pdf", "a")
            result = extract(pdf, root / "document.json", True)
            document = read(root / "document.json")
            self.assertEqual(result["page_count"], 4)
            self.assertEqual(result["requires_visual_or_ocr"], 1)
            rows = document["pages"][1]["tables"][0]["rows"]
            self.assertIn("2024", rows[0][1])
            self.assertIn("2025", rows[0][2])
            self.assertEqual(rows[1][1:], ["200", "240"])
            self.assertIn("(60)", document["pages"][2]["text"])
            self.assertTrue(search(document, ["应收账款"])["hits"])
            render(pdf, [2, 4], root / "pages")
            self.assertTrue((root / "pages/page-0004.png").is_file())
            w = initialize(document)
            self.assertEqual(validate(w, pdf)["errors"], [])
            w["report"]["page_count"] = 3
            w["pages"].pop()
            self.assertTrue(any("实际物理页数" in e for e in validate(w, pdf)["errors"]))
            w = initialize(document)
            pdf.write_bytes(pdf.read_bytes() + b"\n")
            self.assertTrue(validate(w, pdf)["errors"])

    def test_normal_and_warning_cases_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = []
            for case in ("a", "b"):
                pdf = make_report(root / f"{case}.pdf", case)
                extract(pdf, root / f"{case}.json", True)
                values.append(read(root / f"{case}.json")["pages"][1]["tables"][0]["rows"])
            self.assertEqual(values[0][1][2], "240")
            self.assertEqual(values[1][1][2], "400")
            self.assertEqual(values[0][2][2], "12")
            self.assertEqual(values[1][2][2], "5")

    def test_report_consistent_references_and_explicit_gaps(self):
        w = fixture_workpaper()
        w["summary"] = "期末应收 {{F1}}，尚未完成全部排查。"
        w["evidence"][0]["quote"] += " 原资料字面文本 {{untrusted}}"
        self.assertEqual(validate(w)["errors"], [])
        with tempfile.TemporaryDirectory() as directory:
            result = export(w, evaluate(w), validate(w), directory)
            root = Path(directory)
            text = (root / "report.md").read_text(encoding="utf-8")
            self.assertIn("240千元〔F1〕", text)
            self.assertIn("{{untrusted}}", text)
            self.assertIn("部分完成", text)
            self.assertEqual(result["coverage"]["not_completed"], 101)
            self.assertEqual(read(root / "workpaper.json"), w)
            with zipfile.ZipFile(root / "report.docx") as doc:
                xml = doc.read("word/document.xml").decode()
                self.assertIn("240千元〔F1〕", xml)
                self.assertIn("101", xml)
                self.assertNotIn("python-docx", doc.read("docProps/core.xml").decode())
                properties = ET.fromstring(doc.read("docProps/app.xml"))
                self.assertFalse(any(node.tag.rsplit("}", 1)[-1] in ("Application", "AppVersion") for node in properties))


if __name__ == "__main__":
    unittest.main()
