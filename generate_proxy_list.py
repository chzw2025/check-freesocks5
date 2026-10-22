#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代理列表抓取（新版数据源：Geonode）
- 数据来自 https://proxylist.geonode.com 的公开 API，明文 IP:端口，无需账号
- Geonode 自己会持续检测这些代理，返回里带最近检查时间、在线率、延迟
- 输出两个文件：
    proxy.txt        带国家城市标注，格式 协议://ip:port [国家 城市]
    proxy_clean.txt  干净版，只有 协议://ip:port，可直接导入客户端
"""

import time
from datetime import datetime

import requests

# ============ 配置区（按需改这里） ============
# 只保留这些国家的代理（ISO 两字母代码）；留空 [] = 全部国家都要
COUNTRIES = ["JP", "SG", "HK", "TW", "KR", "US"]
# 只保留这些协议
PROTOCOLS = ["http", "https", "socks4", "socks5"]
# 最多抓多少页（每页 500 条），防止一次跑太久
MAX_PAGES = 10
# 在线率低于这个数的丢弃（Geonode 的统计，0-100）；设 0 = 不过滤
MIN_UPTIME = 50
# =============================================

API = "https://proxylist.geonode.com/api/proxy-list"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def fetch_page(page):
    params = {
        "limit": 500,
        "page": page,
        "sort_by": "lastChecked",
        "sort_type": "desc",
    }
    for attempt in range(3):
        try:
            r = requests.get(API, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as e:
            print(f"第 {page} 页第 {attempt + 1} 次请求失败: {e}")
            time.sleep(3)
    return []


def main():
    seen = set()
    proxies = []  # (protocol, ip, port, location)
    for page in range(1, MAX_PAGES + 1):
        rows = fetch_page(page)
        if not rows:
            break
        for row in rows:
            country = (row.get("country") or "").upper()
            if COUNTRIES and country not in COUNTRIES:
                continue
            if row.get("upTime", 100) < MIN_UPTIME:
                continue
            ip = row.get("ip")
            port = row.get("port")
            if not ip or not port:
                continue
            city = row.get("city") or ""
            location = f"{country} {city}".strip()
            for proto in row.get("protocols") or []:
                proto = proto.lower()
                if proto not in PROTOCOLS:
                    continue
                key = (proto, ip, str(port))
                if key in seen:
                    continue
                seen.add(key)
                proxies.append((proto, ip, str(port), location))
        print(f"第 {page} 页处理完，累计 {len(proxies)} 条")
        time.sleep(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    countries_note = ",".join(COUNTRIES) if COUNTRIES else "全部"
    with open("proxy.txt", "w", encoding="utf-8") as f:
        f.write(f"# 代理列表更新时间: {now}\n")
        f.write(f"# 总计: {len(proxies)} 个代理（国家: {countries_note}，来源: Geonode）\n\n")
        for proto, ip, port, loc in proxies:
            f.write(f"{proto}://{ip}:{port} [{loc}]\n")
    with open("proxy_clean.txt", "w", encoding="utf-8") as f:
        f.write(f"# 更新时间: {now}，共 {len(proxies)} 个\n")
        for proto, ip, port, loc in proxies:
            f.write(f"{proto}://{ip}:{port}\n")
    print(f"完成：共 {len(proxies)} 条，已写入 proxy.txt 和 proxy_clean.txt")


if __name__ == "__main__":
    main()
