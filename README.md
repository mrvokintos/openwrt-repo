# OpenWrt Package Repository

[English](README.md) | [Русский](README_RU.md)

[![Sync and Publish OpenWrt Repo](https://github.com/mrvokintos/openwrt-repo/actions/workflows/sync-packages.yml/badge.svg)](https://github.com/mrvokintos/openwrt-repo/actions/workflows/sync-packages.yml)

Automated package repository for OpenWrt serving **sing-box** (Official), **sing-box-extended**, and **luci-app-singbox-ui** for ARM64 Cortex-A53 devices.

Web Dashboard: [https://openwrt.mrvokintos.ru/](https://openwrt.mrvokintos.ru/)

---

## Quick Setup

### 1. OpenWrt 21.02 / 22.03 / 23.05 (`opkg`)

```bash
# 1. (Optional) Import repository usign signing key
wget -O /etc/opkg/keys/5bab0ffe1aafaf8f https://openwrt.mrvokintos.ru/keys/5bab0ffe1aafaf8f

# 2. Add ARM64 Cortex-A53 feed (contains sing-box, sing-box-extended, and luci-app-singbox-ui)
echo "src/gz mrvoki_repo https://openwrt.mrvokintos.ru/packages/aarch64_cortex-a53" >> /etc/opkg/customfeeds.conf

# 3. Update package lists
opkg update

# 4. Install sing-box core and LuCI Web GUI
opkg install luci-app-singbox-ui sing-box

# (Optional) For sing-box-extended instead of official core:
# opkg install luci-app-singbox-ui sing-box-extended
```

*For non-ARM64 devices who only need `luci-app-singbox-ui`:*
```bash
echo "src/gz mrvoki_all https://openwrt.mrvokintos.ru/packages/all" >> /etc/opkg/customfeeds.conf
opkg update
opkg install luci-app-singbox-ui
```

---

### 2. OpenWrt 24.10+ (`apk`)

```bash
# 1. Trust repository RSA public key
wget -O /etc/apk/keys/openwrt.rsa.pub https://openwrt.mrvokintos.ru/keys/openwrt.rsa.pub

# 2. Add ARM64 Cortex-A53 repository
echo "https://openwrt.mrvokintos.ru/packages/aarch64_cortex-a53" >> /etc/apk/repositories.d/mrvoki.list

# 3. Update repository indexes
apk update

# 4. Install sing-box core and LuCI Web GUI (verified and trusted!)
apk add luci-app-singbox-ui sing-box

# (Optional) For sing-box-extended instead of official core:
# apk add luci-app-singbox-ui sing-box-extended
```

---

## Included Projects

- **[mrvokintos/sing-box-clean](https://github.com/mrvokintos/sing-box-clean)**: Automated builds of official sing-box and sing-box-extended for OpenWrt ARM64 Cortex-A53 (`with_quic,with_utls,with_naive_outbound,with_musl`).
- **[mrvokintos/luci-app-singbox-ui](https://github.com/mrvokintos/luci-app-singbox-ui)**: Modern LuCI web application for configuring and managing sing-box on OpenWrt routers.

---

## Repository Structure

```text
packages/
├── aarch64_cortex-a53/
│   ├── Packages, Packages.gz     # OPKG indexes
│   ├── APKINDEX.tar.gz          # APK index
│   ├── SHA256SUMS               # Checksums
│   ├── sing-box_*.ipk / .apk
│   ├── sing-box-extended_*.ipk / .apk
│   └── luci-app-singbox-ui_*.ipk / .apk
└── all/
    ├── Packages, Packages.gz
    ├── APKINDEX.tar.gz
    ├── SHA256SUMS
    └── luci-app-singbox-ui_*.ipk / .apk
```

---

## Maintenance & Automation

This repository automatically syncs daily with upstream releases using GitHub Actions ([`.github/workflows/sync-packages.yml`](.github/workflows/sync-packages.yml)), regenerates package indexes, and deploys directly to GitHub Pages.

