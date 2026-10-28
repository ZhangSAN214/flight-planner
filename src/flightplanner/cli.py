"""命令行：

  flightplanner serve                     启动网页（默认 http://127.0.0.1:8010，被占用时自动换下一个端口）
  flightplanner search MAN PVG 2026-12-01 [--return 2026-12-15] [--mode cheapest]
  flightplanner check                     立即检查所有航线监控
"""

from __future__ import annotations

import argparse
import asyncio
import socket
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flightplanner", description="航班规划与特价雷达")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="启动网页")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--no-browser", action="store_true")

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

        host = getattr(args, "host", "127.0.0.1")
        port = _free_port(host, getattr(args, "port", DEFAULT_PORT))
        print(f"航班规划已启动：http://{host}:{port}  （关掉这个窗口就会停止）")
        if not getattr(args, "no_browser", False):
            webbrowser.open(f"http://{host}:{port}")
        uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
