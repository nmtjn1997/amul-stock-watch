"""Every test runs against a throwaway home directory, never the user's real config."""

import os
import tempfile

_HOME = tempfile.mkdtemp(prefix="amul-watch-test-")
os.environ["AMUL_WATCH_HOME"] = _HOME
