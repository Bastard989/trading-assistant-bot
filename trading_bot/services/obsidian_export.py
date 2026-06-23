from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


def build_obsidian_vault_zip(
    *,
    sessions: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    journal_entries: Sequence[Mapping[str, Any]],
    generated_at: datetime | None = None,
) -> bytes:
    generated_at = generated_at or datetime.now(timezone.utc)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as vault:
        writer = VaultWriter(generated_at)
        writer.write(vault, "Trading Assistant Export/README.md", readme_note(generated_at))
        writer.write(vault, "Trading Assistant Export/Dashboard.md", dashboard_note(sessions, trades, journal_entries, generated_at))

        session_filenames = {int(session["id"]): session_filename(session) for session in sessions if session.get("id") is not None}
        trade_filenames = {int(trade["id"]): trade_filename(trade) for trade in trades if trade.get("id") is not None}

        for session in sessions:
            writer.write(
                vault,
                f"Trading Assistant Export/Sessions/{session_filename(session)}",
                session_note(session, trades_for_session(trades, session), journal_for_session(journal_entries, session), trade_filenames),
            )

        for trade in trades:
            writer.write(
                vault,
                f"Trading Assistant Export/Trades/{trade_filename(trade)}",
                trade_note(trade, session_filenames, journal_for_trade(journal_entries, trade)),
            )

        for entry in journal_entries:
            writer.write(
                vault,
                f"Trading Assistant Export/Journal/{journal_filename(entry)}",
                journal_note(entry, session_filenames, trade_filenames),
            )

        for symbol, symbol_trades in sorted(group_by_symbol(trades).items()):
            writer.write(
                vault,
                f"Trading Assistant Export/Coins/{safe_filename(symbol)}.md",
                coin_note(symbol, symbol_trades, trade_filenames),
            )

        for day, day_entries in sorted(group_by_day(journal_entries, "created_at").items(), reverse=True):
            writer.write(
                vault,
                f"Trading Assistant Export/Daily/{safe_filename(day)}.md",
                daily_note(day, day_entries, trade_filenames),
            )

        writer.write(
            vault,
            "Trading Assistant Export/Canvas/Trading Map.canvas",
            trading_canvas(sessions, trades, journal_entries, session_filenames, trade_filenames),
        )

    return archive.getvalue()


