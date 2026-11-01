"""Streamlit dashboard: presentation only. All numbers come from src/dashboard/view_model.py.

Run: python tasks.py app   (or: streamlit run app.py)
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from src.dashboard import charts
from src.dashboard import view_model as vm

st.set_page_config(
    page_title="AWS Cost Advisor (synthetic)", page_icon=":bar_chart:", layout="wide"
)


@st.cache_resource(show_spinner="Analysing synthetic resources...")
def data() -> vm.DashboardData:
    return vm.build()


def theme() -> charts.Theme:
    try:
        return charts.theme_for(st.context.theme.type)
    except AttributeError:
        return charts.LIGHT


def money(x: float | None) -> str:
    return (
        "Cannot estimate" if x is None or (isinstance(x, float) and math.isnan(x)) else f"${x:,.2f}"
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_overview() -> None:
    d = data()
    o = vm.overview(d)
    st.title("Overview")
    c1, c2, c3 = st.columns(3)
    c1.metric("Estimated monthly compute spend", money(o.total_monthly_cost_usd))
    c1.caption(
        f"Priced resources only. {o.unpriced_resources} unpriced resources excluded, "
        "not counted as $0."
    )
    c2.metric("Resources analysed", o.resources_analyzed)
    c2.caption(f"{o.resources_rejected} records rejected by validation.")
    c3.metric("Estimated monthly savings (actionable)", money(o.estimated_monthly_savings_usd))
    c3.caption(
        f"Larger estimate per resource, no double counting. {money(o.pending_review_savings_usd)} "
        "more is pending human review."
    )
    c4, c5, c6 = st.columns(3)
    c4.metric("Actionable recommendations", o.actionable_recommendations)
    c4.caption(f"{o.recommendations_without_estimate} recommendations have no savings estimate.")
    c5.metric("Needs human review", o.needs_review)
    c6.metric("Resources with insufficient data", o.insufficient_data)

    st.subheader("Decisions by rule")
    st.dataframe(vm.outcome_counts(d), width="stretch")

    st.subheader("Estimated monthly cost by service")
    st.plotly_chart(
        charts.cost_bar(vm.breakdown(d, "Service"), "service", theme()), width="stretch"
    )

    with st.expander("Estimated vs simulated vs realized savings"):
        st.markdown(
            "- **Estimated** (shown here): computed by formula under stated assumptions.\n"
            "- **Simulated**: would come from replaying a synthetic scenario after applying "
            "the action. Not implemented.\n"
            "- **Realized**: measured on real bills after the change. **Not available** in "
            "this project (no real AWS data)."
        )


def page_cost() -> None:
    d = data()
    st.title("Cost analysis")
    label = st.radio("Break down by", list(vm.BREAKDOWN_DIMENSIONS), horizontal=True)
    df = vm.breakdown(d, label)
    col = vm.BREAKDOWN_DIMENSIONS[label]
    st.plotly_chart(charts.cost_bar(df, col, theme()), width="stretch")
    st.dataframe(
        df.rename(columns={col: label}),
        width="stretch",
        hide_index=True,
        column_config={
            "monthly_cost_usd": st.column_config.NumberColumn(
                "Estimated monthly cost", format="$%.2f"
            ),
            "resources": "Resources",
            "unpriced_resources": "Unpriced (excluded)",
        },
    )
    if d.summary.unpriced_ids:
        st.info(
            "Unpriced resources (cost unknown, excluded from totals): "
            + ", ".join(d.summary.unpriced_ids)
        )
    st.caption(
        "Compute only: excludes storage, data transfer, tax, credits and discounts. "
        "Monthly = hourly price x 730 h x running fraction. Prices are illustrative."
    )


def page_recommendations() -> None:
    d = data()
    st.title("Recommendations")
    table = vm.recommendations_table(d)
    if table.empty:
        st.info("No recommendations.")
        return

    f1, f2, f3, f4 = st.columns(4)
    cats = f1.multiselect("Category", sorted(table["category"].unique()))
    outs = f2.multiselect("Outcome", sorted(table["outcome"].unique()))
    quals = f3.multiselect("Evidence quality", sorted(table["evidence_quality"].unique()))
    envs = f4.multiselect("Environment", sorted(table["environment"].unique()))
    shown = vm.filter_recommendations(table, cats, outs, quals, envs)
    st.caption(
        f"{len(shown)} of {len(table)} recommendations. "
        "Sorted by evidence, then risk, then savings."
    )

    display = shown.assign(savings=shown["estimated_monthly_savings_usd"].map(money))
    st.dataframe(
        display[
            [
                "rank",
                "resource_id",
                "category",
                "outcome",
                "evidence_quality",
                "risk",
                "savings",
                "environment",
                "instance_type",
                "proposed_instance_type",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={"savings": "Estimated savings / month"},
    )
    if shown.empty:
        return

    st.subheader("Evidence drill-down")
    choice = st.selectbox(
        "Recommendation",
        shown["id"].tolist(),
        format_func=lambda i: f"#{int(shown.loc[shown.id == i, 'rank'].iloc[0])}  {i}",
    )
    a = vm.assessment_by_id(d, choice)
    r = d.resources_by_id[a.resource_id]
    row = shown[shown.id == choice].iloc[0]

    st.markdown(
        f"**{a.outcome}** · evidence **{a.evidence_quality}** · risk **{a.risk_level or '-'}**"
    )
    if a.human_review_required:
        st.warning("Human review required: this is not an automatic action.")
    st.write(a.problem)
    st.caption(f"Rule: {a.rule_triggered}")
    st.caption(f"Rank: {row['rank_reason']}")

    left, right = st.columns([1, 2])
    left.dataframe(vm.evidence_rows(a), hide_index=True, width="stretch")
    with right:
        if r.cpu_hourly is None:
            st.info("No CPU metrics for this resource.")
        else:
            lines, windows = vm.chart_references(d, a)
            st.plotly_chart(
                charts.cpu_line(vm.cpu_series(r), lines, windows, theme()), width="stretch"
            )
            note = (
                "Dashed lines: rule limits."
                if lines
                else "Shaded: scheduled on-hours (Mon-Fri 09:00-19:00 UTC); the rest is off-hours."
            )
            st.caption(f"Hourly CPU (synthetic). Gaps are missing data, not 0%. {note}")

    st.markdown("**Estimated savings**")
    st.dataframe(vm.savings_rows(a), hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    c1.markdown("**Risks**\n" + "\n".join(f"- {x}" for x in a.risks))
    c1.markdown("**Missing information**\n" + "\n".join(f"- {x}" for x in a.missing_info))
    c2.markdown("**Assumptions**\n" + "\n".join(f"- {x}" for x in a.assumptions))
    c2.markdown(f"**Next step**\n\n{a.next_step}")

    with st.expander("All decisions, including NO_ACTION and INSUFFICIENT_DATA"):
        st.dataframe(vm.all_assessments_table(d), hide_index=True, width="stretch")


def page_evaluation() -> None:
    st.title("Evaluation")
    report = vm.read_text(vm.REPORT_PATH)
    if report is None:
        st.error("No evaluation report found. Run `python tasks.py evaluate`.")
        return
    st.caption(
        "Rendered from reports/evaluation_report.md (generated by `python tasks.py evaluate`)."
    )
    st.markdown(report)


def page_settings() -> None:
    d = data()
    st.title("Settings")
    st.write(
        "Thresholds are read-only here. They live in `config/thresholds.yaml`; changing them "
        "must be logged in docs/EVALUATION.md, so the dashboard does not offer sliders."
    )
    rs, sd, dq = (
        d.thresholds.rightsizing,
        d.thresholds.scheduled_shutdown,
        d.thresholds.data_quality,
    )
    rows = {
        "Rightsizing: max mean CPU %": rs.max_mean_cpu_pct,
        "Rightsizing: max p95 CPU %": rs.max_p95_cpu_pct,
        "Rightsizing: review if projected peak >= %": rs.review_if_projected_peak_pct,
        "Shutdown: review if off-hours mean CPU > %": sd.review_if_off_hours_cpu_pct,
        "Gate: min days": dq.min_coverage_days,
        "Gate: min completeness": dq.min_completeness,
        "Gate: max staleness h": dq.max_staleness_hours,
    }
    st.dataframe(
        pd.DataFrame({"Setting": list(rows), "Value": [str(v) for v in rows.values()]}),
        hide_index=True,
        width="stretch",
    )
    with st.expander("config/thresholds.yaml"):
        st.code(vm.read_text(vm.THRESHOLDS_PATH) or "", language="yaml")


# ---------------------------------------------------------------------------

PAGES = {
    "overview": page_overview,
    "cost": page_cost,
    "recommendations": page_recommendations,
    "evaluation": page_evaluation,
    "settings": page_settings,
}


def main() -> None:
    st.warning(vm.SYNTHETIC_BANNER, icon="⚠️")
    nav = st.navigation(
        [
            st.Page(page_overview, title="Overview", default=True),
            st.Page(page_cost, title="Cost analysis", url_path="cost"),
            st.Page(page_recommendations, title="Recommendations", url_path="recommendations"),
            st.Page(page_evaluation, title="Evaluation", url_path="evaluation"),
            st.Page(page_settings, title="Settings", url_path="settings"),
        ]
    )
    nav.run()


if __name__ == "__main__":
    main()
