#!/usr/bin/env python3
"""Reuse existing EVOLINK signing assets. Apple API access is GET-only."""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import pkcs12

BUNDLE = "jp.sugosaka.evolink"
REPOSITORY = "koji06ochi-ship-it/SuG-EVOLINK-iOS"
HTML_SHA = "3dfb6ef8f3325b6d112a4f85462e94fb1ee4f617e299f7354d9bc0f3c9d3b9f4"
REQUIRED = ("EVOLINK_ASC_KEY_ID", "EVOLINK_ASC_ISSUER_ID", "EVOLINK_ASC_PRIVATE_KEY_B64",
            "EVOLINK_DISTRIBUTION_P12_B64", "EVOLINK_P12_PASSWORD", "EVOLINK_PROFILE_B64")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(*args, data=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(result.returncode == 0, f"Local signing tool failed: {Path(args[0]).name} (exit {result.returncode}); secret-bearing output suppressed")
    return result.stdout


def mask(value):
    if os.environ.get("GITHUB_ACTIONS") == "true" and value:
        print("::add-mask::" + value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))


def b64secret(name):
    return base64.b64decode("".join(os.environ[name].split()), validate=True)


def root():
    suffix = os.environ.get("GITHUB_RUN_ID", "") + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[0-9]+-[0-9]+", suffix), "A GitHub Actions run is required")
    parent = Path(os.environ["RUNNER_TEMP"]).resolve()
    result = parent / ("evolink-signing-" + suffix)
    require(result.parent == parent, "Invalid temporary signing location")
    return result


def save_state(state):
    target = root() / "state.json"
    target.write_text(json.dumps(state), encoding="utf-8")
    target.chmod(0o600)


def state():
    return json.loads((root() / "state.json").read_text(encoding="utf-8"))


def utc(value):
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def validate_profile(profile, cert):
    team = profile.get("TeamIdentifier", [""])[0]
    ent = profile.get("Entitlements", {})
    require(re.fullmatch(r"[A-Z0-9]{10}", team), "Invalid signing team")
    require(ent.get("application-identifier") == team + "." + BUNDLE, "Profile is not exactly EVOLINK")
    require(ent.get("com.apple.developer.healthkit") is True, "Existing profile lacks HealthKit")
    require(ent.get("get-task-allow") is False and not profile.get("ProvisionedDevices")
            and not profile.get("ProvisionsAllDevices", False), "Existing profile is not App Store distribution")
    require(utc(profile["ExpirationDate"]) > dt.datetime.now(dt.timezone.utc), "Existing profile has expired")
    require(cert.not_valid_before_utc <= dt.datetime.now(dt.timezone.utc) < cert.not_valid_after_utc,
            "Existing distribution certificate is outside its validity period")
    der = cert.public_bytes(serialization.Encoding.DER)
    require(der in profile.get("DeveloperCertificates", []), "P12 certificate is not included in the EVOLINK profile")
    require(re.fullmatch(r"[A-Fa-f0-9-]{36}", profile.get("UUID", "")), "Invalid profile UUID")
    require(profile.get("Name") and not re.search(r"[\r\n]", profile["Name"]), "Invalid profile name")
    return team


def apple_get(path, params=None):
    # No caller can choose an HTTP method or a non-Apple host.
    require(path.startswith("/") and not path.startswith("//"), "Invalid Apple read path")
    key = serialization.load_pem_private_key(b64secret("EVOLINK_ASC_PRIVATE_KEY_B64"), password=None)
    require(isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1), "ASC key must be P-256")
    enc = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=")
    now = int(time.time())
    header = enc(json.dumps({"alg": "ES256", "kid": os.environ["EVOLINK_ASC_KEY_ID"], "typ": "JWT"}).encode())
    payload = enc(json.dumps({"iss": os.environ["EVOLINK_ASC_ISSUER_ID"], "iat": now - 5, "exp": now + 900, "aud": "appstoreconnect-v1"}).encode())
    message = header + b"." + payload
    r, s = utils.decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
    token = (message + b"." + enc(r.to_bytes(32, "big") + s.to_bytes(32, "big"))).decode()
    url = "https://api.appstoreconnect.apple.com/v1" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token}, method="GET")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            require(response.url.startswith("https://api.appstoreconnect.apple.com/v1/"), "Unexpected Apple response origin")
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Existing Apple asset read failed: HTTP {error.code}; no certificate/profile creation or revocation attempted") from None


