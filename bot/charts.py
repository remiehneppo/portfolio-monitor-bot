"""Portfolio chart generation using matplotlib."""

import io
import logging
from collections import defaultdict
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np

logger = logging.getLogger(__name__)

# ── Color palette ──────────────────────────────────────────────────
COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    "#af7aa1", "#86bcb6", "#d37295", "#8cd17d", "#b6992d",
]

CHAIN_COLORS = {
    "eth": "#627eea", "bsc": "#f3ba2f", "polygon": "#8247e5",
    "arbitrum": "#28a0f0", "optimism": "#ff0420", "base": "#0052ff",
    "avalanche": "#e84142", "fantom": "#1969ff", "gnosis": "#04795b",
    "linea": "#61dfff", "scroll": "#ffeeda", "xlayer": "#000000",
    "flare": "#e42058", "xai": "#f30019", "telos": "#571aff",
    "taiko": "#e81899", "syscoin": "#0082c6", "stellar": "#000000",
}


def _parse_ts(ts_str: str) -> datetime:
    """Parse timestamp string from CSV."""
    return datetime.strptime(ts_str.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")


def _get_chain_color(chain: str) -> str:
    return CHAIN_COLORS.get(chain.lower(), "#888888")


def _normalize_timestamps(rows: list[dict], window_seconds: int = 60) -> list[dict]:
    """Group timestamps within `window_seconds` into a single canonical timestamp.

    When multiple wallets are fetched sequentially, their timestamps may differ
    by a few seconds. This function normalises them so chart aggregation works
    correctly across all wallets.
    """
    if not rows:
        return rows

    # Collect unique timestamps and sort
    ts_set = sorted({r["timestamp"] for r in rows})
    if len(ts_set) <= 1:
        return rows

    # Build mapping: original_ts -> canonical_ts (first ts in the group)
    ts_map: dict[str, str] = {}
    group_start = ts_set[0]
    ts_map[ts_set[0]] = group_start
    for ts in ts_set[1:]:
        diff = (_parse_ts(ts) - _parse_ts(group_start)).total_seconds()
        if abs(diff) <= window_seconds:
            ts_map[ts] = group_start  # same group
        else:
            group_start = ts
            ts_map[ts] = group_start

    # Apply mapping
    normalised = []
    for r in rows:
        nr = dict(r)
        nr["timestamp"] = ts_map.get(r["timestamp"], r["timestamp"])
        normalised.append(nr)
    return normalised


def generate_wallet_chart(
    rows: list[dict],
    wallet_address: str | None = None,
    title_suffix: str = "",
) -> io.BytesIO | None:
    """
    Generate a 3-panel chart:
      1. Token allocation by chain (horizontal bar)
      2. Token details per chain (grouped bar)
      3. Total portfolio USD value over time
    """
    if wallet_address:
        rows = [r for r in rows if r["wallet_address"].lower() == wallet_address.lower()]

    if not rows:
        return None

    # Normalise timestamps so all wallets in same refresh round share one ts
    rows = _normalize_timestamps(rows)

    # ── Group data by timestamp ────────────────────────────────────
    snapshot_data: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    chain_data: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    total_usd_by_ts: dict[str, float] = {}

    timestamps_set: set[str] = set()
    for r in rows:
        ts = r["timestamp"]
        timestamps_set.add(ts)
        sym = r["token_symbol"]
        chain = r.get("blockchain", "unknown")
        usd = float(r.get("balance_usd", 0) or 0)
        snapshot_data[ts][sym] += usd
        chain_data[ts][chain] += usd

    for ts in timestamps_set:
        total_usd_by_ts[ts] = sum(snapshot_data[ts].values())

    timestamps_sorted = sorted(timestamps_set)
    dates = [_parse_ts(ts) for ts in timestamps_sorted]
    last_ts = timestamps_sorted[-1]

    if len(dates) < 1:
        return None

    # ── Latest snapshot data ───────────────────────────────────────
    # Per-chain totals
    chain_totals = sorted(chain_data[last_ts].items(), key=lambda x: x[1], reverse=True)
    # Per-chain per-token detail
    token_by_chain: dict[str, list[dict]] = defaultdict(list)
    latest_rows = [r for r in rows if r["timestamp"] == last_ts]
    for r in latest_rows:
        chain = r.get("blockchain", "unknown")
        usd = float(r.get("balance_usd", 0) or 0)
        if usd > 0:
            token_by_chain[chain].append({
                "symbol": r["token_symbol"],
                "balance": float(r.get("balance", 0) or 0),
                "usd": usd,
            })
    for chain in token_by_chain:
        token_by_chain[chain].sort(key=lambda x: x["usd"], reverse=True)

    # ── Build figure ───────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(2, 2, height_ratios=[3, 2], hspace=0.4, wspace=0.35)

    ax_chain = fig.add_subplot(gs[0, 0])  # top-left: chain allocation donut
    ax_tokens = fig.add_subplot(gs[0, 1])  # top-right: token per chain bar
    ax_total = fig.add_subplot(gs[1, :])   # bottom: total value timeline

    # ── Panel 1: Chain allocation (donut chart) ────────────────────
    if chain_totals:
        chains = [c[0] for c in chain_totals]
        values = [c[1] for c in chain_totals]
        colors = [_get_chain_color(c) for c in chains]

        wedges, texts, autotexts = ax_chain.pie(
            values, colors=colors, autopct="",
            startangle=90, pctdistance=0.8,
            wedgeprops=dict(width=0.45, edgecolor="white", linewidth=1.5),
        )

        # Use legend instead of labels to avoid overlap
        legend_labels = [
            f"{c.upper()}  ${v:,.2f}  ({v / sum(values) * 100:.1f}%)"
            for c, v in zip(chains, values)
        ]
        ax_chain.legend(
            wedges, legend_labels, loc="center left",
            bbox_to_anchor=(-0.3, 0.5), fontsize=8, frameon=False,
        )
        total_val = sum(values)
        ax_chain.text(0, 0, f"${total_val:,.2f}", ha="center", va="center",
                      fontsize=13, fontweight="bold")
        ax_chain.set_title("Value by Chain", fontweight="bold", fontsize=12, pad=15)
    else:
        ax_chain.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=14)

    # ── Panel 2: Top tokens per chain (horizontal grouped bar) ────
    bar_entries = []  # (label, usd, chain_color, balance_str)
    for chain, _ in chain_totals:
        tokens = token_by_chain.get(chain, [])[:5]  # top 5 per chain
        for t in tokens:
            bal = t["balance"]
            bal_str = f"{bal:,.4f}" if bal < 1 else f"{bal:,.2f}" if bal < 1000 else f"{bal:,.0f}"
            bar_entries.append((
                f"{t['symbol']} ({chain})",
                t["usd"],
                _get_chain_color(chain),
                bal_str,
            ))

    # Limit to top 15 overall and reverse for bottom-to-top display
    bar_entries.sort(key=lambda x: x[1], reverse=True)
    bar_entries = bar_entries[:15]
    bar_entries.reverse()

    if bar_entries:
        labels = [e[0] for e in bar_entries]
        usd_vals = [e[1] for e in bar_entries]
        bar_colors = [e[2] for e in bar_entries]
        bal_strs = [e[3] for e in bar_entries]

        y_pos = np.arange(len(labels))
        bars = ax_tokens.barh(y_pos, usd_vals, color=bar_colors, edgecolor="white", height=0.7)
        ax_tokens.set_yticks(y_pos)
        ax_tokens.set_yticklabels(labels, fontsize=8)
        ax_tokens.set_title("Top Tokens by Chain", fontweight="bold", fontsize=12)
        ax_tokens.set_xlabel("USD Value")
        ax_tokens.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

        for bar, usd, bal_s in zip(bars, usd_vals, bal_strs):
            ax_tokens.text(
                bar.get_width(), bar.get_y() + bar.get_height() / 2,
                f"  ${usd:,.2f}  ({bal_s})", va="center", fontsize=7,
            )
    else:
        ax_tokens.text(0.5, 0.5, "No token data", ha="center", va="center", fontsize=14,
                       transform=ax_tokens.transAxes)

    # ── Panel 3: Total USD over time ──────────────────────────────
    totals = [total_usd_by_ts[ts] for ts in timestamps_sorted]

    if len(dates) == 1:
        ax_total.bar(dates, totals, color="#4e79a7", width=0.02)
        ax_total.bar_label(ax_total.containers[0], fmt="$%.2f", fontsize=10)
    else:
        ax_total.fill_between(dates, totals, alpha=0.3, color="#4e79a7")
        ax_total.plot(dates, totals, color="#4e79a7", linewidth=2, marker="o", markersize=4)
        for d, v in zip(dates, totals):
            ax_total.annotate(f"${v:,.2f}", (d, v), textcoords="offset points",
                              xytext=(0, 8), ha="center", fontsize=7)

    ax_total.set_title("Total Portfolio Value (USD)", fontweight="bold", fontsize=12)
    ax_total.set_ylabel("USD")
    ax_total.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax_total.tick_params(axis="x", rotation=30)
    ax_total.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    # ── Suptitle ───────────────────────────────────────────────────
    addr_label = wallet_address[:8] + "..." + wallet_address[-6:] if wallet_address else "All Wallets"
    fig.suptitle(f"Portfolio: {addr_label} {title_suffix}", fontsize=14, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.95])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_token_breakdown_chart(
    rows: list[dict],
    wallet_address: str | None = None,
) -> io.BytesIO | None:
    """
    Generate a breakdown chart with 3 rows:
      Row 1: Current token quantity (left) + USD value (right) — colored by chain
      Row 2: Top token USD values over time (line chart)
      Row 3: Per-chain total USD over time (stacked area)
    """
    if wallet_address:
        rows = [r for r in rows if r["wallet_address"].lower() == wallet_address.lower()]

    if not rows:
        return None

    # Normalise timestamps so all wallets in same refresh round share one ts
    rows = _normalize_timestamps(rows)

    # ── Collect all timestamps ─────────────────────────────────────
    timestamps_set: set[str] = set()
    for r in rows:
        timestamps_set.add(r["timestamp"])
    timestamps_sorted = sorted(timestamps_set)
    dates = [_parse_ts(ts) for ts in timestamps_sorted]
    latest_ts = timestamps_sorted[-1]
    latest_rows = [r for r in rows if r["timestamp"] == latest_ts]

    # ── Aggregate latest snapshot by (token, chain) ────────────────
    token_data: dict[str, dict] = {}
    for r in latest_rows:
        sym = r["token_symbol"]
        chain = r.get("blockchain", "?")
        key = f"{sym} ({chain})"
        if key not in token_data:
            token_data[key] = {
                "balance": 0.0, "balance_usd": 0.0,
                "chain": chain, "symbol": sym,
            }
        token_data[key]["balance"] += float(r.get("balance", 0) or 0)
        token_data[key]["balance_usd"] += float(r.get("balance_usd", 0) or 0)

    if not token_data:
        return None

    # Top tokens for bar charts
    sorted_tokens = sorted(token_data.items(), key=lambda x: x[1]["balance_usd"], reverse=True)[:15]
    sorted_tokens_rev = list(reversed(sorted_tokens))  # bottom-to-top for barh

    labels = [t[0] for t in sorted_tokens_rev]
    usd_vals = [t[1]["balance_usd"] for t in sorted_tokens_rev]
    balances = [t[1]["balance"] for t in sorted_tokens_rev]
    bar_colors = [_get_chain_color(t[1]["chain"]) for t in sorted_tokens_rev]

    # ── Time-series data: per-token(chain) USD over time ───────────
    # key -> {ts: usd}
    token_ts_usd: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    chain_ts_usd: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        ts = r["timestamp"]
        sym = r["token_symbol"]
        chain = r.get("blockchain", "?")
        key = f"{sym} ({chain})"
        usd = float(r.get("balance_usd", 0) or 0)
        token_ts_usd[key][ts] += usd
        chain_ts_usd[chain][ts] += usd

    # Pick top 8 tokens (by latest USD) for line chart readability
    top_keys = [t[0] for t in sorted_tokens[:8]]

    has_history = len(dates) > 1

    # ── Build figure ───────────────────────────────────────────────
    n_rows = 3 if has_history else 1
    height_ratios = [3, 2, 2] if has_history else [1]
    fig_h = 16 if has_history else max(5, len(labels) * 0.55)

    fig, axes = plt.subplots(
        n_rows, 2, figsize=(15, fig_h),
        gridspec_kw={"height_ratios": height_ratios, "wspace": 0.45, "hspace": 0.4},
    )
    if n_rows == 1:
        axes = np.array([axes])  # ensure 2D

    ax_qty = axes[0, 0]
    ax_usd = axes[0, 1]

    # ── Row 1 Left: Token quantity ─────────────────────────────────
    y_pos = np.arange(len(labels))
    bars1 = ax_qty.barh(y_pos, balances, color=bar_colors, edgecolor="white", height=0.7)
    ax_qty.set_yticks(y_pos)
    ax_qty.set_yticklabels(labels, fontsize=8)
    ax_qty.set_title("Token Quantity (Latest)", fontweight="bold")
    ax_qty.set_xlabel("Amount")
    for bar, val in zip(bars1, balances):
        lbl = f"{val:,.4f}" if val < 1 else f"{val:,.2f}" if val < 1000 else f"{val:,.0f}"
        ax_qty.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                    f" {lbl}", va="center", fontsize=7)

    # ── Row 1 Right: USD value ─────────────────────────────────────
    bars2 = ax_usd.barh(y_pos, usd_vals, color=bar_colors, edgecolor="white", height=0.7)
    ax_usd.set_yticks(y_pos)
    ax_usd.set_yticklabels(labels, fontsize=8)
    ax_usd.set_title("USD Value (Latest)", fontweight="bold")
    ax_usd.set_xlabel("USD")
    ax_usd.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    for bar, val in zip(bars2, usd_vals):
        ax_usd.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                    f" ${val:,.2f}", va="center", fontsize=7)

    # ── Row 2 & 3: Time-series charts (only if multiple snapshots) ─
    if has_history:
        ax_tok_line = axes[1, 0]
        ax_tok_line2 = axes[1, 1]
        ax_chain_area = axes[2, 0]
        ax_chain_area2 = axes[2, 1]

        # ── Row 2: Top token USD over time (split into 2 panels) ───
        half = (len(top_keys) + 1) // 2
        left_keys = top_keys[:half]
        right_keys = top_keys[half:]

        for ax, keys in [(ax_tok_line, left_keys), (ax_tok_line2, right_keys)]:
            for i, key in enumerate(keys):
                ts_map = token_ts_usd[key]
                vals = [ts_map.get(ts, 0) for ts in timestamps_sorted]
                color = _get_chain_color(token_data.get(key, {}).get("chain", ""))
                ax.plot(dates, vals, linewidth=1.8, marker="o", markersize=3,
                        label=key, color=COLORS[i % len(COLORS)])
                # annotate last point
                if vals:
                    ax.annotate(f"${vals[-1]:,.2f}", (dates[-1], vals[-1]),
                                textcoords="offset points", xytext=(5, 3),
                                fontsize=6, color=COLORS[i % len(COLORS)])

            ax.set_title("Token USD Over Time", fontweight="bold", fontsize=10)
            ax.set_ylabel("USD")
            ax.legend(fontsize=7, loc="upper left", framealpha=0.7)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
            ax.tick_params(axis="x", rotation=30, labelsize=7)
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
            ax.grid(True, alpha=0.3)

        if not right_keys:
            ax_tok_line2.set_visible(False)

        # ── Row 3 Left: Per-chain stacked area ─────────────────────
        sorted_chains = sorted(
            chain_ts_usd.keys(),
            key=lambda c: chain_ts_usd[c].get(latest_ts, 0),
            reverse=True,
        )
        chain_values = {}
        for chain in sorted_chains:
            chain_values[chain] = [chain_ts_usd[chain].get(ts, 0) for ts in timestamps_sorted]

        ax_chain_area.stackplot(
            dates,
            *[chain_values[c] for c in sorted_chains],
            labels=[c.upper() for c in sorted_chains],
            colors=[_get_chain_color(c) for c in sorted_chains],
            alpha=0.8,
        )
        ax_chain_area.set_title("USD by Chain Over Time", fontweight="bold", fontsize=10)
        ax_chain_area.set_ylabel("USD")
        ax_chain_area.legend(fontsize=7, loc="upper left", framealpha=0.7)
        ax_chain_area.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        ax_chain_area.tick_params(axis="x", rotation=30, labelsize=7)
        ax_chain_area.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax_chain_area.grid(True, alpha=0.3)

        # ── Row 3 Right: Per-chain individual lines ────────────────
        for chain in sorted_chains:
            vals = chain_values[chain]
            ax_chain_area2.plot(
                dates, vals, linewidth=2, marker="o", markersize=3,
                label=chain.upper(), color=_get_chain_color(chain),
            )
            if vals:
                ax_chain_area2.annotate(
                    f"${vals[-1]:,.2f}", (dates[-1], vals[-1]),
                    textcoords="offset points", xytext=(5, 3),
                    fontsize=6, color=_get_chain_color(chain),
                )
        ax_chain_area2.set_title("Per-Chain Value Over Time", fontweight="bold", fontsize=10)
        ax_chain_area2.set_ylabel("USD")
        ax_chain_area2.legend(fontsize=7, loc="upper left", framealpha=0.7)
        ax_chain_area2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        ax_chain_area2.tick_params(axis="x", rotation=30, labelsize=7)
        ax_chain_area2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax_chain_area2.grid(True, alpha=0.3)

    # ── Chain legend (for bar charts) ──────────────────────────────
    seen_chains = {}
    for t in sorted_tokens:
        c = t[1]["chain"]
        if c not in seen_chains:
            seen_chains[c] = _get_chain_color(c)
    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, fc=color, ec="white")
        for color in seen_chains.values()
    ]
    fig.legend(legend_handles, [c.upper() for c in seen_chains.keys()],
               loc="lower center", ncol=min(len(seen_chains), 8),
               fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.01))

    addr_label = wallet_address[:8] + "..." + wallet_address[-6:] if wallet_address else "All Wallets"
    fig.suptitle(f"Token Breakdown: {addr_label}", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
