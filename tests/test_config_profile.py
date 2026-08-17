from pathlib import Path
import tempfile
import unittest

from kernel_modularizer.config_profile import (
    generate_tristate_module_profile,
)
from kernel_modularizer.errors import GraphValidationError


class ConfigProfileTests(unittest.TestCase):
    def make_fixture(self, directory: str):
        root = Path(directory)
        (root / "Kconfig").write_text(
            """mainmenu \"profile test\"

config MODULES
    bool \"modules\"
    option modules
    default y

config REQUIRED
    tristate \"required\"
    default y

config OPTIONAL
    tristate \"optional\"
    default y

config CHILD
    tristate \"child\"
    depends on OPTIONAL
    default y

config DISABLED
    tristate \"disabled\"
    default n
""",
            encoding="utf-8",
        )
        config = root / "base.config"
        config.write_text(
            """CONFIG_REQUIRED=y
CONFIG_MODULES=y
CONFIG_OPTIONAL=y
CONFIG_CHILD=y
# CONFIG_DISABLED is not set
""",
            encoding="utf-8",
        )
        return root, config

    def test_demotes_only_enabled_tristates_and_honors_keep(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.make_fixture(directory)
            output, report = generate_tristate_module_profile(
                root, config, keep_symbols=["CONFIG_REQUIRED"]
            )

        self.assertIn("CONFIG_REQUIRED=y", output)
        self.assertIn("CONFIG_OPTIONAL=m", output)
        self.assertIn("CONFIG_CHILD=m", output)
        self.assertIn("# CONFIG_DISABLED is not set", output)
        transitions = {
            row["symbol"]: (row["before"], row["after"])
            for row in report["transitions"]
        }
        self.assertEqual(transitions["CONFIG_OPTIONAL"], ("y", "m"))
        self.assertEqual(transitions["CONFIG_CHILD"], ("y", "m"))
        self.assertNotIn("CONFIG_DISABLED", transitions)
        self.assertEqual(
            report["summary"]["transition_counts"], {"y->m": 2}
        )

    def test_unknown_keep_symbol_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.make_fixture(directory)
            with self.assertRaisesRegex(
                GraphValidationError, "unknown keep symbol"
            ):
                generate_tristate_module_profile(
                    root, config, keep_symbols=["TYPO"]
                )


if __name__ == "__main__":
    unittest.main()
