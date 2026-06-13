# Host-Tests fuer sensors.read_all (pure Python, MicroPython-Module gestubt).
#
# Ausfuehren:  python3 -m unittest discover -s tests/firmware
#
# Kernregel: ein flackernder Fuehler (CRC-Fehler -> onewire.OneWireError, die
# WEDER von OSError NOCH ValueError erbt) darf read_all NIE crashen. Sonst
# faengt der Top-Level-Handler in main.py die Exception, schreibt ein leeres
# "CRASH:" und schlaeft 60 s — die Mess-/Sende-Logik wird nie erreicht und das
# Dashboard bleibt leer (real beobachtet, Log 11:05:37 ff.).
import os
import sys
import types
import unittest

# MicroPython-only Module stubben, damit sensors.py auf dem Host importierbar ist.
sys.modules.setdefault("machine", types.ModuleType("machine"))
_logbuf = types.ModuleType("logbuf")
_logbuf.log = lambda *a, **k: None
sys.modules.setdefault("logbuf", _logbuf)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "internal", "assets", "firmware"))

import sensors  # noqa: E402


class _OneWireError(Exception):
    """Stellt onewire.OneWireError nach: erbt NICHT von OSError/ValueError,
    und der echte Treiber wirft sie bei CRC-Fehlern OHNE Message (str == "")."""


class TestReadAllIsolation(unittest.TestCase):
    def setUp(self):
        self._saved = dict(sensors.READERS)

    def tearDown(self):
        sensors.READERS.clear()
        sensors.READERS.update(self._saved)

    def _cfg(self, *types_):
        return {"sensors": [{"sensor_id": "s-%d" % i, "type": t} for i, t in enumerate(types_)]}

    def test_onewire_crc_error_does_not_propagate(self):
        def boom(_cfg):
            raise _OneWireError  # bare -> leere Message, wie der echte Treiber
        sensors.READERS["boom"] = boom
        # Darf NICHT werfen — vor dem Fix fliegt _OneWireError hier raus.
        self.assertEqual(sensors.read_all(self._cfg("boom")), [])

    def test_flaky_sensor_does_not_kill_healthy_one(self):
        sensors.READERS["boom"] = lambda _c: (_ for _ in ()).throw(_OneWireError())
        sensors.READERS["good"] = lambda _c: 184
        result = sensors.read_all(self._cfg("boom", "good"))
        self.assertEqual(result, [("s-1", 184)])

    def test_oserror_still_handled(self):
        sensors.READERS["io"] = lambda _c: (_ for _ in ()).throw(OSError("bus"))
        self.assertEqual(sensors.read_all(self._cfg("io")), [])

    def test_none_reading_skipped(self):
        sensors.READERS["empty"] = lambda _c: None
        self.assertEqual(sensors.read_all(self._cfg("empty")), [])

    def test_value_passed_through(self):
        sensors.READERS["ok"] = lambda _c: 225
        self.assertEqual(sensors.read_all(self._cfg("ok")), [("s-0", 225)])


if __name__ == "__main__":
    unittest.main()
