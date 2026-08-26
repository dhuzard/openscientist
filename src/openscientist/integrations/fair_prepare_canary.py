"""Deployment canary for the pinned FAIR-VCG Mentor API contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence

from openscientist.integrations.fair_prepare import (
    DEFAULT_FAIR_PREPARE_URL,
    FAIR_PREPARE_URL_ENV,
    FairPrepareError,
    HttpFairPrepareProvider,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the FAIR-VCG upload, metadata, FAIR-score, and required "
            "template endpoints using synthetic data."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv(FAIR_PREPARE_URL_ENV) or DEFAULT_FAIR_PREPARE_URL,
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.attempts < 1:
        print("--attempts must be at least 1", file=sys.stderr)
        return 2

    last_error: FairPrepareError | None = None
    for attempt in range(1, args.attempts + 1):
        provider: HttpFairPrepareProvider | None = None
        try:
            provider = HttpFairPrepareProvider(args.base_url, timeout=args.timeout)
            report = provider.check_compatibility()
            print(
                json.dumps(
                    {
                        "compatible": True,
                        "api_version": report.api_version,
                        "dataset_id": report.dataset_id,
                        "templates": list(report.templates),
                    },
                    sort_keys=True,
                )
            )
            return 0
        except FairPrepareError as exc:
            last_error = exc
            if attempt < args.attempts:
                time.sleep(args.retry_delay)
        finally:
            if provider is not None:
                provider.client.close()

    print(
        f"FAIR-VCG compatibility canary failed closed: {last_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