def confirm_apple_state(profile, cert):
    bundles = apple_get("/bundleIds", {"filter[identifier]": BUNDLE, "limit": 2})["data"]
    require(len(bundles) == 1 and bundles[0]["attributes"]["identifier"] == BUNDLE, "Existing EVOLINK Bundle ID not found")
    bundle_id = bundles[0]["id"]
    capabilities = apple_get(f"/bundleIds/{bundle_id}/bundleIdCapabilities")["data"]
    require(any(x["attributes"].get("capabilityType") == "HEALTHKIT" for x in capabilities), "Registered EVOLINK Bundle ID lacks HealthKit")
    certificates = apple_get("/certificates", {"filter[certificateType]": "DISTRIBUTION,IOS_DISTRIBUTION", "limit": 200})["data"]
    matches = [x for x in certificates if x["attributes"].get("certificateType") in ("DISTRIBUTION", "IOS_DISTRIBUTION")
               and base64.b64decode(x["attributes"].get("certificateContent", "")) == cert.public_bytes(serialization.Encoding.DER)]
    require(len(matches) == 1 and matches[0]["attributes"].get("activated") is not False, "Existing certificate is missing, revoked, or inactive")
    certificate_id = matches[0]["id"]
    profiles = apple_get(f"/bundleIds/{bundle_id}/profiles", {"limit": 200})
    candidates = [x for x in profiles["data"] if x["attributes"].get("uuid") == profile["UUID"]]
    require(len(candidates) == 1, "EVOLINK profile not found in the first 200 existing profiles; no automatic replacement")
    current = apple_get("/profiles/" + candidates[0]["id"], {"include": "bundleId,certificates"})["data"]
    attrs = current["attributes"]
    require(attrs.get("profileState") == "ACTIVE" and attrs.get("profileType") == "IOS_APP_STORE", "Existing EVOLINK profile is not active App Store distribution")
    require(current["relationships"]["bundleId"]["data"]["id"] == bundle_id, "Apple profile Bundle ID mismatch")
    require(certificate_id in [x["id"] for x in current["relationships"]["certificates"]["data"]], "Apple profile certificate relationship mismatch")
    require(base64.b64decode(attrs["profileContent"]) == b64secret("EVOLINK_PROFILE_B64"), "Stored profile differs from the current Apple profile")
    print("Existing EVOLINK certificate/profile and HealthKit confirmed through GET-only Apple API")


def profile_and_certificate():
    profile_path = root() / "EVOLINK.mobileprovision"
    profile = plistlib.loads(run("security", "cms", "-D", "-i", str(profile_path)))
    key, cert, _ = pkcs12.load_key_and_certificates(b64secret("EVOLINK_DISTRIBUTION_P12_B64"), os.environ["EVOLINK_P12_PASSWORD"].encode())
    require(key is not None and cert is not None, "P12 must include the existing certificate and private key")
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    require(key.public_key().public_bytes(*fmt) == cert.public_key().public_bytes(*fmt), "P12 private key does not match certificate")
    return profile, cert


