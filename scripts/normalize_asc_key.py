#!/usr/bin/env python3
import base64
import os
import textwrap

HEADER = "-----BEGIN PRIVATE KEY-----"
FOOTER = "-----END PRIVATE KEY-----"

raw = os.environ.get("ASC_API_KEY_P8", "").strip()
if not raw:
    raise SystemExit("ASC_API_KEY_P8 is empty")

# GitHub/iOS copy-paste can preserve wrapping quotes or literal \\n sequences.
if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
    raw = raw[1:-1].strip()

raw = raw.replace("\r\n", "\n").replace("\r", "\n")
raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n")

# Also accept a secret that was pasted as base64 rather than raw PEM text.
if HEADER not in raw:
    try:
        decoded = base64.b64decode("".join(raw.split()), validate=True).decode("utf-8")
    except Exception:
        decoded = ""
    if HEADER in decoded:
        raw = decoded.replace("\r\n", "\n").replace("\r", "\n")

if HEADER not in raw or FOOTER not in raw:
    raise SystemExit(
        "ASC_API_KEY_P8 does not contain an Apple private key PEM. "
        "The secret must contain the contents of the AuthKey_*.p8 file, not its filename."
    )

# Rebuild canonical PEM framing even if the body was pasted onto one line.
start = raw.index(HEADER) + len(HEADER)
end = raw.index(FOOTER, start)
body = "".join(raw[start:end].split())
if not body:
    raise SystemExit("ASC_API_KEY_P8 private key body is empty")

pem = HEADER + "\n" + "\n".join(textwrap.wrap(body, 64)) + "\n" + FOOTER + "\n"
encoded = base64.b64encode(pem.encode("utf-8")).decode("ascii")

output = os.environ.get("GITHUB_ENV")
if not output:
    raise SystemExit("GITHUB_ENV is unavailable")
with open(output, "a", encoding="utf-8") as handle:
    handle.write(f"ASC_API_KEY_P8_BASE64={encoded}\n")

print("App Store Connect private key normalized successfully")
