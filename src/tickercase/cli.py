"""Command line: python -m tickercase.cli run <claim.json> --sec-mode synthetic --confirm [--out result.json]

--confirm is the explicit confirmation step: without it the case is not evaluated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import SEC_MODES, load_settings
from .models import ClaimDraft
from .service import CaseService
from .validation import ConfirmationError, confirm, validate_draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tickercase")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="validate, confirm and evaluate a claim file")
    run.add_argument("claim_file", type=Path)
    run.add_argument("--sec-mode", choices=SEC_MODES, default=None)
    run.add_argument("--confirm", action="store_true", help="confirm the inputs in this file as written")
    run.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    data = json.loads(args.claim_file.read_text(encoding="utf-8"))
    draft = ClaimDraft.model_validate(data.get("draft", data))
    settings = load_settings()
    service = CaseService(settings)

    if not args.confirm:
        result = validate_draft(draft, today=service.today())
        print(result.model_dump_json(indent=2))
        print("\nNot evaluated: pass --confirm after checking the inputs above.", file=sys.stderr)
        return 2
    try:
        confirmation = confirm(draft, now=service.now, today=service.today())
    except ConfirmationError as exc:
        print(exc.result.model_dump_json(indent=2))
        return 2
    case = service.evaluate(draft, confirmation, sec_mode=args.sec_mode or settings.sec_mode)
    text = case.model_dump_json(indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {args.out} (status={case.status.value})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