class VaultWriter:
    def __init__(self, generated_at: datetime) -> None:
        self.generated_at = generated_at

    def write(self, vault: zipfile.ZipFile, path: str, content: str) -> None:
        info = zipfile.ZipInfo(path, date_time=self.generated_at.timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        vault.writestr(info, content.encode("utf-8"))


def readme_note(generated_at: datetime) -> str:
    return (
        "# Trading Assistant Export\n\n"
        f"Generated at: `{generated_at.isoformat()}`\n\n"
        "Open this folder as an Obsidian vault or copy its contents into an existing vault.\n\n"
        "Start from [[Dashboard]] or open `Canvas/Trading Map.canvas` for the visual map.\n"
    )


def dashboard_note(
    sessions: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    journal_entries: Sequence[Mapping[str, Any]],
    generated_at: datetime,
) -> str:
    closed = [trade for trade in trades if trade.get("status") == "closed"]
    pnl = sum(float_value(trade.get("pnl")) or 0 for trade in closed)
    wins = sum(1 for trade in closed if (float_value(trade.get("pnl")) or 0) > 0)
    winrate = wins / len(closed) * 100 if closed else 0
    symbols = sorted(group_by_symbol(trades))
    body = frontmatter(
        {
            "type": "trading-dashboard",
            "generated_at": generated_at.isoformat(),
            "sessions": len(sessions),
            "trades": len(trades),
            "journal_entries": len(journal_entries),
            "tags": ["trading/dashboard"],
        }
    )
    body += "# Trading Dashboard\n\n"
    body += f"- Sessions: **{len(sessions)}**\n"
    body += f"- Trades: **{len(trades)}**\n"
    body += f"- Closed trades: **{len(closed)}**\n"
    body += f"- Net PnL: **{pnl:g} USDT**\n"
    body += f"- Winrate: **{winrate:.1f}%**\n\n"
    body += "## Navigation\n\n"
    body += "- [[Trading Map.canvas|Trading Map]]\n"
    body += "- Sessions folder: `Sessions/`\n"
    body += "- Trades folder: `Trades/`\n"
    body += "- Journal folder: `Journal/`\n\n"
    if symbols:
        body += "## Coins\n\n"
        body += "\n".join(f"- [[{symbol}]]" for symbol in symbols)
        body += "\n"
    return body


def session_note(
    session: Mapping[str, Any],
    session_trades: Sequence[Mapping[str, Any]],
    session_journal: Sequence[Mapping[str, Any]],
    trade_filenames: Mapping[int, str],
) -> str:
    properties = {
        "type": "trading-session",
        "session_id": int(session["id"]),
        "status": text(session.get("status")),
        "started_at": text(session.get("started_at")),
        "archived_at": text(session.get("archived_at")),
        "start_balance": float_value(session.get("start_balance")),
        "target_balance": float_value(session.get("target_balance")),
        "trade_count": len(session_trades),
        "realized_pnl": float_value(session.get("realized_pnl")),
        "tags": ["trading/session"],
    }
    body = frontmatter(properties)
    body += f"# {text(session.get('name')) or 'Trading Session'}\n\n"
    if text(session.get("note")):
        body += f"{text(session.get('note'))}\n\n"
    body += "## Trades\n\n"
    body += linked_list([trade_filenames[int(trade["id"])] for trade in session_trades if trade.get("id") in trade_filenames])
    body += "\n## Journal\n\n"
    body += linked_list([journal_filename(entry) for entry in session_journal])
    return body


def trade_note(
    trade: Mapping[str, Any],
    session_filenames: Mapping[int, str],
    linked_journal: Sequence[Mapping[str, Any]],
) -> str:
    session_id = int(trade["session_id"]) if trade.get("session_id") is not None else None
    session_link = basename_without_md(session_filenames[session_id]) if session_id in session_filenames else ""
    properties = {
        "type": "trade",
        "trade_id": int(trade["id"]),
        "symbol": text(trade.get("symbol")),
        "side": text(trade.get("side")),
        "status": text(trade.get("status")),
        "entry": float_value(trade.get("entry_price")),
        "stop": float_value(trade.get("stop_price")),
        "target": float_value(trade.get("target_price")),
        "quantity": float_value(trade.get("quantity")),
        "leverage": float_value(trade.get("leverage")),
        "pnl": float_value(trade.get("pnl")),
        "opened_at": text(trade.get("opened_at")),
        "closed_at": text(trade.get("closed_at")),
        "session": f"[[{session_link}]]" if session_link else "",
        "tags": ["trading/trade", f"coin/{text(trade.get('symbol'))}"],
    }
    body = frontmatter(properties)
    body += f"# Trade {trade.get('id')} {text(trade.get('symbol'))} {text(trade.get('side')).upper()}\n\n"
    body += "| Field | Value |\n|---|---:|\n"
    body += f"| Entry | {number_or_dash(trade.get('entry_price'))} |\n"
    body += f"| Stop | {number_or_dash(trade.get('stop_price'))} |\n"
    body += f"| Target | {number_or_dash(trade.get('target_price'))} |\n"
    body += f"| Quantity | {number_or_dash(trade.get('quantity'))} |\n"
    body += f"| Leverage | {number_or_dash(trade.get('leverage'))} |\n"
    body += f"| PnL | {number_or_dash(trade.get('pnl'))} |\n\n"
    if text(trade.get("note")):
        body += f"## Note\n\n{text(trade.get('note'))}\n\n"
    if linked_journal:
        body += "## Linked journal\n\n"
        body += linked_list([journal_filename(entry) for entry in linked_journal])
    return body


def journal_note(
    entry: Mapping[str, Any],
    session_filenames: Mapping[int, str],
    trade_filenames: Mapping[int, str],
) -> str:
    session_id = int(entry["session_id"]) if entry.get("session_id") is not None else None
    trade_id = int(entry["linked_trade_id"]) if entry.get("linked_trade_id") is not None else None
    properties = {
        "type": "journal-entry",
        "journal_id": int(entry["id"]),
        "symbol": text(entry.get("symbol")),
        "outcome": text(entry.get("outcome")),
        "theory": text(entry.get("theory")),
        "created_at": text(entry.get("created_at")),
        "session": f"[[{basename_without_md(session_filenames[session_id])}]]" if session_id in session_filenames else "",
        "linked_trade": f"[[{basename_without_md(trade_filenames[trade_id])}]]" if trade_id in trade_filenames else "",
        "tags": ["trading/journal"],
    }
    body = frontmatter(properties)
    body += f"# Journal {entry.get('id')} {text(entry.get('symbol'))}\n\n"
    body += text(entry.get("description")) or "No description."
    body += "\n"
    screenshot = text(entry.get("screenshot_file_id"))
    if screenshot:
        body += f"\n## Screenshot reference\n\n`{screenshot}`\n"
    return body


def coin_note(symbol: str, symbol_trades: Sequence[Mapping[str, Any]], trade_filenames: Mapping[int, str]) -> str:
    body = frontmatter({"type": "coin", "symbol": symbol, "trade_count": len(symbol_trades), "tags": ["trading/coin"]})
    body += f"# {symbol}\n\n"
    body += linked_list([trade_filenames[int(trade["id"])] for trade in symbol_trades if int(trade["id"]) in trade_filenames])
    return body


def daily_note(day: str, entries: Sequence[Mapping[str, Any]], trade_filenames: Mapping[int, str]) -> str:
    body = frontmatter({"type": "trading-day", "date": day, "journal_entries": len(entries), "tags": ["trading/day"]})
    body += f"# {day}\n\n"
    for entry in entries:
        trade_id = int(entry["linked_trade_id"]) if entry.get("linked_trade_id") is not None else None
        linked_trade = f" → [[{basename_without_md(trade_filenames[trade_id])}]]" if trade_id in trade_filenames else ""
        body += f"- [[{basename_without_md(journal_filename(entry))}]]{linked_trade}\n"
    return body


def trading_canvas(
    sessions: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    journal_entries: Sequence[Mapping[str, Any]],
    session_filenames: Mapping[int, str],
    trade_filenames: Mapping[int, str],
) -> str:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    session_x = 0
    trade_x = 420
    journal_x = 840
    y_by_session: dict[int | None, int] = {}

    for index, session in enumerate(sessions):
        session_id = int(session["id"])
        y = index * 260
        y_by_session[session_id] = y
        nodes.append(canvas_file_node(f"session-{session_id}", session_x, y, f"Sessions/{session_filenames[session_id]}", color="6"))

    y_by_session[None] = len(sessions) * 260
    trade_offsets: defaultdict[int | None, int] = defaultdict(int)
    for trade in trades:
        trade_id = int(trade["id"])
        session_id = int(trade["session_id"]) if trade.get("session_id") is not None else None
        y = y_by_session.get(session_id, y_by_session[None]) + trade_offsets[session_id] * 120
        trade_offsets[session_id] += 1
        nodes.append(canvas_file_node(f"trade-{trade_id}", trade_x, y, f"Trades/{trade_filenames[trade_id]}", color="4" if trade.get("status") == "closed" else "5"))
        if session_id in session_filenames:
            edges.append(canvas_edge(f"edge-session-{session_id}-trade-{trade_id}", f"session-{session_id}", f"trade-{trade_id}", "contains"))

    journal_offsets: defaultdict[int | None, int] = defaultdict(int)
    for entry in journal_entries:
        entry_id = int(entry["id"])
        trade_id = int(entry["linked_trade_id"]) if entry.get("linked_trade_id") is not None else None
        session_id = int(entry["session_id"]) if entry.get("session_id") is not None else None
        anchor = session_id if session_id in y_by_session else None
        y = y_by_session.get(anchor, y_by_session[None]) + journal_offsets[anchor] * 100
        journal_offsets[anchor] += 1
        nodes.append(canvas_file_node(f"journal-{entry_id}", journal_x, y, f"Journal/{journal_filename(entry)}", color="3"))
        if trade_id in trade_filenames:
            edges.append(canvas_edge(f"edge-trade-{trade_id}-journal-{entry_id}", f"trade-{trade_id}", f"journal-{entry_id}", "journal"))
    return json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2)


