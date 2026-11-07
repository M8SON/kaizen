import os

# Keep tests from writing to the real ~/.kaizen/usage.db; tests of the usage
# log turn it back on with a temporary path.
os.environ["USAGE_LOG_ENABLED"] = "false"

# Keep tests away from the real ~/.kaizen/panel.json and run state.
import tempfile  # noqa: E402

os.environ["KAIZEN_PANEL_DIR"] = tempfile.mkdtemp(prefix="kaizen-panel-test-")
