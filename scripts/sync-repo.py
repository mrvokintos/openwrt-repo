#!/usr/bin/env python3
"""
Sync OpenWrt packages from upstream GitHub releases (sing-box-clean and luci-app-singbox-ui),
organize them into package directories, sign packages and indexes, and build repository indexes.
"""

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES_DIR = os.path.join(REPO_ROOT, "packages")
ARCH_A53_DIR = os.path.join(PACKAGES_DIR, "aarch64_cortex-a53")
ARCH_ALL_DIR = os.path.join(PACKAGES_DIR, "all")
KEYS_DIR = os.path.join(REPO_ROOT, "keys")
USIGN_BIN = os.path.join(REPO_ROOT, "scripts", "usign")

GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def fetch_json(url):
    headers = {"User-Agent": "openwrt-repo-sync"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_file(url, dest_path):
    print(f"Downloading {url} -> {dest_path}...")
    headers = {"User-Agent": "openwrt-repo-sync"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out:
        out.write(resp.read())


def extract_ipk_metadata(ipk_path):
    with tarfile.open(ipk_path, "r:*") as outer_tar:
        control_tar_member = None
        for member in outer_tar.getmembers():
            if os.path.basename(member.name) == "control.tar.gz":
                control_tar_member = member
                break
        if not control_tar_member:
            raise ValueError(f"control.tar.gz not found in {ipk_path}")

        control_tar_bytes = outer_tar.extractfile(control_tar_member).read()
        with tarfile.open(fileobj=io.BytesIO(control_tar_bytes), mode="r:*") as inner_tar:
            control_member = None
            for member in inner_tar.getmembers():
                if os.path.basename(member.name) == "control":
                    control_member = member
                    break
            if not control_member:
                raise ValueError(f"control not found in {ipk_path}")

            text = inner_tar.extractfile(control_member).read().decode("utf-8", errors="replace")

    meta = {}
    for line in text.splitlines():
        if ":" in line and not line.startswith(" "):
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, text


def find_latest_releases():
    targets = {}

    print("Fetching releases for mrvokintos/sing-box-clean...")
    singbox_releases = fetch_json("https://api.github.com/repos/mrvokintos/sing-box-clean/releases")

    official_rel = None
    extended_rel = None

    for rel in singbox_releases:
        tag = rel.get("tag_name", "")
        if tag == "latest" or tag == "latest-extended":
            continue
        if "-extended-" in tag and not extended_rel:
            extended_rel = rel
        elif "-extended-" not in tag and not official_rel:
            official_rel = rel
        if official_rel and extended_rel:
            break

    if official_rel:
        targets["sing-box-official"] = official_rel
    if extended_rel:
        targets["sing-box-extended"] = extended_rel

    print("Fetching releases for mrvokintos/luci-app-singbox-ui...")
    ui_releases = fetch_json("https://api.github.com/repos/mrvokintos/luci-app-singbox-ui/releases")
    if ui_releases:
        targets["luci-app-singbox-ui"] = ui_releases[0]

    return targets


def resolve_keys():
    """
    Locates or prepares signing keys:
    1. APK RSA key: from env APK_SIGNING_KEY or ~/.config/openwrt-repo/openwrt.rsa
    2. OPKG usign key: from env USIGN_SECRET_KEY or ~/.config/openwrt-repo/openwrt.sec
    """
    apk_key_file = None
    usign_key_file = None
    cleanups = []

    # APK RSA Key
    apk_env = os.environ.get("APK_SIGNING_KEY")
    if apk_env:
        t = tempfile.NamedTemporaryFile(mode="w", delete=False, prefix="openwrt_", suffix=".rsa")
        t.write(apk_env.strip() + "\n")
        t.close()
        os.chmod(t.name, 0o600)
        apk_key_file = t.name
        cleanups.append(t.name)
    else:
        local_rsa = os.path.expanduser("~/.config/openwrt-repo/openwrt.rsa")
        if os.path.isfile(local_rsa):
            apk_key_file = local_rsa

    # usign Secret Key
    usign_env = os.environ.get("USIGN_SECRET_KEY")
    if usign_env:
        t = tempfile.NamedTemporaryFile(mode="w", delete=False, prefix="openwrt_", suffix=".sec")
        t.write(usign_env.strip() + "\n")
        t.close()
        os.chmod(t.name, 0o600)
        usign_key_file = t.name
        cleanups.append(t.name)
    else:
        local_sec = os.path.expanduser("~/.config/openwrt-repo/openwrt.sec")
        if os.path.isfile(local_sec):
            usign_key_file = local_sec

    return apk_key_file, usign_key_file, cleanups


def build_ipk_index(target_dir, usign_key=None):
    script_path = os.path.join(REPO_ROOT, "scripts", "build-ipk-index.py")
    subprocess.run([sys.executable, script_path, target_dir], check=True)

    packages_path = os.path.join(target_dir, "Packages")
    sig_path = os.path.join(target_dir, "Packages.sig")

    if usign_key and os.path.isfile(USIGN_BIN):
        print(f"Signing {packages_path} with usign...")
        cmd = [USIGN_BIN, "-S", "-m", packages_path, "-s", usign_key, "-x", sig_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f"Successfully created signature: {sig_path}")
        else:
            print(f"Warning: usign failed: {res.stderr}", file=sys.stderr)
    elif not usign_key:
        print("Notice: No usign secret key provided; skipping Packages.sig generation.")


def build_apk_index(target_dir, apk_key=None):
    apk_files = [f for f in os.listdir(target_dir) if f.endswith(".apk")]
    if not apk_files:
        return

    out_file = os.path.join(target_dir, "APKINDEX.tar.gz")
    print(f"Building APK index for {target_dir} ({len(apk_files)} APKs)...")

    # If APK RSA key is present, sign both the packages and the index!
    if apk_key:
        print(f"Signing APK packages and index with {os.path.basename(apk_key)}...")
        key_dir = os.path.dirname(os.path.abspath(apk_key))
        key_name = os.path.basename(apk_key)

        # 1. Sign each .apk package
        sign_pkgs_cmd = [
            "docker", "run", "--rm",
            "-v", f"{target_dir}:/work",
            "-v", f"{key_dir}:/keys:ro",
            "-w", "/work",
            "alpine:edge",
            "sh", "-c",
            f"for f in *.apk; do apk adbsign --allow-untrusted --reset-signatures --sign-key /keys/{key_name} \"$f\"; done"
        ]
        res = subprocess.run(sign_pkgs_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f"Warning: apk adbsign failed: {res.stderr}", file=sys.stderr)
        else:
            print(f"Successfully signed {len(apk_files)} APK package(s).")

        # 2. Build signed APKINDEX.tar.gz
        mkndx_cmd = [
            "docker", "run", "--rm",
            "-v", f"{target_dir}:/work",
            "-v", f"{key_dir}:/keys:ro",
            "-w", "/work",
            "alpine:edge",
            "sh", "-c",
            f"apk mkndx --allow-untrusted --sign-key /keys/{key_name} -o APKINDEX.tar.gz " + " ".join(apk_files)
        ]
        res = subprocess.run(mkndx_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f"Successfully generated signed {out_file}")
        else:
            print(f"Warning: apk mkndx failed: {res.stderr}", file=sys.stderr)
    else:
        print("Notice: No APK signing key provided; building unsigned APKINDEX.")
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{target_dir}:/work",
            "-w", "/work",
            "alpine:edge",
            "apk", "mkndx", "--allow-untrusted", "-o", "APKINDEX.tar.gz"
        ] + apk_files
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f"Successfully generated unsigned {out_file}")
        else:
            print(f"Warning: apk mkndx failed: {res.stderr}", file=sys.stderr)


