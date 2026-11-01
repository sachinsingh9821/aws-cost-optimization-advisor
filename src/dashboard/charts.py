"""Plotly figure builders (presentation only; data comes from view_model).

Palette: single-series charts use categorical slot 1 (blue), validated against the
light (#fcfcfb) and dark (#1a1a19) chart surfaces. Dark mode uses its own step.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go


@dataclass(frozen=True)
class Theme:
    surface: str
    ink: str
    ink_secondary: str
    muted: str
    grid: str
    baseline: str
    series: str
    reference: str  # threshold reference lines


LIGHT = Theme(
    "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#2a78d6", "#52514e"
)
DARK = Theme("#1a1a19", "#f0efec", "#c3c2b7", "#898781", "#2c2c2a", "#383835", "#3987e5", "#c3c2b7")


def theme_for(mode: str | None) -> Theme:
    return DARK if mode == "dark" else LIGHT


def _layout(fig: go.Figure, t: Theme, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 24, "t": 8, "b": 8},
        paper_bgcolor=t.surface,
        plot_bgcolor=t.surface,
        font={"color": t.ink_secondary, "size": 13},
        hoverlabel={"bgcolor": t.surface, "font": {"color": t.ink}},
        showlegend=False,
    )
    return fig


def cost_bar(df: pd.DataFrame, dimension_col: str, t: Theme = LIGHT) -> go.Figure:
    """Horizontal bars of estimated monthly cost, largest on top.

    Groups whose every price is unknown have NaN cost: they get no bar (never a 0 bar)
    and are listed in the accompanying table instead.
    """
    priced = df.dropna(subset=["monthly_cost_usd"]).sort_values("monthly_cost_usd")
    fig = go.Figure(
        go.Bar(
            x=priced["monthly_cost_usd"],
            y=priced[dimension_col].astype(str),
            orientation="h",
            marker={"color": t.series, "line": {"color": t.surface, "width": 2}},
            text=[f"${v:,.0f}" for v in priced["monthly_cost_usd"]],
            textposition="outside",
            textfont={"color": t.ink_secondary},
            cliponaxis=False,
            customdata=priced[["resources", "unpriced_resources"]].to_numpy(),
            hovertemplate=(
                "<b>%{y}</b><br>Estimated monthly cost: $%{x:,.2f}"
                "<br>Resources: %{customdata[0]} (unpriced: %{customdata[1]})<extra></extra>"
            ),
        )
    )
    fig.update_xaxes(
        title_text="Estimated monthly compute cost (USD, illustrative prices)",
        gridcolor=t.grid,
        zerolinecolor=t.baseline,
        tickprefix="$",
        color=t.muted,
    )
    fig.update_yaxes(color=t.ink_secondary)
    return _layout(fig, t, height=max(220, 36 * len(priced) + 80))


def cpu_line(
    series: pd.DataFrame,
    thresholds: list[tuple[str, float]] | None = None,
    on_windows: list[tuple[pd.Timestamp, pd.Timestamp]] | None = None,
    t: Theme = LIGHT,
) -> go.Figure:
    """Hourly CPU %. Missing hours are drawn as gaps (connectgaps=False), never as 0%.

    thresholds: dashed reference lines, labelled in the right margin so they never
    collide with the data. on_windows: scheduled on-hours, shaded behind the line.
    """
    fig = go.Figure(
        go.Scatter(
            x=series["timestamp"],
            y=series["cpu_pct"],
            mode="lines",
            line={"color": t.series, "width": 2},
            connectgaps=False,
            hovertemplate="%{x|%a %d %b %H:00} UTC<br>CPU %{y:.1f}%<extra></extra>",
        )
    )
    for start, end in on_windows or []:
        fig.add_vrect(x0=start, x1=end, fillcolor=t.grid, opacity=0.6, layer="below", line_width=0)
    for label, value in thresholds or []:
        fig.add_hline(y=value, line={"color": t.reference, "width": 1, "dash": "dash"})
        fig.add_annotation(
            x=1,
            xref="paper",
            xanchor="left",
            y=value,
            yref="y",
            showarrow=False,
            text=f"{label} {value:g}%",
            font={"color": t.ink_secondary, "size": 12},
        )
    fig.update_xaxes(gridcolor=t.grid, color=t.muted, showspikes=True, spikecolor=t.muted)
    fig.update_yaxes(
        title_text="CPU %",
        range=[0, 100],
        gridcolor=t.grid,
        zerolinecolor=t.baseline,
        color=t.muted,
    )
    fig.update_layout(hovermode="x")
    fig = _layout(fig, t, height=300)
    if thresholds:
        fig.update_layout(margin={"r": 130})
    return fig
