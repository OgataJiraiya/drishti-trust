"""Export latest checkpoint plus public verification material."""
import argparse, json
from pathlib import Path
from drishti_sdk import DrishtiClient, DrishtiError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with DrishtiClient(args.base_url) as client:
            bundle = client._request("GET", "/api/audit/checkpoints/latest")
            key = client._request("GET", "/api/audit/checkpoints/public-key")
    except DrishtiError as exc:
        print(f"Checkpoint export failed: {exc}"); return 2
    material = {"checkpoint_bundle": bundle, "verification_key": key}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(material, indent=2, sort_keys=True) + "\n")
    print(f"Exported externally retainable public verification material to {args.output}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