def generate_sha256sums(target_dir):
    files = [f for f in sorted(os.listdir(target_dir)) if not f.startswith(".") and f != "SHA256SUMS"]
    lines = []
    for fname in files:
        fpath = os.path.join(target_dir, fname)
        if os.path.isfile(fpath) and not os.path.islink(fpath):
            with open(fpath, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
            lines.append(f"{h}  {fname}\n")

    sums_path = os.path.join(target_dir, "SHA256SUMS")
    with open(sums_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"Generated {sums_path}")


def sync_all():
    os.makedirs(ARCH_A53_DIR, exist_ok=True)
    os.makedirs(ARCH_ALL_DIR, exist_ok=True)
    os.makedirs(KEYS_DIR, exist_ok=True)

    apk_key, usign_key, cleanups = resolve_keys()

    try:
        releases = find_latest_releases()
        tmp_dir = os.path.join(REPO_ROOT, ".tmp_download")
        os.makedirs(tmp_dir, exist_ok=True)

        for rel_type, rel_info in releases.items():
            tag = rel_info["tag_name"]
            print(f"\nProcessing {rel_type} ({tag})...")
            assets = rel_info.get("assets", [])

            for asset in assets:
                name = asset["name"]
                url = asset["browser_download_url"]

                if not (name.endswith(".ipk") or name.endswith(".apk")):
                    continue

                local_tmp = os.path.join(tmp_dir, f"{tag}_{name}")
                download_file(url, local_tmp)

                if name.endswith(".ipk"):
                    meta, _ = extract_ipk_metadata(local_tmp)
                    pkg_name = meta.get("Package", name[:-4])
                    pkg_ver = meta.get("Version", tag.lstrip("v"))
                    pkg_arch = meta.get("Architecture", "all")
                    canonical_name = f"{pkg_name}_{pkg_ver}_{pkg_arch}.ipk"

                    if pkg_arch == "all":
                        dest = os.path.join(ARCH_ALL_DIR, canonical_name)
                        os.replace(local_tmp, dest)
                        dest_a53 = os.path.join(ARCH_A53_DIR, canonical_name)
                        if os.path.exists(dest_a53):
                            os.remove(dest_a53)
                        with open(dest, "rb") as src_f, open(dest_a53, "wb") as dst_f:
                            dst_f.write(src_f.read())
                        print(f"Saved {canonical_name} to both 'all' and 'aarch64_cortex-a53'")
                    else:
                        dest = os.path.join(ARCH_A53_DIR, canonical_name)
                        os.replace(local_tmp, dest)
                        print(f"Saved {canonical_name} to 'aarch64_cortex-a53'")

                elif name.endswith(".apk"):
                    base_name = name[:-4]
                    clean_ver = tag.lstrip("v")
                    canonical_name = f"{base_name}_{clean_ver}.apk"

                    if "luci-app-singbox-ui" in name:
                        dest = os.path.join(ARCH_ALL_DIR, canonical_name)
                        os.replace(local_tmp, dest)
                        dest_a53 = os.path.join(ARCH_A53_DIR, canonical_name)
                        if os.path.exists(dest_a53):
                            os.remove(dest_a53)
                        with open(dest, "rb") as src_f, open(dest_a53, "wb") as dst_f:
                            dst_f.write(src_f.read())
                        print(f"Saved {canonical_name} to both 'all' and 'aarch64_cortex-a53'")
                    else:
                        dest = os.path.join(ARCH_A53_DIR, canonical_name)
                        os.replace(local_tmp, dest)
                        print(f"Saved {canonical_name} to 'aarch64_cortex-a53'")

        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

        print("\n=== Building and Signing Repository Indexes ===")
        for d in [ARCH_ALL_DIR, ARCH_A53_DIR]:
            build_ipk_index(d, usign_key)
            build_apk_index(d, apk_key)
            generate_sha256sums(d)

        # Convenience symlinks
        def ensure_symlink(src, link_path):
            if os.path.islink(link_path) or os.path.exists(link_path):
                try:
                    os.remove(link_path)
                except IsADirectoryError:
                    pass
            try:
                os.symlink(src, link_path)
            except Exception as e:
                print(f"Notice: symlink {link_path} -> {src}: {e}")

        ensure_symlink("aarch64_cortex-a53", os.path.join(PACKAGES_DIR, "aarch64"))
        ensure_symlink("packages/aarch64_cortex-a53", os.path.join(REPO_ROOT, "aarch64_cortex-a53"))
        ensure_symlink("packages/all", os.path.join(REPO_ROOT, "all"))

        print("\nSync, signing, and index build finished successfully!")
    finally:
        for f in cleanups:
            if os.path.isfile(f):
                os.remove(f)


if __name__ == "__main__":
    sync_all()
