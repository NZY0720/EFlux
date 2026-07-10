#!/usr/bin/env python3
"""Generate TypeScript schema declarations from the checked-in OpenAPI document.

The generator intentionally uses only Python's standard library. It is a small,
deterministic fallback for environments where installing `openapi-typescript` is
not possible; it emits the same useful `components["schemas"]` surface without
introducing a frontend runtime dependency.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def property_name(name: str) -> str:
    return name if IDENTIFIER.match(name) else json.dumps(name)


def schema_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return f'components["schemas"][{json.dumps(schema["$ref"].rsplit("/", 1)[-1])}]'
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    for combinator, operator in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        if combinator in schema:
            return operator.join(schema_type(option) for option in schema[combinator]) or "unknown"
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(schema_type({**schema, "type": item}) for item in kind)
    if kind == "array":
        return f"Array<{schema_type(schema.get('items', {}))}>"
    if kind == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = [
            f"{property_name(name)}{' ' if name in required else '?'}: {schema_type(value)};"
            for name, value in properties.items()
        ]
        additional = schema.get("additionalProperties")
        if additional is True:
            fields.append("[key: string]: unknown;")
        elif isinstance(additional, dict):
            fields.append(f"[key: string]: {schema_type(additional)};")
        return "{ " + " ".join(fields) + " }" if fields else "Record<string, unknown>"
    return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null"}.get(kind, "unknown")


def render(spec: dict[str, Any]) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    lines = [
        "/* eslint-disable */",
        "/**",
        " * Generated from docs/openapi.json by scripts/generate_openapi_ts.py.",
        " * Do not edit by hand; run `pnpm -C frontend generate:api`.",
        " */",
        "",
        "export interface components {",
        "  schemas: {",
    ]
    for name in sorted(schemas):
        lines.append(f"    {property_name(name)}: {schema_type(schemas[name])};")
    lines.extend(["  };", "}", "", "export type schemas = components[\"schemas\"];", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "docs/openapi.json")
    parser.add_argument("--output", type=Path, default=ROOT / "frontend/src/api/schema.gen.ts")
    args = parser.parse_args()
    spec = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(spec), encoding="utf-8")


if __name__ == "__main__":
    main()
