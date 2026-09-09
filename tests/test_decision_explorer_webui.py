import unittest
from datetime import datetime, timezone

from tradingagents.operations.explorer import DecisionTimelineEvent


class DecisionExplorerWebUITests(unittest.TestCase):
    def test_panel_exposes_filters_and_detail(self):
        from webui.components.decision_explorer import create_decision_explorer

        rendered = str(create_decision_explorer())
        for component_id in (
            "decision-explorer-symbol",
            "decision-explorer-status",
            "decision-explorer-selection",
            "decision-explorer-detail",
        ):
            self.assertIn(component_id, rendered)

    def test_event_renderer_includes_category_and_fill_detail(self):
        from webui.callbacks.decision_explorer_callbacks import _event_row

        rendered = str(
            _event_row(
                DecisionTimelineEvent(
                    occurred_at=datetime.now(timezone.utc),
                    category="fill",
                    label="Filled 2 @ 100",
                    details={"source": "broker"},
                )
            )
        )
        self.assertIn("FILL", rendered)
        self.assertIn("source: broker", rendered)

    def test_callbacks_register(self):
        import dash

        from webui.callbacks.decision_explorer_callbacks import register_decision_explorer_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_decision_explorer_callbacks(app)
        outputs = " ".join(app.callback_map)
        self.assertIn("decision-explorer-selection.options", outputs)
        self.assertIn("decision-explorer-detail.children", outputs)


if __name__ == "__main__":
    unittest.main()
