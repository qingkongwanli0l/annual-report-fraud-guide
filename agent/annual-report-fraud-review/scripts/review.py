"""Portable CLI for a single-PDF financial fraud risk review."""
import argparse
import json
import sys
from pathlib import Path

import pdfplumber

from workpaper import ROOT, digest, evaluate, initialize, knowledge, read, validate, write


def extract(pdf_path, output, tables=False):
    pdf_path = Path(pdf_path)
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            row = {"page": number, "text": text, "has_images": bool(page.images),
                   "extraction_status": "text_extracted_needs_review" if text.strip() else "requires_visual_or_ocr",
                   "tables": []}
            if tables:
                for i, table in enumerate(page.find_tables(), 1):
                    row["tables"].append({"id": f"p{number}-t{i}", "bbox": list(table.bbox),
                                          "rows": table.extract(), "status": "candidate_needs_page_check"})
            pages.append(row)
            page.close()
    document = {"schema_version": "1.0", "report": {"filename": pdf_path.name,
                "sha256": digest(pdf_path), "page_count": len(pages)}, "pages": pages}
    write(output, document)
    return {"page_count": len(pages), "requires_visual_or_ocr": sum(
        p["extraction_status"] == "requires_visual_or_ocr" for p in pages), "sha256": document["report"]["sha256"]}


def search(document, queries):
    hits = []
    for page in document["pages"]:
        for query in queries:
            text, offset = page["text"], 0
            while True:
                at = text.casefold().find(query.casefold(), offset)
                if at < 0:
                    break
                hits.append({"page": page["page"], "query": query,
                             "excerpt": text[max(0, at - 160):at + len(query) + 260]})
                offset = at + max(1, len(query))
    return {"hits": hits, "note": "片段用于定位；须回读完整章节、表头、续页及表注。无命中不证明未披露或不适用。"}


def render(pdf_path, numbers, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(pdf_path) as pdf:
        if any(n < 1 or n > len(pdf.pages) for n in numbers):
            raise ValueError("PDF 物理页码超出范围")
        for n in numbers:
            pdf.pages[n - 1].to_image(resolution=150, antialias=True).save(output / f"page-{n:04d}.png")
    return {"rendered_pages": numbers}


def neighbors(node_id, depth):
    graph = read(ROOT / "knowledge/graph.json")
    if node_id not in {n["id"] for n in graph["nodes"]}:
        raise ValueError("概念节点 ID 不存在")
    visited = {node_id}
    for _ in range(depth):
        visited |= {e["target"] for e in graph["edges"] if e["source"] in visited} | {
            e["source"] for e in graph["edges"] if e["target"] in visited}
    return {"nodes": [n for n in graph["nodes"] if n["id"] in visited],
            "edges": [e for e in graph["edges"] if e["source"] in visited and e["target"] in visited],
            "note": "方法概念关系，不是目标企业已证实的交易关系。"}


def checked(path, pdf=None, check_conclusions=True):
    w = read(path)
    result = validate(w, pdf, check_conclusions)
    if result["errors"]:
        raise ValueError("\n".join(result["errors"]))
    return w, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    rule = commands.add_parser("rules", help="完整规则索引或指定规则/专题")
    selection = rule.add_mutually_exclusive_group()
    selection.add_argument("--id")
    selection.add_argument("--topic")
    graph = commands.add_parser("neighbors", help="概念邻接查询")
    graph.add_argument("--id", required=True)
    graph.add_argument("--depth", type=int, choices=(1, 2), default=1)
    ext = commands.add_parser("extract", help="全文和可选表格候选提取")
    ext.add_argument("pdf")
    ext.add_argument("--output", required=True)
    ext.add_argument("--tables", action="store_true")
    srch = commands.add_parser("search", help="按关键词定位，不替代全文阅读")
    srch.add_argument("document")
    srch.add_argument("--query", nargs="+", required=True)
    images = commands.add_parser("render", help="渲染指定 PDF 物理页")
    images.add_argument("pdf")
    images.add_argument("--pages", nargs="+", type=int, required=True)
    images.add_argument("--output", required=True)
    init = commands.add_parser("init", help="初始化全部 101 条规则为未完成")
    init.add_argument("document")
    init.add_argument("--output", required=True)
    for name in ("calculate", "validate", "export"):
        command = commands.add_parser(name)
        command.add_argument("workpaper")
        if name == "validate":
            command.add_argument("--pdf")
        else:
            command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "rules":
            bundle = knowledge()
            if args.id or args.topic:
                selected = [r for r in bundle["rules"] if (r["id"] == args.id if args.id else r["topic_id"] == args.topic)]
                if not selected:
                    raise ValueError("规则或专题 ID 不存在")
                result = {"version": bundle["version"], "scope": bundle["scope"], "rules": selected}
            else:
                result = {"version": bundle["version"], "topics": bundle["topics"], "rules": [
                    {k: r[k] for k in ("id", "topic_id", "method", "applicability")} for r in bundle["rules"]]}
        elif args.command == "neighbors":
            result = neighbors(args.id, args.depth)
        elif args.command == "extract":
            result = extract(args.pdf, args.output, args.tables)
        elif args.command == "search":
            result = search(read(args.document), args.query)
        elif args.command == "render":
            result = render(args.pdf, args.pages, args.output)
        elif args.command == "init":
            if Path(args.output).exists():
                raise ValueError("底稿已存在，请选择新输出路径，避免重置已有分析记录")
            write(args.output, initialize(read(args.document)))
            result = {"initialized_rules": 101, "status": "not_completed"}
        elif args.command == "validate":
            result = validate(read(args.workpaper), args.pdf)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["errors"] else 0
        else:
            w, validation = checked(args.workpaper, check_conclusions=args.command != "calculate")
            calculations = evaluate(w)
            calculations["workpaper_sha256"] = digest(args.workpaper)
            if args.command == "calculate":
                write(args.output, calculations)
                result = {"calculations": len(calculations["calculations"]),
                          "not_calculated": [{"id": c["id"], "reason": c["reason"]}
                                             for c in calculations["calculations"] if c["value"] is None],
                          "warnings": validation["warnings"]}
            else:
                from report import export
                result = export(w, calculations, validation, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