def canvas_file_node(node_id: str, x: int, y: int, file: str, color: str) -> dict[str, Any]:
    return {"id": node_id, "type": "file", "file": file, "x": x, "y": y, "width": 320, "height": 90, "color": color}


def canvas_edge(edge_id: str, from_node: str, to_node: str, label: str) -> dict[str, Any]:
    return {"id": edge_id, "fromNode": from_node, "toNode": to_node, "label": label}


def session_filename(session: Mapping[str, Any]) -> str:
    started = day_part(session.get("started_at")) or "session"
    return safe_filename(f"{started} Session {session.get('id')} {text(session.get('name'))}") + ".md"


def trade_filename(trade: Mapping[str, Any]) -> str:
    return safe_filename(f"Trade {trade.get('id')} {text(trade.get('symbol'))} {text(trade.get('side'))}") + ".md"


def journal_filename(entry: Mapping[str, Any]) -> str:
    return safe_filename(f"{day_part(entry.get('created_at')) or 'Journal'} Journal {entry.get('id')} {text(entry.get('symbol'))}") + ".md"


def trades_for_session(trades: Sequence[Mapping[str, Any]], session: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    session_id = int(session["id"])
    return [trade for trade in trades if trade.get("session_id") == session_id]


def journal_for_session(entries: Sequence[Mapping[str, Any]], session: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    session_id = int(session["id"])
    return [entry for entry in entries if entry.get("session_id") == session_id]


def journal_for_trade(entries: Sequence[Mapping[str, Any]], trade: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    trade_id = int(trade["id"])
    return [entry for entry in entries if entry.get("linked_trade_id") == trade_id]


def group_by_symbol(trades: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trade in trades:
        symbol = text(trade.get("symbol"))
        if symbol:
            grouped[symbol].append(trade)
    return dict(grouped)


def group_by_day(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, list[Mapping[str, Any]]]:
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        day = day_part(item.get(key))
        if day:
            grouped[day].append(item)
    return dict(grouped)


def linked_list(filenames: Sequence[str]) -> str:
    if not filenames:
        return "_No items._\n"
    return "\n".join(f"- [[{basename_without_md(filename)}]]" for filename in filenames) + "\n"


def frontmatter(values: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is None or value == "":
            continue
        if isinstance(value, list | tuple):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return f"{value:g}"
    raw = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", raw):
        return raw
    return json.dumps(raw, ensure_ascii=False)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "Untitled")[:120]


def basename_without_md(filename: str) -> str:
    return filename[:-3] if filename.endswith(".md") else filename


def day_part(value: Any) -> str:
    raw = text(value)
    return raw[:10] if len(raw) >= 10 else ""


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def float_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def number_or_dash(value: Any) -> str:
    number = float_value(value)
    return f"{number:g}" if number is not None else "-"