def prepare():
    require(sys.platform == "darwin", "Existing-signing preparation requires an authorized Mac runner")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "EVOLINK repository only")
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    require(not missing, "Missing GitHub Secrets: " + ", ".join(missing))
    require(re.fullmatch(r"[1-9][0-9]{0,8}", os.environ.get("EVOLINK_BUILD_NUMBER", "")), "Invalid EVOLINK build number")
    os.umask(0o077)
    folder = root()
    folder.mkdir(mode=0o700)
    for name, secret in (("EVOLINK.p12", "EVOLINK_DISTRIBUTION_P12_B64"), ("EVOLINK.mobileprovision", "EVOLINK_PROFILE_B64")):
        (folder / name).write_bytes(b64secret(secret))
    original_keychains = re.findall(r'"([^"\n]+)"', run("security", "list-keychains", "-d", "user").decode())
    require(original_keychains, "Cannot safely preserve runner keychain search list")
    st = {"original_keychains": original_keychains, "keychain_created": False, "profile_installed": False}
    save_state(st)
    profile, cert = profile_and_certificate()
    team = validate_profile(profile, cert)
    common_name = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    require(common_name.startswith(("Apple Distribution:", "iPhone Distribution:")), "P12 is not an Apple distribution identity")
    for value in (common_name, profile["Name"], profile.get("TeamName", ""), team):
        mask(value)
    confirm_apple_state(profile, cert)
    fingerprint = cert.fingerprint(hashes.SHA1()).hex().upper()
    mask(fingerprint)
    keychain = folder / "EVOLINK.keychain-db"
    password = secrets.token_urlsafe(32)
    mask(password)
    run("security", "create-keychain", "-p", password, str(keychain))
    st["keychain_created"] = True
    save_state(st)
    run("security", "set-keychain-settings", "-lut", "21600", str(keychain))
    run("security", "unlock-keychain", "-p", password, str(keychain))
    run("security", "import", str(folder / "EVOLINK.p12"), "-P", os.environ["EVOLINK_P12_PASSWORD"], "-t", "cert", "-f", "pkcs12", "-k", str(keychain), "-T", "/usr/bin/codesign")
    run("security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-s", "-k", password, str(keychain))
    run("security", "list-keychains", "-d", "user", "-s", str(keychain), *original_keychains)
    identities = run("security", "find-identity", "-v", "-p", "codesigning", str(keychain)).decode()
    require(fingerprint in identities, "No valid imported Apple identity with private key; no replacement issued")
    directory = Path.home() / "Library/MobileDevice/Provisioning Profiles"
    directory.mkdir(parents=True, exist_ok=True)
    installed = directory / (profile["UUID"] + ".mobileprovision")
    require(not installed.exists(), "Refusing to overwrite an existing runner profile")
    st.update({"installed_profile": str(installed), "team": team, "uuid": profile["UUID"], "fingerprint": fingerprint})
    save_state(st)
    with installed.open("xb") as handle:
        handle.write(b64secret("EVOLINK_PROFILE_B64"))
    st["profile_installed"] = True
    save_state(st)
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as handle:
        for name, value in {"EVOLINK_TEAM_ID": team, "EVOLINK_PROFILE_NAME": profile["Name"], "EVOLINK_CERT_SHA1": fingerprint}.items():
            handle.write(name + "=" + value + "\n")
    export = {"method": "app-store-connect", "destination": "export", "signingStyle": "manual", "teamID": team,
              "signingCertificate": fingerprint, "stripSwiftSymbols": True, "uploadSymbols": False,
              "manageAppVersionAndBuildNumber": False, "provisioningProfiles": {BUNDLE: profile["Name"]}}
    (Path(os.environ["RUNNER_TEMP"]) / "EVOLINK-ExportOptions.plist").write_bytes(plistlib.dumps(export))
    print("Existing signing identity installed into a dedicated temporary keychain; no Apple assets changed")


def verify_app(app):
    st = state()
    info = plistlib.loads((app / "Info.plist").read_bytes())
    require(info.get("CFBundleIdentifier") == BUNDLE, "Signed app Bundle ID mismatch")
    require(info.get("CFBundleVersion") == os.environ["EVOLINK_BUILD_NUMBER"], "Signed app build number mismatch")
    require(hashlib.sha256((app / "WebContent/EVOLINK_OFFLINE.html").read_bytes()).hexdigest() == HTML_SHA, "Signed app runtime source mismatch")
    require(not (app / "Resources").exists(), "Reserved Resources directory reintroduced")
    run("codesign", "--verify", "--deep", "--strict", str(app))
    ent = plistlib.loads(run("codesign", "-d", "--entitlements", ":-", str(app)))
    require(ent.get("com.apple.developer.healthkit") is True, "Signed app lacks HealthKit entitlement")
    require(ent.get("application-identifier") == st["team"] + "." + BUNDLE, "Signed app identifier entitlement mismatch")
    require(ent.get("get-task-allow") is not True, "Debug entitlement in distribution app")
    embedded = plistlib.loads(run("security", "cms", "-D", "-i", str(app / "embedded.mobileprovision")))
    profile, cert = profile_and_certificate()
    validate_profile(embedded, cert)
    require(embedded["UUID"] == st["uuid"], "Embedded provisioning profile mismatch")
    prefix = root() / ("signature-" + secrets.token_hex(6) + "-")
    run("codesign", "-d", "--extract-certificates", str(prefix), str(app))
    require(hashlib.sha1(Path(str(prefix) + "0").read_bytes()).hexdigest().upper() == st["fingerprint"], "Actual code signature does not use the selected existing certificate")
    print("Actual Apple signature, HealthKit, embedded EVOLINK profile, build number and runtime verified")


