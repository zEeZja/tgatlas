"""Run the required PostgreSQL suite; a missing database is a failure, not a skip."""

import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    if not os.getenv("ATLAS_TEST_DSN"):
        print(
            "Set ATLAS_TEST_DSN explicitly to a disposable PostgreSQL database.",
            file=sys.stderr,
        )
        return 2
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests/integration"), top_level_dir=str(ROOT)
    )
    if not suite.countTestCases():
        print("No PostgreSQL tests discovered.", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
