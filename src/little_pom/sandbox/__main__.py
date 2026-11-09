"""Usage: python -m little_pom.sandbox SNAPSHOT_ROOT [--inspect-docker]."""
import argparse
import json

from .preflight import preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only public-corpus resource and isolation preflight")
    parser.add_argument("snapshot_root")
    parser.add_argument("--inspect-docker", action="store_true", help="Read live Docker metadata; never build or run")
    parser.add_argument("--images", help="Explicit JSON file mapping task IDs to immutable Docker image IDs")
    args = parser.parse_args()
    images = None
    if args.images:
        with open(args.images, encoding="utf-8") as stream:
            images = json.load(stream)
    result = preflight(args.snapshot_root, inspect_docker=args.inspect_docker, images=images)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
