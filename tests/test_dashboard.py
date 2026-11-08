import unittest
import time
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from volatility_scanner.data import FyersDataProvider, SampleMarketDataProvider


class TestDashboard(unittest.TestCase):
    def test_initial_scan_and_source_switch(self):
        live = SampleMarketDataProvider().fetch_snapshot()
        live.source = "fyers"
        live.futures_price = 25100.0
        live.expiry_data = [{"date": "test expiry", "expiry": str(time.time() + 2 * 86400)}]
        with patch.object(FyersDataProvider, "__init__", return_value=None), patch(
            "volatility_scanner.live.get_live_snapshot", return_value=live
        ):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py")).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(len(app.metric), 5)
            self.assertEqual(app.session_state["scan_results"][0].futures_price, 25100.0)
            self.assertTrue(app.number_input[5].disabled)
            self.assertAlmostEqual(app.number_input[5].value, 2.0, places=3)

            app.checkbox[0].uncheck().run()
            app.number_input[2].set_value(0.30)
            app.button[0].click().run()
            for quote in live.option_chain:
                quote.ce_iv = quote.pe_iv = .25
            app.run()
            self.assertTrue(all(s.iv == .30 for s in app.session_state["scan_results"]))
            self.assertTrue(all(not s.qualifies for s in app.session_state["scan_results"]))

            app.selectbox[0].select("Sample market").run()
            self.assertEqual(app.session_state["scan_source"], "Sample market")
            self.assertEqual(app.session_state["scan_results"][0].futures_price, 25000.0)
            self.assertEqual(len(app.metric), 5)

            app.number_input[3].set_value(0.25)
            app.button[0].click().run()
            self.assertEqual(app.session_state["scan_results"][0].forecast_rv, 0.25)
            self.assertFalse(app.exception)

            app.number_input[2].set_value(0.30)
            app.button[0].click().run()
            self.assertTrue(all(s.iv == 0.30 for s in app.session_state["scan_results"]))

            app.number_input[4].set_value(0.0)
            app.button[0].click().run()
            self.assertTrue(all(not s.qualifies for s in app.session_state["scan_results"]))

            app.number_input[1].set_value(99999.0)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertIn("Selected strike is unavailable", app.error[0].value)
            self.assertNotIn("scan_results", app.session_state)

            app.number_input[1].set_value(25000.0)
            app.button[0].click().run()
            self.assertFalse(app.error)
            self.assertEqual(len(app.metric), 5)
