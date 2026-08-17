from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("cc")
        if cls.compiler is None:
            raise unittest.SkipTest("host C compiler is absent")

    def _check_trigger(self, name):
        subprocess.run(
            [
                self.compiler,
                "-std=gnu11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fsyntax-only",
                str(PROJECT_ROOT / "Runtime/triggers" / name),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_wallclock_compat_trigger_is_warning_free(self):
        self._check_trigger("wallclock_trigger.c")

    def test_eight_module_composite_trigger_is_warning_free(self):
        self._check_trigger("eight_modules_trigger.c")

    def test_v33_source_closure_trigger_is_warning_free(self):
        self._check_trigger("v33_core_modules_trigger.c")

    def test_v34_select_trigger_is_warning_free(self):
        self._check_trigger("v34_select_trigger.c")

    def test_io_uring_trigger_is_warning_free(self):
        self._check_trigger("io_uring_trigger.c")

    def test_perf_event_trigger_is_warning_free(self):
        self._check_trigger("perf_event_trigger.c")

    def test_tracefs_callback_trigger_is_warning_free(self):
        self._check_trigger("tracefs_callback_trigger.c")

    def test_v40_callback_trigger_is_warning_free(self):
        self._check_trigger("v40_callback_trigger.c")

    def test_v41_callback_trigger_is_warning_free(self):
        self._check_trigger("v41_callback_trigger.c")

    def test_v42_callback_trigger_is_warning_free(self):
        self._check_trigger("v42_callback_trigger.c")

    def test_v43_callback_trigger_is_warning_free(self):
        self._check_trigger("v43_callback_trigger.c")


if __name__ == "__main__":
    unittest.main()
