"""命令行：

  flightplanner serve                     启动网页（默认 http://127.0.0.1:8010，被占用时自动换下一个端口）
  flightplanner serve --lan               同时让同一 Wi-Fi 下的平板、手机访问（需要访问码）
  flightplanner serve --cloud             部署在云端时用：监听 $PORT，必须设置 APP_PASSWORD
  flightplanner search MAN PVG 2026-12-01 [--return 2026-12-15] [--mode cheapest]
  flightplanner check                     立即检查所有航线监控
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import socket
import sys
import webbrowser
from datetime import date

from .config import load_settings
from .db import Store
from .models import SearchQuery
from .scoring import Mode, Preferences
from .service import Planner


def _fmt_minutes(m: int) -> str:
    return f"{m // 60}h{m % 60:02d}m"


async def _search(args: argparse.Namespace) -> None:
    settings = load_settings()
    planner = Planner(settings, Store(settings.db_path))
    q = SearchQuery(
        origin=args.origin,
        destination=args.destination,
        depart_date=date.fromisoformat(args.date),
        return_date=date.fromisoformat(args.return_date) if args.return_date else None,
        max_stops=args.max_stops,
    )
    res = await planner.search(q, Preferences(mode=Mode(args.mode), hour_value=args.hour_value))
    if settings.demo_mode:
        print("（演示数据：在 .env 填写 API key 后显示真实航班）")
    for e in res.errors:
        print("提示：", e)
    for i, it in enumerate(res.itineraries[: args.limit], 1):
        s0 = it.slices[0]
        route = " → ".join([s0.segments[0].origin] + [seg.destination for seg in s0.segments])
        tags = ",".join(t for t in it.tags if t in {"cheapest", "fastest", "best"})
        price = it.comparable_price()
        print(
            f"{i:2d}. {settings.currency} {price:>8.0f}  {_fmt_minutes(it.total_minutes):>7}  "
            f"中转{it.total_stops}  {s0.segments[0].depart:%m-%d %H:%M}  {route:<22} {'/'.join(it.airlines)}  [{it.source}] {tags}"
        )


async def _check() -> None:
    from .deals.watcher import check_all

    settings = load_settings()
    store = Store(settings.db_path)
    for r in await check_all(Planner(settings, store), store):
        print(r)


DEFAULT_PORT = 8010


def _free_port(host: str, port: int, tries: int = 20) -> int:
    """端口被别的程序占用时（比如另一个也用 8000 的本地网页），往后找一个空闲端口。"""
    for p in range(port, port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                if p == port:
                    print(f"端口 {p} 已被其他程序占用，换一个端口")
    return port


def _lan_ip() -> str:
    """本机在局域网里的地址（UDP connect 不会真的发数据）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _lan_key() -> str:
    """局域网访问码，存在 data 目录里，重启后不变，平板上的书签可以一直用。"""
    path = load_settings().data_dir / "lan_key.txt"
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(9)
    path.write_text(key)
    return key


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flightplanner", description="航班规划与特价雷达")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="启动网页")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--lan", action="store_true", help="让同一 Wi-Fi 下的平板、手机也能打开")
    serve.add_argument("--cloud", action="store_true", help="部署在云端（Render 等）时使用")

    search = sub.add_parser("search", help="命令行搜索")
    search.add_argument("origin")
    search.add_argument("destination")
    search.add_argument("date", help="出发日期 YYYY-MM-DD")
    search.add_argument("--return", dest="return_date", help="返程日期 YYYY-MM-DD")
    search.add_argument("--mode", default="best", choices=[m.value for m in Mode])
    search.add_argument("--max-stops", type=int)
    search.add_argument("--hour-value", type=float, default=15.0, help="多飞 1 小时相当于多花多少钱")
    search.add_argument("--limit", type=int, default=15)

    sub.add_parser("check", help="立即检查所有航线监控")

    args = parser.parse_args(argv)
    if args.cmd == "search":
        asyncio.run(_search(args))
    elif args.cmd == "check":
        asyncio.run(_check())
    else:
        import uvicorn

        from .api import create_app

        if getattr(args, "cloud", False):
            if not load_settings().app_password:
                sys.exit("云端运行必须设置环境变量 APP_PASSWORD，否则任何人都能用你的 API 额度")
            uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", DEFAULT_PORT)), proxy_headers=True, forwarded_allow_ips="*")
            return
        lan = getattr(args, "lan", False)
        host = "0.0.0.0" if lan else getattr(args, "host", "127.0.0.1")
        port = _free_port(host, getattr(args, "port", DEFAULT_PORT))
        key = _lan_key() if lan else None
        print(f"航班规划已启动：http://127.0.0.1:{port}  （关掉这个窗口就会停止）")
        if lan:
            print()
            print("平板 / 手机（连同一个 Wi-Fi）打开下面这个地址：")
            print(f"    http://{_lan_ip()}:{port}/?key={key}")
            print("第一次运行时 Windows 可能弹出防火墙提示，请勾选「专用网络」并允许。")
            print("访问码只给自己用；想换一个，删除 data\\lan_key.txt 再重启。")
            print()
        if not getattr(args, "no_browser", False):
            webbrowser.open(f"http://127.0.0.1:{port}")
        uvicorn.run(create_app(lan_key=key), host=host, port=port)


if __name__ == "__main__":
    main()
