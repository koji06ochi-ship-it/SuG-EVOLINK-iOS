# Temporary EVOLINK-only signing: state file MUST be in RUNNER_TEMP; cleanup only IDs created in this same run.
#!/usr/bin/env python3
import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import jwt
import requests

API_BASE = "https://api.appstoreconnect.apple.com/v1"
TRANSIENT_STATUS = {429, 500, 502, 503, 504}

KEY_ID = os.environ["ASC_API_KEY_ID"]
ISSUER_ID = os.environ["ASC_API_ISSUER_ID"]
P8_BASE64 = os.environ["ASC_API_KEY_P8_BASE64"]
BUNDLE_ID = os.environ.get("APP_BUNDLE_ID", "jp.sugosaka.evolink")


def make_token() -> str:
    private_key = base64.b64decode(P8_BASE64).decode("utf-8")
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER_ID, "iat": now - 5, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"},
    )


TOKEN = make_token()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    allow: Tuple[int, ...] = (200, 201),
    attempts: int = 5,
) -> Dict[str, Any]:
    last_response = None
    for attempt in range(1, attempts + 1):
        response = requests.request(
            method,
            API_BASE + path,
            headers=HEADERS,
            params=params,
            json=body,
            timeout=60,
        )
        last_response = response
        if response.status_code in allow:
            if not response.text.strip():
                return {}
            return response.json()

        if response.status_code in TRANSIENT_STATUS and attempt < attempts:
            wait = min(5 * (2 ** (attempt - 1)), 30)
            print(
                f"Apple API transient HTTP {response.status_code} for {method} {path}; "
                f"retrying in {wait}s ({attempt}/{attempts})"
            )
            time.sleep(wait)
            continue
        break

    assert last_response is not None
    raise RuntimeError(
        f"{method} {path} failed: HTTP {last_response.status_code}\n{last_response.text[:4000]}"
    )


def request_delete(path: str) -> None:
    last_response = None
    for attempt in range(1, 4):
        response = requests.delete(API_BASE + path, headers=HEADERS, timeout=60)
        last_response = response
        if response.status_code in (204, 404):
            return
        if response.status_code == 409 and path.startswith("/certificates/"):
            # Apple returns 409 when a certificate is already no longer in the
            # issued state. For cleanup purposes that is already the desired end state.
            print(f"Certificate is already non-issued; cleanup can continue: {path}")
            return
        if response.status_code in TRANSIENT_STATUS and attempt < 3:
            wait = 3 * attempt
            print(f"Apple API transient HTTP {response.status_code} for DELETE {path}; retrying in {wait}s")
            time.sleep(wait)
            continue
        break

    assert last_response is not None
    raise RuntimeError(
        f"DELETE {path} failed: HTTP {last_response.status_code}\n{last_response.text[:4000]}"
    )


def find_bundle_id() -> str:
    payload = request("GET", "/bundleIds", params={"filter[identifier]": BUNDLE_ID, "limit": 10})
    for item in payload.get("data", []):
        if item.get("attributes", {}).get("identifier") == BUNDLE_ID:
            return str(item["id"])
    raise RuntimeError(f"Bundle ID is not registered: {BUNDLE_ID}. Run appstore_bootstrap.py first.")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def create_certificate(csr_text: str) -> Dict[str, Any]:
    errors = []
    for cert_type in ("DISTRIBUTION", "IOS_DISTRIBUTION"):
        body = {
            "data": {
                "type": "certificates",
                "attributes": {
                    "certificateType": cert_type,
                    "csrContent": csr_text,
                },
            }
        }
        try:
            created = request("POST", "/certificates", body=body)["data"]
            print(f"Created signing certificate {created['id']} ({cert_type})")
            return created
        except Exception as exc:
            errors.append(f"{cert_type}: {exc}")
    raise RuntimeError("Could not create distribution certificate:\n" + "\n".join(errors))


def create_profile(bundle_resource_id: str, certificate_resource_id: str) -> Dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    body = {
        "data": {
            "type": "profiles",
            "attributes": {
                "name": f"S.u.G EVOLINK TestFlight CI {stamp}",
                "profileType": "IOS_APP_STORE",
            },
            "relationships": {
                "bundleId": {
                    "data": {"type": "bundleIds", "id": bundle_resource_id}
                },
                "certificates": {
                    "data": [
                        {"type": "certificates", "id": certificate_resource_id}
                    ]
                },
            },
        }
    }
    # A freshly created distribution certificate can take a few seconds to
    # propagate through Apple's profile service. Give it a small head start;
    # request() also retries transient 5xx responses with backoff.
    time.sleep(5)
    created = request("POST", "/profiles", body=body, attempts=5)["data"]
    print(f"Created provisioning profile {created['id']}")
    return created


