from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import process_all

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Process clinic messages with the Maplewood front-desk agent.")
    parser.add_argument("--messages", default=str(PROJECT_ROOT / "data" / "messages_sample.json"), help="Path to an array of input message objects")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"), help="Directory containing the six clinic JSON files")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "reports" / "results.json"), help="Output JSON path")
    parser.add_argument("--audit-output", default=str(PROJECT_ROOT / "reports" / "audit_log.json"), help="Tool/action audit JSON path")
    parser.add_argument("--llm", action="store_true", help="Use optional OpenAI semantic fallback for messages rules classify as other")
    parser.add_argument("--model", default=None, help="OpenAI model override; defaults to OPENAI_MODEL or gpt-6-luna")
    parser.add_argument("--mcp-tools", action="store_true", help="Invoke tools through the MCP server over stdio JSON-RPC instead of in-process")
    args = parser.parse_args()

    llm_classifier = None
    if args.llm:
        from .llm_classifier import LLMClassifier
        llm_classifier = LLMClassifier(model=args.model)
    results, tools = process_all(args.data_dir, args.messages, llm_classifier=llm_classifier, use_mcp=args.mcp_tools)
    output = Path(args.output)
    audit_output = Path(args.audit_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    audit_output.write_text(json.dumps(tools.audit_log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Processed {len(results)} messages -> {output}")
    print(f"Tool/policy audit -> {audit_output} ({len(tools.audit_log)} events)")


if __name__ == "__main__":
    main()
