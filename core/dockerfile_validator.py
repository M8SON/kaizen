"""
Dockerfile validator for Kaizen skills.

Per-tier allowlist:
  bundled  — no validation (trusted; repo-reviewed)
  authored — only FROM (kaizen/base:latest), RUN (pip/apt commands, no other
             shell syntax), COPY (local), WORKDIR, CMD, ENV; no parser directives
  imported — everything authored allows, PLUS apt-get packages must be in
             the allowlist (core.apt_allowlist), and pip install must not use
             --index-url / --extra-index-url
"""

import re
import shlex
from pathlib import Path

from core.apt_allowlist import load_apt_allowlist
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED


class DockerfileValidationError(Exception):
    pass


BLOCKED_PATTERNS = [
    (r"curl\s+.*\|\s*(ba)?sh", "curl pipe to shell"),
    (r"wget\s+.*\|\s*(ba)?sh", "wget pipe to shell"),
    (r"\beval\b",               "eval"),
    (r"\bnetcat\b",             "netcat"),
    (r"(?<!\w)nc\s+-",          "netcat (nc)"),
    (r"--privileged",           "privileged flag"),
    (r"/var/run/docker",        "Docker socket reference"),
    (r"^COPY\s+https?://",      "COPY from URL"),
]

ALLOWED_RUN_PREFIXES = (
    "pip install",
    "pip3 install",
    "apt-get install",
    "apt-get update",
    "apt-get clean",
    "apt-get -y install",
    "apt-get -y update",
    "rm -rf /var/lib/apt/lists",
)

ALLOWED_INSTRUCTIONS = {"FROM", "RUN", "COPY", "WORKDIR", "CMD", "ENV"}

# "# syntax=<image>" makes BuildKit run that image as the Dockerfile parser.
_PARSER_DIRECTIVE = re.compile(r"#\s*(syntax|escape|check)\s*=", re.IGNORECASE)
_CONTINUATION = re.compile(r"\\[ \t]*$")
_RUN_SEPARATORS = {"&&", "||", ";"}
_SHELL_PUNCTUATION = set("();<>|&")
# pip options that change where packages come from, alone or clustered (-qi).
_PIP_SOURCE_OPTION = re.compile(r"--(extra-)?index-url|--find-links|--trusted-host|-[a-zA-Z]*[if]")


