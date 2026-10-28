#!/usr/bin/env python3
"""
Sync OpenWrt packages across multiple channels (stable, beta, git, dev) from
upstream GitHub releases (sing-box-clean, luci-app-singbox-ui) and CI artifacts (luci-app-singbox-ui/artifacts),
organize them into package directories, sign packages and indexes with RSA (apk) and usign (opkg),
and build repository indexes.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES_DIR = os.path.join(REPO_ROOT, "packages")
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


def fetch_text(url):
    headers = {"User-Agent": "openwrt-repo-sync"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


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


def patch_ipk_version(ipk_path, new_ver):
    """
    Updates the Version: field inside control.tar.gz within an .ipk file.
    """
    buf = io.BytesIO()
    with tarfile.open(ipk_path, "r:*") as outer_in, tarfile.open(fileobj=buf, mode="w:gz") as outer_out:
        for member in outer_in.getmembers():
            f = outer_in.extractfile(member)
            if member.name.endswith("control.tar.gz"):
                c_in_bytes = f.read()
                c_buf = io.BytesIO()
                with tarfile.open(fileobj=io.BytesIO(c_in_bytes), mode="r:*") as c_in, \
                     tarfile.open(fileobj=c_buf, mode="w:gz") as c_out:
                    for cm in c_in.getmembers():
                        cf = c_in.extractfile(cm)
                        if cm.name.endswith("control") and not cm.name.endswith("conffiles"):
                            text = cf.read().decode("utf-8")
                            text = re.sub(r"(?m)^Version:.*$", f"Version: {new_ver}", text)
                            new_bytes = text.encode("utf-8")
                            cm.size = len(new_bytes)
                            c_out.addfile(cm, io.BytesIO(new_bytes))
                        else:
                            c_out.addfile(cm, cf)
                c_buf.seek(0)
                member.size = len(c_buf.getvalue())
                outer_out.addfile(member, c_buf)
            else:
                outer_out.addfile(member, f)
    buf.seek(0)
    with open(ipk_path, "wb") as out_f:
        out_f.write(buf.getvalue())


def get_singbox_releases():
    print("Fetching releases for mrvokintos/sing-box-clean...")
    releases = fetch_json("https://api.github.com/repos/mrvokintos/sing-box-clean/releases")

    official_stable = None
    extended_stable = None
    official_beta = None
    extended_beta = None

    for rel in releases:
        tag = rel.get("tag_name", "")
        if tag in ("latest", "latest-extended"):
            continue

        is_prerelease = rel.get("prerelease", False) or ("rc" in tag.lower() or "beta" in tag.lower() or "alpha" in tag.lower())
        is_extended = "-extended-" in tag

        if is_extended:
            if not extended_stable and not is_prerelease:
                extended_stable = rel
            if not extended_beta:
                extended_beta = rel
        else:
            if not official_stable and not is_prerelease:
                official_stable = rel
            if not official_beta:
                official_beta = rel

    if not official_stable and official_beta:
        official_stable = official_beta
    if not extended_stable and extended_beta:
        extended_stable = extended_beta

    return {
        "official_stable": official_stable,
        "extended_stable": extended_stable,
        "official_beta": official_beta or official_stable,
        "extended_beta": extended_beta or extended_stable,
    }


def get_ui_release(want_prerelease=False):
    print(f"Fetching release for mrvokintos/luci-app-singbox-ui (want_prerelease={want_prerelease})...")
    releases = fetch_json("https://api.github.com/repos/mrvokintos/luci-app-singbox-ui/releases")
    if not releases:
        return None

    if want_prerelease:
        for r in releases:
            if r.get("prerelease", False) or "rc" in r.get("tag_name", "").lower() or "beta" in r.get("tag_name", "").lower():
                return r
        return releases[0]
    else:
        for r in releases:
            if not r.get("prerelease", False) and "rc" not in r.get("tag_name", "").lower() and "beta" not in r.get("tag_name", "").lower():
                return r
        # Fallback to latest available release if no official stable tag yet
        return releases[0]


def get_ui_artifact(branch):
    """
    Fetches the latest artifact filenames and download URLs from the 'artifacts' branch
    in mrvokintos/luci-app-singbox-ui.
    """
    print(f"Fetching latest artifacts for branch '{branch}' from mrvokintos/luci-app-singbox-ui/artifacts...")
    res = {}
    for ext in ("ipk", "apk"):
        index_url = f"https://raw.githubusercontent.com/mrvokintos/luci-app-singbox-ui/artifacts/{branch}/{ext}/index.txt"
        try:
            content = fetch_text(index_url)
            lines = [l.strip() for l in content.splitlines() if l.strip()]
        except Exception as e:
            print(f"Warning: could not fetch {index_url}: {e}")
            lines = []

        if not lines:
            continue

        def sort_key(name):
            m = re.search(r"(\d{8}-\d{4})", name)
            return m.group(1) if m else name

        lines.sort(key=sort_key, reverse=True)
        newest_file = lines[0]
        download_url = f"https://raw.githubusercontent.com/mrvokintos/luci-app-singbox-ui/artifacts/{branch}/{ext}/{newest_file}"
        res[ext] = {
            "filename": newest_file,
            "url": download_url,
        }
    return res


def resolve_keys():
    apk_key_file = None
    usign_key_file = None
    cleanups = []

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
    apk_files = [f for f in sorted(os.listdir(target_dir)) if f.endswith(".apk")]
    if not apk_files:
        return

    out_file = os.path.join(target_dir, "APKINDEX.tar.gz")
    print(f"Building APK index for {target_dir} ({len(apk_files)} APKs)...")

    if apk_key:
        print(f"Signing APK packages and index with {os.path.basename(apk_key)}...")
        key_dir = os.path.dirname(os.path.abspath(apk_key))
        key_name = os.path.basename(apk_key)

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


def copy_or_replace(src_path, dst_path):
    if os.path.exists(dst_path):
        os.remove(dst_path)
    with open(src_path, "rb") as sf, open(dst_path, "wb") as df:
        df.write(sf.read())


def clean_matching_packages(directory, prefix):
    if not os.path.isdir(directory):
        return
    for f in os.listdir(directory):
        if f.startswith(prefix) and (f.endswith(".ipk") or f.endswith(".apk")):
            os.remove(os.path.join(directory, f))


def sync_channel(channel_name, dir_a53, dir_all, singbox_releases, tmp_dir):
    print(f"\n========================================================")
    print(f"   Syncing Channel: {channel_name.upper()}")
    print(f"   a53 dir: {dir_a53}")
    print(f"   all dir: {dir_all}")
    print(f"========================================================")
    os.makedirs(dir_a53, exist_ok=True)
    os.makedirs(dir_all, exist_ok=True)

    # 1. Determine core releases
    if channel_name == "beta":
        official_core_rel = singbox_releases["official_beta"]
        extended_core_rel = singbox_releases["extended_beta"]
    else:
        official_core_rel = singbox_releases["official_stable"]
        extended_core_rel = singbox_releases["extended_stable"]

    clean_matching_packages(dir_a53, "sing-box_")
    clean_matching_packages(dir_a53, "sing-box-extended_")

    for rel in (official_core_rel, extended_core_rel):
        if not rel:
            continue
        tag = rel["tag_name"]
        for asset in rel.get("assets", []):
            name = asset["name"]
            if not (name.endswith(".ipk") or name.endswith(".apk")):
                continue
            url = asset["browser_download_url"]
            local_tmp = os.path.join(tmp_dir, f"{channel_name}_{tag}_{name}")
            download_file(url, local_tmp)

            if name.endswith(".ipk"):
                meta, _ = extract_ipk_metadata(local_tmp)
                pkg_name = meta.get("Package", name[:-4])
                pkg_ver = meta.get("Version", tag.lstrip("v"))
                pkg_arch = meta.get("Architecture", "aarch64_cortex-a53")
                canonical_name = f"{pkg_name}_{pkg_ver}_{pkg_arch}.ipk"
            else:
                base_name = name[:-4]
                clean_ver = tag.lstrip("v")
                canonical_name = f"{base_name}_{clean_ver}.apk"

            dest = os.path.join(dir_a53, canonical_name)
            copy_or_replace(local_tmp, dest)
            print(f"Saved {canonical_name} to {dir_a53}")

    # 2. Determine UI package
    clean_matching_packages(dir_a53, "luci-app-singbox-ui")
    clean_matching_packages(dir_all, "luci-app-singbox-ui")

    if channel_name in ("git", "dev"):
        branch = "main" if channel_name == "git" else "dev"
        artifacts = get_ui_artifact(branch)
        for ext in ("ipk", "apk"):
            item = artifacts.get(ext)
            if not item:
                print(f"Warning: No {ext} artifact found for branch {branch}")
                continue
            fname = item["filename"]
            furl = item["url"]
            local_tmp = os.path.join(tmp_dir, f"{channel_name}_{fname}")
            download_file(furl, local_tmp)

            if ext == "ipk":
                # Patch control version to guarantee monotonicity for opkg
                m_date = re.search(r"(\d{8}-\d{4})", fname)
                m_sha = re.search(r"-([0-9a-f]{7})-", fname)
                date_str = m_date.group(1).replace("-", "") if m_date else ""
                sha_str = m_sha.group(1) if m_sha else ""
                ver_suffix = f"{channel_name}.{date_str}.{sha_str}" if (date_str and sha_str) else channel_name

                meta, _ = extract_ipk_metadata(local_tmp)
                orig_ver = meta.get("Version", "3.0.0")
                if "git" not in orig_ver and "dev" not in orig_ver:
                    base_v = orig_ver.split("-")[0]
                    patch_ipk_version(local_tmp, f"{base_v}-{ver_suffix}")
                    print(f"Patched internal IPK Version: {base_v}-{ver_suffix}")

            dest_all = os.path.join(dir_all, fname)
            copy_or_replace(local_tmp, dest_all)
            dest_a53 = os.path.join(dir_a53, fname)
            copy_or_replace(local_tmp, dest_a53)
            print(f"Saved {fname} to both '{dir_all}' and '{dir_a53}'")

    else:
        # Release channels: stable and beta
        is_beta = (channel_name == "beta")
        ui_rel = get_ui_release(want_prerelease=is_beta)
        if ui_rel:
            tag = ui_rel["tag_name"]
            for asset in ui_rel.get("assets", []):
                name = asset["name"]
                if not (name.endswith(".ipk") or name.endswith(".apk")):
                    continue
                url = asset["browser_download_url"]
                local_tmp = os.path.join(tmp_dir, f"{channel_name}_{tag}_{name}")
                download_file(url, local_tmp)

                if name.endswith(".ipk"):
                    meta, _ = extract_ipk_metadata(local_tmp)
                    pkg_name = meta.get("Package", "luci-app-singbox-ui")
                    pkg_ver = meta.get("Version", tag.lstrip("v"))
                    pkg_arch = meta.get("Architecture", "all")
                    canonical_name = f"{pkg_name}_{pkg_ver}_{pkg_arch}.ipk"
                else:
                    base_name = name[:-4]
                    clean_ver = tag.lstrip("v")
                    canonical_name = f"{base_name}_{clean_ver}.apk"

                dest_all = os.path.join(dir_all, canonical_name)
                copy_or_replace(local_tmp, dest_all)
                dest_a53 = os.path.join(dir_a53, canonical_name)
                copy_or_replace(local_tmp, dest_a53)
                print(f"Saved {canonical_name} to both '{dir_all}' and '{dir_a53}'")


def sync_all():
    os.makedirs(KEYS_DIR, exist_ok=True)
    apk_key, usign_key, cleanups = resolve_keys()
    tmp_dir = os.path.join(REPO_ROOT, ".tmp_download")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        singbox_releases = get_singbox_releases()

        channels = [
            ("stable", os.path.join(PACKAGES_DIR, "aarch64_cortex-a53"), os.path.join(PACKAGES_DIR, "all")),
            ("beta", os.path.join(PACKAGES_DIR, "beta", "aarch64_cortex-a53"), os.path.join(PACKAGES_DIR, "beta", "all")),
            ("git", os.path.join(PACKAGES_DIR, "git", "aarch64_cortex-a53"), os.path.join(PACKAGES_DIR, "git", "all")),
            ("dev", os.path.join(PACKAGES_DIR, "dev", "aarch64_cortex-a53"), os.path.join(PACKAGES_DIR, "dev", "all")),
        ]

        all_target_dirs = []
        for ch_name, ch_a53, ch_all in channels:
            sync_channel(ch_name, ch_a53, ch_all, singbox_releases, tmp_dir)
            all_target_dirs.extend([ch_a53, ch_all])

        print("\n=== Building and Signing Repository Indexes for All Channels ===")
        for d in all_target_dirs:
            build_ipk_index(d, usign_key)
            build_apk_index(d, apk_key)
            generate_sha256sums(d)

        def ensure_symlink(src, link_path):
            if os.path.islink(link_path) or os.path.exists(link_path):
                try:
                    os.remove(link_path)
                except IsADirectoryError:
                    pass
            try:
                os.symlink(src, link_path)
                print(f"Symlink: {link_path} -> {src}")
            except Exception as e:
                print(f"Notice: symlink {link_path} -> {src}: {e}")

        # Root convenience symlinks
        ensure_symlink("packages/aarch64_cortex-a53", os.path.join(REPO_ROOT, "aarch64_cortex-a53"))
        ensure_symlink("packages/all", os.path.join(REPO_ROOT, "all"))
        ensure_symlink("packages/beta", os.path.join(REPO_ROOT, "beta"))
        ensure_symlink("packages/git", os.path.join(REPO_ROOT, "git"))
        ensure_symlink("packages/dev", os.path.join(REPO_ROOT, "dev"))
        ensure_symlink("aarch64_cortex-a53", os.path.join(PACKAGES_DIR, "aarch64"))

        print("\nAll 4 channels (stable, beta, git, dev) synced, signed, and indexed successfully!")

    finally:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        for f in cleanups:
            if os.path.isfile(f):
                os.remove(f)


if __name__ == "__main__":
    sync_all()
