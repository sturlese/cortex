"""build-graph CLI. Critical paths are required -> fail fast (no silent defaults).

Separate pipeline stage (post-clean, pre-serve): it does NOT touch clean's brain-md
(single-writer); it writes a derived, regenerable layer to brain-md-graphed.
"""
import argparse
import os
import sys

from graph.build import build_graph


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="graph", description="build-graph: brain-md -> brain-md-graphed (entity nodes + wikilinks)"
    )
    p.add_argument("--in", dest="in_dir", required=True, help="input brain-md (from clean). Required.")
    p.add_argument("--out", dest="out_dir", required=True, help="output brain-md-graphed. Required.")
    p.add_argument("--min-mentions", type=int, default=2,
                   help="drop entities with fewer than N total mentions (default 2; 1 = keep all)")
    p.add_argument("--registry", default=None,
                   help="entity-registry.json (curated identity; see graph/registry.py). Optional.")
    args = p.parse_args(argv)

    if not os.path.isdir(args.in_dir):
        print(f"ERROR: --in does not exist or is not a directory: {args.in_dir}", file=sys.stderr)
        return 2
    os.makedirs(args.out_dir, exist_ok=True)

    from graph.registry import load_registry
    registry = load_registry(args.registry)
    stats = build_graph(args.in_dir, args.out_dir, min_mentions=args.min_mentions, registry=registry)
    print(f"build-graph: {stats['docs']} docs · {stats['entities']} entities "
          f"(from {stats['mentions_raw']} mentions) · {stats['by_type']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
