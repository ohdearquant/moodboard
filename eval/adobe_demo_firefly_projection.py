"""Project the exact frozen Firefly web/Khive evidence into the offline viewer bridge."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from moodboard.firefly_viewer import compile_viewer_firefly_bridge, write_viewer_firefly_bridge

ROOT = Path(__file__).resolve().parents[1]
REVISION = "moodboard.adobe-demo-firefly-frozen-projection.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        type=Path,
        default=ROOT / "viewer" / "src" / "generated" / "firefly-bridge.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    firefly = ROOT / ".cache" / "adobe-demo-firefly-v1"
    khive = ROOT / ".cache" / "adobe-demo-firefly-khive-v1" / "evidence"
    bridge = compile_viewer_firefly_bridge(
        replace_evidence=firefly / "evidence.json",
        restyle_evidence=firefly / "restyle-evidence.json",
        khive_evidence=khive / "04-firefly-verification.summary.json",
        raw_output=firefly / "replace-gemini25-iteration-02.png",
        selected_output=firefly / "replace-gemini25-iteration-02-cutout-composite-v5.png",
        restyle_output=firefly / "restyle-gemini25-iteration-01.png",
        ingest_command=khive / "01-firefly-ingest.command.json",
        ingest_results=khive / "01-firefly-ingest.results.jsonl",
        search_command=khive / "02-firefly-search.command.json",
        search_results=khive / "02-firefly-search.results.jsonl",
        restart_command=khive / "03-firefly-restart-search.command.json",
        restart_results=khive / "03-firefly-restart-search.results.jsonl",
        projection_revision=REVISION,
        projection_sha256=_sha256(Path(__file__)),
    )
    if args.write.exists():
        raise SystemExit(f"refusing to overwrite existing bridge: {args.write}")
    write_viewer_firefly_bridge(bridge, args.write)
    print(f"bridge_id={bridge['bridge_id']} path={args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
