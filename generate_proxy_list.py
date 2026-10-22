#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代理列表抓取 + 验活（v2，数据源：Geonode）
- 数据来自 https://proxylist.geonode.com 的公开 API，明文 IP:端口，无需账号
- v2 新增：抓完后逐个真实连接验活，只把真能用的写进名单
  验活方式：经候选代理请求 Cloudflare 的 trace 页，能返回出口 IP 才算活，
  并记录实测出口 IP 和响应延迟，按延迟从快到慢排序
- 输出两个文件（都只含验活通过的代理）：
    proxy.txt        带标注：协议://ip:port [国家 城市] 出口IP x.x.x.x · 延迟ms
    proxy_clean.txt  干净版，只有 协议://ip:port，可直接导入客户端
- 安全阀：若验活通过数为 0（疑似网络异常），脚本报错退出且不覆盖旧文件，
  仓库保留上一版名单，Actions 会显示红色失败而不是悄悄清空
- 依赖：requests + PySocks（见 requirements.txt）
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
import urllib3

# https 类代理的证书警告只会刷屏日志，与验活结果无关，关掉
urllib3.disable_warnings()

# ============ 配置区（按需改这里） ============
# 只保留这些国家的代理（ISO 两字母代码）；留空 [] = 全部国家都要
COUNTRIES = ["JP", "SG", "HK", "TW", "KR", "US"]
# 只保留这些协议
PROTOCOLS = ["http", "https", "socks4", "socks5"]
# 最多抓多少页（每页 500 条），防止一次跑太久
MAX_PAGES = 10
# 在线率低于这个数的丢弃（Geonode 的统计，0-100）；设 0 = 不过滤
MIN_UPTIME = 50
# 验活：单个代理的超时秒数（连不上或超时的直接淘汰）
CHECK_TIMEOUT = 8
# 验活并发数（太高容易被目标站限流，60 比较稳）
CHECK_WORKERS = 60
# =============================================

API = "https://proxylist.geonode.com/api/proxy-list"
CHECK_URL = "http://www.cloudflare.com/cdn-cgi/trace"
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


def fetch_candidates():
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
    return proxies


def check_one(item):
    """经候选代理请求 trace 页；成功返回 (item, 出口IP, 延迟ms)，失败返回 None"""
    proto, ip, port, _loc = item
    if proto == "socks5":
        proxy_url = f"socks5h://{ip}:{port}"   # h = DNS 在代理端解析
    elif proto == "socks4":
        proxy_url = f"socks4a://{ip}:{port}"   # a = DNS 在代理端解析
    else:
        proxy_url = f"{proto}://{ip}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    start = time.time()
    try:
        r = requests.get(CHECK_URL, proxies=proxies,
                         timeout=CHECK_TIMEOUT, headers=HEADERS)
        elapsed_ms = int((time.time() - start) * 1000)
        if r.status_code != 200:
            return None
        exit_ip = ""
        for line in r.text.splitlines():
            if line.startswith("ip="):
                exit_ip = line[3:].strip()
                break
        if not exit_ip:
            return None
        return (item, exit_ip, elapsed_ms)
    except Exception:
        return None


def verify_all(candidates):
    alive = []
    done = 0
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
        futures = [pool.submit(check_one, c) for c in candidates]
        for fut in as_completed(futures):
            done += 1
            res = fut.result()
            if res:
                alive.append(res)
            if done % 100 == 0:
                print(f"验活进度 {done}/{len(candidates)}，已活 {len(alive)} 条")
    alive.sort(key=lambda x: x[2])  # 按延迟从快到慢
    return alive


def main():
    candidates = fetch_candidates()
    print(f"抓取完成：候选 {len(candidates)} 条，开始验活"
          f"（超时 {CHECK_TIMEOUT}s，并发 {CHECK_WORKERS}）...")
    alive = verify_all(candidates)
    print(f"验活完成：{len(candidates)} 条里活了 {len(alive)} 条")

    if not alive:
        print("错误：验活通过数为 0，疑似网络异常，不覆盖旧文件，退出。")
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    countries_note = ",".join(COUNTRIES) if COUNTRIES else "全部"
    with open("proxy.txt", "w", encoding="utf-8") as f:
        f.write(f"# 代理列表更新时间: {now}\n")
        f.write(f"# 已验活：候选 {len(candidates)} 个，通过 {len(alive)} 个"
                f"（国家: {countries_note}，来源: Geonode，出口IP与延迟为验活实测）\n\n")
        for (proto, ip, port, loc), exit_ip, ms in alive:
            f.write(f"{proto}://{ip}:{port} [{loc}] 出口 {exit_ip} · {ms}ms\n")
    with open("proxy_clean.txt", "w", encoding="utf-8") as f:
        f.write(f"# 更新时间: {now}，已验活共 {len(alive)} 个\n")
        for (proto, ip, port, loc), _exit_ip, _ms in alive:
            f.write(f"{proto}://{ip}:{port}\n")
    print(f"完成：{len(alive)} 条已写入 proxy.txt 和 proxy_clean.txt")


if __name__ == "__main__":
    main()