def validate(dockerfile_path: Path, *, tier: str = TIER_AUTHORED) -> None:
    """
    Validate a Dockerfile against the per-tier allowlist.

    Raises DockerfileValidationError with a descriptive message on failure.
    Bundled skills bypass validation; dev is validated like authored.
    """
    if tier == TIER_BUNDLED:
        return

    apt_allowlist = load_apt_allowlist() if tier == TIER_IMPORTED else None
    text = dockerfile_path.read_text(encoding="utf-8")
    from_count = 0

    for lineno, line in _logical_lines(text):
        raw_line = line
        for pattern, label in BLOCKED_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                raise DockerfileValidationError(
                    f"Line {lineno}: blocked pattern '{label}' — {raw_line!r}"
                )

        instruction = line.split()[0].upper() if line.split() else ""

        if instruction not in ALLOWED_INSTRUCTIONS:
            raise DockerfileValidationError(
                f"Line {lineno}: {instruction} is not allowed for tier {tier!r}"
            )

        if instruction == "FROM":
            from_count += 1
            if from_count > 1:
                raise DockerfileValidationError(
                    f"Line {lineno}: multi-stage builds are not allowed (second FROM)"
                )
            parts = line.split()
            image_ref = parts[1] if len(parts) > 1 else ""
            if image_ref.lower() != "kaizen/base:latest":
                raise DockerfileValidationError(
                    f"Line {lineno}: base image must be 'kaizen/base:latest', got {image_ref!r}"
                )

        elif instruction == "RUN":
            run_body = re.sub(r"^RUN\s+", "", line, flags=re.IGNORECASE).strip()
            try:
                segments = _run_segments(run_body)
            except ValueError:
                segments = None
            if segments is None or not _is_allowed_run(segments):
                raise DockerfileValidationError(
                    f"Line {lineno}: RUN only permits pip install / apt-get commands. "
                    f"Got: {run_body!r}"
                )
            if tier == TIER_IMPORTED:
                _validate_run_imported(segments, apt_allowlist, lineno)

        elif instruction == "COPY":
            parts = line.split()
            if any(p.startswith("--from=") for p in parts[1:]):
                raise DockerfileValidationError(
                    f"Line {lineno}: COPY --from is not allowed for tier {tier!r}"
                )
            src_parts = [p for p in parts[1:] if not p.startswith("--")]
            if len(src_parts) >= 2:
                sources = src_parts[:-1]
                invalid = next(
                    (src for src in sources if not _is_relative_copy_source(src)),
                    None,
                )
                if invalid is not None:
                    raise DockerfileValidationError(
                        f"Line {lineno}: COPY source must be a relative local path, "
                        f"got {invalid!r}"
                    )

    if from_count == 0:
        raise DockerfileValidationError("Dockerfile has no FROM instruction")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, instruction), with backslash continuations joined
    and comments dropped, the way Docker reads the file."""
    logical = []
    parts: list[str] = []
    start = 0
    for lineno, raw_line in enumerate(text.split("\n"), 1):
        line = raw_line.strip()
        if _PARSER_DIRECTIVE.match(line):
            raise DockerfileValidationError(f"Line {lineno}: parser directives are not allowed")
        if not line or line.startswith("#"):
            continue
        start = start or lineno
        parts.append(_CONTINUATION.sub("", line))
        if not _CONTINUATION.search(line):
            logical.append((start, " ".join(parts)))
            parts, start = [], 0
    if parts:
        logical.append((start, " ".join(parts)))
    return logical


def _run_segments(run_body: str) -> list[str]:
    """The commands in a RUN body, split on && || ;. Raises ValueError for any
    other shell syntax: pipes, background, redirects, subshells, expansions."""
    if "$" in run_body or "`" in run_body:
        raise ValueError("shell expansion")
    lexer = shlex.shlex(run_body, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # shlex ends a comment at any #, the shell only at a word start
    segments, current = [], []
    for token in lexer:
        if token in _RUN_SEPARATORS:
            segments.append(" ".join(current))
            current = []
        elif set(token) <= _SHELL_PUNCTUATION:
            raise ValueError(f"shell operator {token}")
        else:
            current.append(token)
    segments.append(" ".join(current))
    return [s for s in segments if s]


def _is_allowed_run(segments: list[str]) -> bool:
    return all(any(seg.startswith(prefix) for prefix in ALLOWED_RUN_PREFIXES) for seg in segments)


def _validate_run_imported(segments: list[str], apt_allowlist: frozenset[str], lineno: int) -> None:
    for seg in segments:
        if seg.startswith("pip install") or seg.startswith("pip3 install"):
            if any(_PIP_SOURCE_OPTION.match(t) or "://" in t for t in seg.split()[2:]):
                raise DockerfileValidationError(
                    f"Line {lineno}: pip install --index-url, other sources and URLs "
                    "are not allowed for imported skills"
                )
        if seg.startswith("apt-get install") or seg.startswith("apt-get -y install"):
            tokens = [
                t for t in seg.split()
                if t not in {"apt-get", "install", "-y", "--yes", "--no-install-recommends"}
            ]
            for pkg in tokens:
                if pkg in apt_allowlist:
                    continue
                raise DockerfileValidationError(
                    f"Line {lineno}: apt package {pkg!r} is not in the allowlist "
                    "(extend via ~/.kaizen/config/apt-allowlist.txt)"
                )


def _is_relative_copy_source(src: str) -> bool:
    if not src or src.startswith("/"):
        return False
    src_path = Path(src)
    return ".." not in src_path.parts
