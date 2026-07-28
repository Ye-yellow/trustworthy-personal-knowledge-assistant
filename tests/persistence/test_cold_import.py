from __future__ import annotations

import subprocess
import sys


def test_persistence_package_imports_in_a_fresh_interpreter() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from trustworthy_kb.persistence import Base; print(len(Base.metadata.tables))",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 0
