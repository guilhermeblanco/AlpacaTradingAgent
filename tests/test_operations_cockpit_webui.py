import unittest


class OperationsCockpitWebUITests(unittest.TestCase):
    def test_panel_exposes_operational_controls(self):
        from webui.components.operations_panel import create_operations_panel

        rendered = str(create_operations_panel())
        for component_id in (
            "operations-metrics",
            "operations-workers",
            "operations-controls",
            "operations-pause-automation",
            "operations-resume-automation",
        ):
            self.assertIn(component_id, rendered)

    def test_callbacks_register(self):
        import dash

        from webui.callbacks.operations_callbacks import register_operations_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_operations_callbacks(app)
        outputs = " ".join(app.callback_map)
        self.assertIn("operations-metrics.children", outputs)
        self.assertIn("operations-action-status.children", outputs)


if __name__ == "__main__":
    unittest.main()