def verify_archive():
    verify_app(Path(os.environ["RUNNER_TEMP"]) / "EVOLINK-signed.xcarchive/Products/Applications/SuGEvolink.app")


def verify_ipa():
    folder = Path(os.environ["RUNNER_TEMP"]) / "EVOLINK-ipa"
    files = list(folder.glob("*.ipa"))
    require(len(files) == 1, "Exactly one exported IPA is required")
    target = root() / "ipa-check"
    target.mkdir()
    run("unzip", "-q", str(files[0]), "-d", str(target))
    require(len(list((target / "Payload").glob("*.app"))) == 1, "Unexpected IPA application count")
    verify_app(target / "Payload/SuGEvolink.app")
    profile, cert = profile_and_certificate()
    confirm_apple_state(profile, cert)
    print("Signed IPA export verified; Apple upload has NOT been performed")


def cleanup():
    folder = root()
    if not folder.exists():
        return
    if (folder / "state.json").exists():
        st = state()
        if st.get("keychain_created"):
            run("security", "list-keychains", "-d", "user", "-s", *st["original_keychains"])
            run("security", "delete-keychain", str(folder / "EVOLINK.keychain-db"))
        if st.get("profile_installed"):
            installed = Path(st["installed_profile"])
            require(installed.parent == Path.home() / "Library/MobileDevice/Provisioning Profiles", "Cleanup profile location mismatch")
            if installed.exists():
                require(installed.read_bytes() == b64secret("EVOLINK_PROFILE_B64"), "Refusing to clean a changed runner profile")
                installed.unlink()
    shutil.rmtree(folder)
    print("Temporary runner signing copies removed; Apple certificates/profiles unchanged")


def save_draft():
    # GitHub draft releases are visible to repository writers, not public readers.
    # Release storage avoids billable Actions artifact/cache storage.
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "EVOLINK repository only")
    folder = Path(os.environ["RUNNER_TEMP"])
    files = list((folder / "EVOLINK-ipa").glob("*.ipa"))
    require(len(files) == 1, "Exactly one verified IPA required")
    archive = folder / "EVOLINK-signed.xcarchive"
    require(archive.is_dir(), "Signed archive missing")
    archive_zip = folder / "EVOLINK-signed.xcarchive.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(archive), str(archive_zip))
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2026-03-10", "Content-Type": "application/json"}
    base = "https://api.github.com/repos/" + REPOSITORY
    release = {"tag_name": "evolink-ipa-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"],
               "target_commitish": os.environ["GITHUB_SHA"], "name": "EVOLINK signed IPA - awaiting Apple upload approval",
               "body": "Signed archive and IPA only. Apple upload has NOT been performed.", "draft": True, "prerelease": False}
    request = urllib.request.Request(base + "/releases", data=json.dumps(release).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        created = json.load(response)
    require(created.get("draft") is True, "Output release must remain a draft")
    for item in (files[0], archive_zip):
        require(0 < item.stat().st_size < 2 * 1024 ** 3, "Invalid release output size")
        url = "https://uploads.github.com/repos/" + REPOSITORY + "/releases/" + str(created["id"]) + "/assets?" + urllib.parse.urlencode({"name": item.name})
        request = urllib.request.Request(url, data=item.read_bytes(), headers={**headers, "Content-Type": "application/octet-stream"}, method="POST")
        with urllib.request.urlopen(request, timeout=180) as response:
            uploaded = json.load(response)
        require(uploaded.get("state") == "uploaded", "Draft output upload did not complete")
        print(item.name + " SHA-256 " + hashlib.sha256(item.read_bytes()).hexdigest())
    print("Private-to-repository-writers draft output: " + created["html_url"])
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
        handle.write("Signed EVOLINK archive and IPA verified. Apple upload NOT performed.\n\nDraft outputs: " + created["html_url"] + "\n")


if __name__ == "__main__":
    actions = {"prepare": prepare, "verify-archive": verify_archive, "verify-ipa": verify_ipa, "cleanup": cleanup, "save-draft": save_draft}
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in actions, "Expected prepare, verify-archive, verify-ipa or cleanup")
        actions[sys.argv[1]]()
    except Exception as error:
        # Do not include command arguments, key material, HTTP bodies or tracebacks.
        print("ERROR: " + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