def write_github_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def cleanup_state(state_path: Path) -> int:
    if not state_path.exists():
        print("No previous CI signing state to clean")
        return 0

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot read signing state {state_path}: {exc}") from exc

    profile_id = state.get("profileId")
    certificate_id = state.get("certificateId")

    if profile_id:
        print(f"Deleting previous provisioning profile {profile_id}")
        request_delete(f"/profiles/{profile_id}")
    if certificate_id:
        print(f"Revoking previous CI distribution certificate {certificate_id}")
        request_delete(f"/certificates/{certificate_id}")

    state_path.unlink(missing_ok=True)
    print("Previous CI signing state cleaned")
    return 0


def prepare(output_dir: Path, state_path: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    bundle_resource_id = find_bundle_id()
    key_path = output_dir / "distribution.key.pem"
    csr_path = output_dir / "distribution.csr.pem"
    cert_der_path = output_dir / "distribution.cer"
    cert_pem_path = output_dir / "distribution.cert.pem"
    p12_path = output_dir / "distribution.p12"
    profile_path = output_dir / "EVOLINK-TestFlight.mobileprovision"

    run("openssl", "genrsa", "-out", str(key_path), "2048")
    run(
        "openssl",
        "req",
        "-new",
        "-key",
        str(key_path),
        "-out",
        str(csr_path),
        "-subj",
        "/CN=S.u.G EVOLINK TestFlight CI/O=S.u.G OSAKA/C=JP",
    )

    certificate = create_certificate(csr_path.read_text(encoding="utf-8"))
    certificate_id = str(certificate["id"])
    profile_id: Optional[str] = None

    try:
        certificate_content = certificate.get("attributes", {}).get("certificateContent")
        if not certificate_content:
            raise RuntimeError("Apple did not return certificateContent")
        cert_der_path.write_bytes(base64.b64decode(certificate_content))
        run("openssl", "x509", "-inform", "DER", "-in", str(cert_der_path), "-out", str(cert_pem_path))

        profile = create_profile(bundle_resource_id, certificate_id)
        profile_id = str(profile["id"])
        profile_content = profile.get("attributes", {}).get("profileContent")
        if not profile_content:
            raise RuntimeError("Apple did not return profileContent")
        profile_path.write_bytes(base64.b64decode(profile_content))

        p12_password = secrets.token_urlsafe(24)
        print(f"::add-mask::{p12_password}")
        run(
            "openssl",
            "pkcs12",
            "-export",
            "-out",
            str(p12_path),
            "-inkey",
            str(key_path),
            "-in",
            str(cert_pem_path),
            "-password",
            f"pass:{p12_password}",
        )

        state = {
            "bundleIdentifier": BUNDLE_ID,
            "bundleResourceId": bundle_resource_id,
            "certificateId": certificate_id,
            "profileId": profile_id,
            "createdAt": int(time.time()),
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        write_github_output("p12_path", str(p12_path))
        write_github_output("p12_password", p12_password)
        write_github_output("profile_path", str(profile_path))
        write_github_output("profile_name", str(profile.get("attributes", {}).get("name", "S.u.G EVOLINK TestFlight CI")))
        write_github_output("certificate_id", certificate_id)
        write_github_output("profile_id", profile_id)

        print(f"Prepared rotating signing assets for {BUNDLE_ID}")
        return 0
    except Exception:
        # If preparation fails before state is persisted, remove anything this
        # attempt created so scheduled retries do not leak certificates/profiles.
        if profile_id:
            try:
                print(f"Cleaning failed-attempt provisioning profile {profile_id}")
                request_delete(f"/profiles/{profile_id}")
            except Exception as cleanup_exc:
                print(f"WARNING: could not clean provisioning profile {profile_id}: {cleanup_exc}", file=sys.stderr)
        try:
            print(f"Cleaning failed-attempt certificate {certificate_id}")
            request_delete(f"/certificates/{certificate_id}")
        except Exception as cleanup_exc:
            print(f"WARNING: could not clean certificate {certificate_id}: {cleanup_exc}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    cleanup_parser = sub.add_parser("cleanup-state")
    cleanup_parser.add_argument("--state-file", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--state-file", required=True)

    args = parser.parse_args()
    if args.command == "cleanup-state":
        return cleanup_state(Path(args.state_file))
    return prepare(Path(args.output_dir), Path(args.state_file))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
