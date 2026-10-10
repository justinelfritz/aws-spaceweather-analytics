import sys
from pathlib import Path

# sea_results.py imports alignment/normalization/aggregation/load_data as
# flat top-level modules (matching how the Lambda layer built from sea/'s
# own files exposes them at runtime -- see infra/stacks/api_stack.py --
# and how sea/pyproject.toml's wheel installs them for the Glue job).
# Locally, that means sea/ needs to be on sys.path for these tests to
# import lambdas.api.sea_results at all.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sea"))
