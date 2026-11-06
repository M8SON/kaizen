import os

# Keep tests from writing to the real ~/.kaizen/usage.db; tests of the usage
# log turn it back on with a temporary path.
os.environ["USAGE_LOG_ENABLED"] = "false"
