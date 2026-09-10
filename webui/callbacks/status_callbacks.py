"""Per-session counters.

The agent status table moved to the pipeline board and the refresh governor
was retired: intervals no longer need switching on and off now that the
server-sent pulse says when something changed.
"""

from dash import Input, Output

from webui.utils.state import app_state


def register_status_callbacks(app):
    """Register the session counter callbacks."""

    @app.callback(
        [Output("tool-calls-text", "children"),
         Output("llm-calls-text", "children"),
         Output("reports-text", "children")],
        [Input("refresh-interval", "n_intervals")]
    )
    def update_progress_stats(n_intervals):
        """Update the progress statistics"""
        return (
            f"🧰 Tool Calls: {app_state.tool_calls_count}",
            f"🤖 LLM Calls: {app_state.llm_calls_count}",
            f"📊 Generated Reports: {app_state.generated_reports_count}"
        )
