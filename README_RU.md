# Репозиторий пакетов OpenWrt

[English](README.md) | [Русский](README_RU.md)

[![Sync and Publish OpenWrt Repo](https://github.com/mrvokintos/openwrt-repo/actions/workflows/sync-packages.yml/badge.svg)](https://github.com/mrvokintos/openwrt-repo/actions/workflows/sync-packages.yml)

Автоматизированный репозиторий пакетов OpenWrt для ядер **sing-box** (Official), **sing-box-extended** и веб-интерфейса **luci-app-singbox-ui** под архитектуру ARM64 Cortex-A53.

Веб-страница: [https://openwrt.mrvokintos.ru/](https://openwrt.mrvokintos.ru/)

---

## Быстрая настройка

### 1. OpenWrt 21.02 / 22.03 / 23.05 / 24.10 (`opkg`)

```bash
# 1. (Опционально) Импорт ключа usign для проверки подписи
wget -O /etc/opkg/keys/5bab0ffe1aafaf8f https://openwrt.mrvokintos.ru/keys/5bab0ffe1aafaf8f

# 2. Добавление репозитория для ARM64 Cortex-A53 (содержит sing-box, sing-box-extended и luci-app-singbox-ui)
echo "src/gz mrvoki_repo https://openwrt.mrvokintos.ru/packages/aarch64_cortex-a53" >> /etc/opkg/customfeeds.conf

# 3. Обновление списка пакетов
opkg update

# 4. Установка веб-интерфейса и ядра sing-box
opkg install luci-app-singbox-ui sing-box

# (Опционально) Для установки версии sing-box-extended вместо официальной:
# opkg install luci-app-singbox-ui sing-box-extended
```

*Для устройств других архитектур, которым нужен только интерфейс `luci-app-singbox-ui`:*
```bash
echo "src/gz mrvoki_all https://openwrt.mrvokintos.ru/packages/all" >> /etc/opkg/customfeeds.conf
opkg update
opkg install luci-app-singbox-ui
```

---

### 2. OpenWrt 25.12+ (`apk`)

> [!NOTE]
> APK (*Alpine Package Keeper*) был разработан проектом [Alpine Linux](https://alpinelinux.org/) и является пакетным менеджером в OpenWrt 25.12 и новее. Он пришел на смену [opkg](https://openwrt.org/docs/guide-user/additional-software/opkg), активная разработка которого завершилась. Обратите внимание: формат `apk` в OpenWrt не имеет никакого отношения к пакетам Android или другим системам с похожей аббревиатурой.

```bash
# 1. Доверие публичному RSA-ключу репозитория
wget -O /etc/apk/keys/openwrt.rsa.pub https://openwrt.mrvokintos.ru/keys/openwrt.rsa.pub

# 2. Добавление репозитория для ARM64 Cortex-A53
echo "https://openwrt.mrvokintos.ru/packages/aarch64_cortex-a53" >> /etc/apk/repositories.d/mrvoki.list

# 3. Обновление индексов
apk update

# 4. Установка веб-интерфейса и ядра sing-box (проверено цифровой подписью!)
apk add luci-app-singbox-ui sing-box

# (Опционально) Для версии sing-box-extended:
# apk add luci-app-singbox-ui sing-box-extended
```

---

## Входящие проекты

- **[mrvokintos/sing-box-clean](https://github.com/mrvokintos/sing-box-clean)**: Сборки sing-box и sing-box-extended под OpenWrt ARM64 Cortex-A53 с поддержкой NaiveProxy, Hysteria 2, REALITY, uTLS и musl libc.
- **[mrvokintos/luci-app-singbox-ui](https://github.com/mrvokintos/luci-app-singbox-ui)**: Современный LuCI веб-интерфейс для настройки и управления sing-box на OpenWrt.

---

## Структура репозитория

```text
packages/
├── aarch64_cortex-a53/
│   ├── Packages, Packages.gz     # Индексы для OPKG
│   ├── APKINDEX.tar.gz          # Индекс для APK
│   ├── SHA256SUMS               # Контрольные суммы
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

## Автоматизация

Репозиторий ежедневно автоматически опрашивает новые релизы проектов через GitHub Actions ([`.github/workflows/sync-packages.yml`](.github/workflows/sync-packages.yml)), пересобирает индексы `Packages.gz` и `APKINDEX.tar.gz` и обновляет сайт на GitHub Pages.

