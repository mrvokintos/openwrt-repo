#!/usr/bin/env python3
"""
Generate OpenWrt opkg repository indexes (Packages and Packages.gz)
from a directory of .ipk package files.
"""

import gzip
import hashlib
import io
import os
import sys
import tarfile


def extract_control_text(ipk_path):
    """
    Extract control file text from an .ipk file (tar archive containing control.tar.gz).
    Supports both ar and tar formats, and arbitrary leading './' paths.
    """
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
                raise ValueError(f"control file not found in control.tar.gz of {ipk_path}")

            raw_bytes = inner_tar.extractfile(control_member).read()
            return raw_bytes.decode("utf-8", errors="replace")


def build_index(package_dir, output_dir=None):
    if output_dir is None:
        output_dir = package_dir

    if not os.path.isdir(package_dir):
        print(f"Error: {package_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    ipk_files = [f for f in os.listdir(package_dir) if f.endswith(".ipk")]
    ipk_files.sort()

    entries = []

    for filename in ipk_files:
        filepath = os.path.join(package_dir, filename)
        try:
            control_text = extract_control_text(filepath)
        except Exception as e:
            print(f"Warning: skipping {filename}: {e}", file=sys.stderr)
            continue

        file_size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            content = f.read()
            sha256 = hashlib.sha256(content).hexdigest()
            md5 = hashlib.md5(content).hexdigest()

        lines = control_text.strip().splitlines()
        new_lines = []
        inserted = False

        meta_lines = [
            f"Filename: {filename}",
            f"Size: {file_size}",
            f"MD5Sum: {md5}",
            f"SHA256sum: {sha256}",
        ]

        # Insert metadata right before Description: line, or append at the end
        for line in lines:
            if line.startswith("Description:") and not inserted:
                new_lines.extend(meta_lines)
                inserted = True
            new_lines.append(line)

        if not inserted:
            new_lines.extend(meta_lines)

        entries.append("\n".join(new_lines) + "\n")

    packages_content = "\n".join(entries) + ("\n" if entries else "")
    os.makedirs(output_dir, exist_ok=True)

    packages_path = os.path.join(output_dir, "Packages")
    packages_gz_path = os.path.join(output_dir, "Packages.gz")

    with open(packages_path, "w", encoding="utf-8") as f:
        f.write(packages_content)

    with gzip.open(packages_gz_path, "wb", compresslevel=9) as f:
        f.write(packages_content.encode("utf-8"))

    print(f"Successfully generated {packages_path} and {packages_gz_path} ({len(entries)} packages)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <package_directory> [output_directory]")
        sys.exit(1)

    pkg_dir = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else pkg_dir
    build_index(pkg_dir, out_dir)
