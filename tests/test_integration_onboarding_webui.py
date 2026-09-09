import unittest


class IntegrationOnboardingWebUITests(unittest.TestCase):
    def test_modal_exposes_guided_broker_setup(self):
        from webui.components.api_config_modal import create_api_config_modal

        rendered = str(create_api_config_modal())
        for component_id in (
            "integration-execution-broker",
            "integration-market-data-provider",
            "test-broker-connection-btn",
            "integration-health-results",
        ):
            self.assertIn(component_id, rendered)
        self.assertIn("Trading connection", rendered)
        self.assertIn("Credentials", rendered)

    def test_callbacks_register_connection_health(self):
        import dash

        from webui.callbacks.api_config_callbacks import register_api_config_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_api_config_callbacks(app)
        outputs = " ".join(app.callback_map)
        self.assertIn("integration-health-results.children", outputs)
        self.assertIn("integration-execution-broker.value", outputs)


if __name__ == "__main__":
    unittest.main()
