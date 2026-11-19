#!/usr/bin/env python3
"""Create a review-only BB source snapshot with likely credentials redacted.

Runs ONLY on local files, no network, no application imports, no service control.
Produces a small ZIP for sharing privately in chat -- NEVER publish on GitHub.
Inspect the ZIP's contents before sharing: no automatic redactor is perfect.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
import re
import tokenize
import zipfile

APP = Path("/home/pi/WA/Main_program/Fis")
DEST = Path.home() / "BB_source_review_redacted.zip"
NAMES = ("Panel.py", "Panel_backend_merged.py", "backend_client.py", "Ads.py")
SENSITIVE = re.compile(
    r"(?ix)(?:"
    r"pass(?:word|wd|phrase)?|secret|cred(?:ential)?s?|"
    r"token|api[_-]?key|panel[_-]?key|private[_-]?key|"
    r"access[_-]?key|auth(?:orization)?[_-]?(?:header|key)|"
    r"merchant[_-]?key|smtp[_-]?(?:user|login|pass)|"
    r"username|user[_-]?name|email[_-]?(?:user|pass)|"
    r"bearer|pin[_-]?code)"
)
# Other string literals may contain sensitive information, such as email addresses.
EMAIL = re.compile(r"(?i)(?<![\w.])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
URL_PASSWORD = re.compile(r"(?i)\bhttps?://[^/\s:'\"]+:[^@/\s]+@")
TOKEN_PATTERN = re.compile(
    r"(?i)^(?:sk-[a-z0-9_-]{15,}|gh[pousr]_[a-z0-9]{20,}|"
    r"Bearer[ \t]+[a-z0-9._-]{12,}|[a-z0-9_-]{20,}\.[a-z0-9_-]{20,}\.[a-z0-9_-]{20,})$"
)

def is_sensitive(name):
    return bool(SENSITIVE.search(str(name)))

def target_names(node):
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [n for e in node.elts for n in target_names(e)]
    if isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value,str):
            return [node.slice.value]
    return []

def constants(node):
    return [v for v in ast.walk(node)
            if isinstance(v, ast.Constant) and isinstance(v.value, (str, bytes, int, float))
            and not isinstance(v.value,bool)]

def redacted_source(content: str):
    tree=ast.parse(content)
    spans=set()
    counts={"named_assignments":0,"dictionary_entries":0,"login_calls":0,"sensitive_literals":0}
    selected=set()
    def mark(expr, label):
        local=0
        for v in constants(expr):
            selected.add((v.lineno, v.col_offset, v.end_lineno, v.end_col_offset))
            local+=1
        if local:counts[label]+=local

    for node in ast.walk(tree):
        if isinstance(node,ast.Assign):
            if any(is_sensitive(n) for t in node.targets for n in target_names(t)):
                mark(node.value, "named_assignments")
        elif isinstance(node,ast.AnnAssign):
            if any(is_sensitive(n) for n in target_names(node.target)) and node.value:
                mark(node.value,"named_assignments")
        elif isinstance(node,ast.NamedExpr):
            if any(is_sensitive(n) for n in target_names(node.target)):
                mark(node.value,"named_assignments")
        elif isinstance(node,ast.Dict):
            for k,v in zip(node.keys,node.values):
                if isinstance(k,ast.Constant) and isinstance(k.value,str) and is_sensitive(k.value):
                    mark(v,"dictionary_entries")
        elif isinstance(node,ast.Call):
            fn = node.func
            method = fn.attr.lower() if isinstance(fn,ast.Attribute) else (
                fn.id.lower() if isinstance(fn,ast.Name) else "")
            if method in ("login","authenticate","set_password","httpbasicauth","smtpauth"):
                for arg in node.args[:3]:
                    mark(arg,"login_calls")
            if method in ("getenv","get") and len(node.args)>=2:
                key=node.args[0]
                if isinstance(key,ast.Constant) and isinstance(key.value,str) and is_sensitive(key.value):
                    mark(node.args[1],"named_assignments")
            for kw in node.keywords:
                if kw.arg and is_sensitive(kw.arg):
                    mark(kw.value,"named_assignments")

    lines=content.splitlines(keepends=True)
    new_lines=[]
    for tok in tokenize.generate_tokens(io.StringIO(content).readline):
        if tok.type != tokenize.STRING:
            continue
        exact_span=(tok.start[0],tok.start[1],tok.end[0],tok.end[1])
        # Preserves source structure, minimizes changes to logic.
        if exact_span in selected:
            spans.add(exact_span)
            continue
        try:
            val=ast.literal_eval(tok.string)
        except (ValueError,TypeError,SyntaxError):
            continue
        if isinstance(val,str) and (EMAIL.search(val) or URL_PASSWORD.search(val) or TOKEN_PATTERN.fullmatch(val.strip())):
            spans.add(exact_span)
            counts["sensitive_literals"]+=1

    # Redact source at tokenize.STRING boundaries; literal f-strings are left unchanged.
    tokens=[]
    for t in tokenize.generate_tokens(io.StringIO(content).readline):
        if t.type==tokenize.STRING and (t.start[0],t.start[1],t.end[0],t.end[1]) in spans:
            value=ast.literal_eval(t.string)
            replacement = "b'REDACTED'" if isinstance(value,bytes) else (
                "0" if isinstance(value,(int,float)) else "'REDACTED'")
            tokens.append(tokenize.TokenInfo(t.type,replacement,t.start,t.end,t.line))
        else:
            tokens.append(t)
    output=tokenize.untokenize(tokens)
    ast.parse(output)
    compile(output,"<redacted>","exec")
    return output,counts

def main():
    if not APP.is_dir():
        raise RuntimeError("BB application folder not found")
    report={"files":[],"notice":"Review contents before sharing. Never publish the ZIP publicly."}
    with zipfile.ZipFile(DEST,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in NAMES:
            src=APP/name
            if not src.is_file():
                raise RuntimeError("Missing BB application file "+name)
            content=src.read_text(encoding="utf-8")
            out,counts=redacted_source(content)
            z.writestr("BB_source_redacted/"+name, out)
            report["files"].append({
                "name":name,
                "original_sha256":hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "original_line_count":len(content.splitlines()),
                "redaction_counts":counts,
            })
        z.writestr("BB_source_redacted/report.json",json.dumps(report,indent=2))
        z.writestr("BB_source_redacted/README.txt",
            "BB source review, redacted by best-effort static checks.\n"
            "No production code or service was changed.\n"
            "CAUTION: review ALL contents before sharing; secrets can escape automated detection.\n"
            "Do not put this archive in public GitHub.\n"
        )
    print("Review-only archive created:", DEST)
    print("Size:",round(DEST.stat().st_size/1024,1),"KiB")
    print("Files: "+", ".join(NAMES))
    for item in report["files"]:
        print(item["name"],"SHA256",item["original_sha256"],
              "redacted tokens:",sum(item["redaction_counts"].values()))
    print("IMPORTANT: Inspect the ZIP for sensitive strings before attaching privately in chat.")
    print("No production application files or services were changed.")

if __name__=="__main__":
    main()
