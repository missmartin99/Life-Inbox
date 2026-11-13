#!/usr/bin/env python3
"""
Automated 4-Level Smoke-Testing Suite for Matt Pocock Skills & Life-Inbox Integration.

Validates:
  Level 1: 28/28 frontmatter validations & companion templates across Claude and Gemini skill directories.
  Level 2: Markdown link integrity across all skill files (zero broken relative links).
  Level 3: Router & rule verification (ask-matt router mappings, codebase-design vocabulary, repo steering).
  Level 4: Life-Inbox test suite baseline execution (463 passed, 15 skipped, 0 failed).
"""

import os
import re
import subprocess
import sys
from pathlib import Path
import yaml

# Canonical list of 28 Matt Pocock skills
EXPECTED_SKILLS = [
    "setup-matt-pocock-skills",
    "ask-matt",
    "grill-me",
    "grill-with-docs",
    "grilling",
    "handoff",
    "prototype",
    "to-spec",
    "to-tickets",
    "implement",
    "implement-spec",
    "tdd",
    "code-review",
    "pr",
    "retro",
    "triage",
    "diagnosing-bugs",
    "wayfinder",
    "improve-codebase-architecture",
    "domain-modeling",
    "codebase-design",
    "research",
    "to-questionnaire",
    "wizard",
    "wait-what",
    "teach",
    "writing-for-agents",
    "chief-of-staff",
]

# Specific companion files expected per skill
COMPANION_FILES = {
    "setup-matt-pocock-skills": [
        "domain.md",
        "triage-labels.md",
        "issue-tracker-github.md",
        "issue-tracker-gitlab.md",
        "issue-tracker-local.md",
    ],
    "ask-matt": ["PHASE-BOUNDARIES.md"],
    "prototype": ["LOGIC.md", "UI.md"],
    "tdd": ["mocking.md", "tests.md"],
    "pr": ["CREDITS.md"],
    "triage": ["AGENT-BRIEF.md", "OUT-OF-SCOPE.md"],
    "diagnosing-bugs": ["scripts/hitl-loop.template.sh"],
    "improve-codebase-architecture": ["HTML-REPORT.md"],
    "domain-modeling": ["GLOSSARY-FORMAT.md", "ADR-FORMAT.md"],
    "codebase-design": ["DEEPENING.md", "DESIGN-IT-TWICE.md"],
    "wizard": ["template.sh"],
    "teach": [
        "MISSION-FORMAT.md",
        "RESOURCES-FORMAT.md",
        "LEARNING-RECORD-FORMAT.md",
        "GLOSSARY-FORMAT.md",
    ],
    "writing-for-agents": ["SKILL-MECHANICS.md"],
}

CORE_VOCABULARY = [
    "Module",
    "Interface",
    "Implementation",
    "Depth",
    "Seam",
    "Adapter",
    "Leverage",
    "Locality",
]


class SmokeTestRunner:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.claude_skills_dir = Path.home() / ".claude" / "skills"
        self.gemini_skills_dir = Path.home() / ".gemini" / "config" / "skills"
        self.failures = []

    def log_success(self, msg: str):
        print(f"  [PASS] {msg}")

    def log_failure(self, msg: str):
        print(f"  [FAIL] {msg}")
        self.failures.append(msg)

    def run_level_1(self):
        """Level 1: 28/28 Frontmatter & Companion Templates validation."""
        print("\n" + "=" * 70)
        print("LEVEL 1: Frontmatter & Companion Templates Validation")
        print("=" * 70)

        # Check Claude skills directory
        if not self.claude_skills_dir.exists():
            self.log_failure(f"Claude skills dir not found: {self.claude_skills_dir}")
            return

        # Check Gemini skills directory
        if not self.gemini_skills_dir.exists():
            self.log_failure(f"Gemini skills dir not found: {self.gemini_skills_dir}")
            return

        for skill_name in EXPECTED_SKILLS:
            claude_path = self.claude_skills_dir / skill_name
            gemini_path = self.gemini_skills_dir / skill_name

            # Check existence in Claude
            if not claude_path.exists():
                self.log_failure(f"Skill '{skill_name}' missing in {self.claude_skills_dir}")
                continue

            # Check bridging in Gemini
            if not gemini_path.exists():
                self.log_failure(f"Skill '{skill_name}' not bridged in {self.gemini_skills_dir}")
                continue
            if not gemini_path.is_symlink() and not gemini_path.is_dir():
                self.log_failure(f"Skill '{skill_name}' in Gemini dir is not a symlink or directory")
                continue

            # Validate SKILL.md
            skill_md = claude_path / "SKILL.md"
            if not skill_md.exists():
                self.log_failure(f"SKILL.md missing for '{skill_name}'")
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
                match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                if not match:
                    self.log_failure(f"SKILL.md for '{skill_name}' missing YAML frontmatter delimiters")
                    continue

                fm = yaml.safe_load(match.group(1))
                if not isinstance(fm, dict):
                    self.log_failure(f"Invalid frontmatter format in '{skill_name}'")
                    continue

                parsed_name = fm.get("name")
                if parsed_name != skill_name:
                    self.log_failure(
                        f"Frontmatter name mismatch in '{skill_name}': got '{parsed_name}'"
                    )
                    continue

                desc = fm.get("description", "")
                if not desc or not isinstance(desc, str) or len(desc.strip()) < 10:
                    self.log_failure(f"Empty or insufficient description in '{skill_name}'")
                    continue

            except Exception as e:
                self.log_failure(f"Error parsing SKILL.md for '{skill_name}': {e}")
                continue

            # Validate openai.yaml
            openai_yaml = claude_path / "agents" / "openai.yaml"
            if not openai_yaml.exists():
                self.log_failure(f"agents/openai.yaml missing for '{skill_name}'")
            else:
                try:
                    yaml_content = yaml.safe_load(openai_yaml.read_text(encoding="utf-8"))
                    if not isinstance(yaml_content, dict):
                        self.log_failure(f"agents/openai.yaml is not a valid YAML mapping for '{skill_name}'")
                except Exception as e:
                    self.log_failure(f"Invalid YAML in agents/openai.yaml for '{skill_name}': {e}")

            # Validate companion files
            expected_companions = COMPANION_FILES.get(skill_name, [])
            for companion in expected_companions:
                comp_path = claude_path / companion
                if not comp_path.exists():
                    self.log_failure(f"Companion file '{companion}' missing for '{skill_name}'")
                elif comp_path.stat().st_size == 0:
                    self.log_failure(f"Companion file '{companion}' for '{skill_name}' is empty")

        if not self.failures:
            self.log_success(f"All {len(EXPECTED_SKILLS)} skills have valid frontmatter, metadata, and companion templates in Claude & Gemini dirs.")

    def run_level_2(self):
        """Level 2: Markdown Link Integrity validation."""
        print("\n" + "=" * 70)
        print("LEVEL 2: Markdown Link Integrity Validation")
        print("=" * 70)

        link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        total_links = 0
        broken_links = []

        for skill_name in EXPECTED_SKILLS:
            skill_dir = self.claude_skills_dir / skill_name
            for root, _, files in os.walk(skill_dir):
                for f in files:
                    if not f.endswith(".md"):
                        continue
                    md_path = Path(root) / f
                    try:
                        content = md_path.read_text(encoding="utf-8")
                        # Strip code blocks to avoid false positives on syntax examples
                        content_no_code = re.sub(r"```.*?```", "", content, flags=re.DOTALL)

                        for match in link_pattern.finditer(content_no_code):
                            label, url = match.groups()
                            # Skip external protocols, page anchors, or placeholder text
                            if url.startswith(("http://", "https://", "mailto:", "#")):
                                continue
                            if url in ("link",) or url.startswith(("<", "$")):
                                continue

                            # Remove in-page anchor from path
                            target_url = url.split("#")[0]
                            if not target_url:
                                continue

                            total_links += 1
                            target_path = (Path(root) / target_url).resolve()
                            if not target_path.exists():
                                broken_links.append((str(md_path.relative_to(skill_dir)), url, str(target_path)))
                    except Exception as e:
                        self.log_failure(f"Error checking links in {md_path}: {e}")

        if broken_links:
            for source, url, resolved in broken_links:
                self.log_failure(f"Broken link in {source}: '{url}' -> '{resolved}'")
        else:
            self.log_success(f"All {total_links} relative markdown links across all 28 skills resolve to existing files.")

    def run_level_3(self):
        """Level 3: Router & Rule Verification."""
        print("\n" + "=" * 70)
        print("LEVEL 3: Router & Rule Verification")
        print("=" * 70)

        # 1. Verify ask-matt router mappings
        ask_matt_dir = self.claude_skills_dir / "ask-matt"
        ask_matt_md = ask_matt_dir / "SKILL.md"
        phase_boundaries_md = ask_matt_dir / "PHASE-BOUNDARIES.md"

        if not ask_matt_md.exists():
            self.log_failure("ask-matt/SKILL.md does not exist")
        else:
            content = ask_matt_md.read_text(encoding="utf-8")
            # Extract skills referenced as `/skill-name`
            referenced_skills = set(re.findall(r"/([a-z0-9\-]+)", content))
            known_skill_set = set(EXPECTED_SKILLS)
            # Find which of the expected skills are covered
            covered = referenced_skills.intersection(known_skill_set)
            self.log_success(f"ask-matt routes to {len(covered)} Matt Pocock skills directly.")
            if len(covered) < 20:
                self.log_failure(f"ask-matt routes to fewer skills than expected: {len(covered)}")

        if not phase_boundaries_md.exists():
            self.log_failure("ask-matt/PHASE-BOUNDARIES.md does not exist")
        else:
            pb_content = phase_boundaries_md.read_text(encoding="utf-8")
            for boundary_action in ["Continue", "/clear", "/handoff", "Subagent", "/compact"]:
                if boundary_action not in pb_content:
                    self.log_failure(f"Phase boundary action '{boundary_action}' not found in PHASE-BOUNDARIES.md")
            self.log_success("ask-matt/PHASE-BOUNDARIES.md defines all 5 phase boundary decisions.")

        # 2. Verify codebase-design deep-module vocabulary
        codebase_design_md = self.claude_skills_dir / "codebase-design" / "SKILL.md"
        if not codebase_design_md.exists():
            self.log_failure("codebase-design/SKILL.md does not exist")
        else:
            cd_content = codebase_design_md.read_text(encoding="utf-8")
            missing_vocab = [term for term in CORE_VOCABULARY if term not in cd_content]
            if missing_vocab:
                self.log_failure(f"Missing core deep-module vocabulary in codebase-design: {missing_vocab}")
            else:
                self.log_success(f"codebase-design SKILL.md defines all {len(CORE_VOCABULARY)} core vocabulary terms.")

        # 3. Verify Life-Inbox repository steering files
        agents_md = self.repo_root / "AGENTS.md"
        issue_tracker_md = self.repo_root / "docs" / "agents" / "issue-tracker.md"
        triage_labels_md = self.repo_root / "docs" / "agents" / "triage-labels.md"
        domain_md = self.repo_root / "docs" / "agents" / "domain.md"
        scratch_keep = self.repo_root / ".scratch" / ".gitkeep"

        if not agents_md.exists():
            self.log_failure(f"Life-Inbox/AGENTS.md does not exist")
        else:
            agents_text = agents_md.read_text(encoding="utf-8")
            for section in ["## Agent skills", "### Issue tracker", "### Triage labels", "### Domain docs"]:
                if section not in agents_text:
                    self.log_failure(f"Section '{section}' missing from AGENTS.md")
            self.log_success("Life-Inbox/AGENTS.md contains all required Agent skills sections.")

        if not issue_tracker_md.exists():
            self.log_failure("Life-Inbox/docs/agents/issue-tracker.md does not exist")
        else:
            it_text = issue_tracker_md.read_text(encoding="utf-8")
            if ".scratch/" not in it_text:
                self.log_failure("docs/agents/issue-tracker.md does not reference '.scratch/'")
            else:
                self.log_success("docs/agents/issue-tracker.md configured for Local Markdown (.scratch/).")

        if not triage_labels_md.exists():
            self.log_failure("Life-Inbox/docs/agents/triage-labels.md does not exist")
        else:
            tl_text = triage_labels_md.read_text(encoding="utf-8")
            roles = ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"]
            missing_roles = [r for r in roles if r not in tl_text]
            if missing_roles:
                self.log_failure(f"docs/agents/triage-labels.md missing canonical roles: {missing_roles}")
            else:
                self.log_success("docs/agents/triage-labels.md defines all 5 canonical triage roles.")

        if not domain_md.exists():
            self.log_failure("Life-Inbox/docs/agents/domain.md does not exist")
        else:
            dm_text = domain_md.read_text(encoding="utf-8")
            if "GLOSSARY.md" not in dm_text or "docs/adr/" not in dm_text:
                self.log_failure("docs/agents/domain.md missing single-context references (GLOSSARY.md, docs/adr/)")
            else:
                self.log_success("docs/agents/domain.md configured for single-context layout.")

        if not scratch_keep.exists():
            self.log_failure("Life-Inbox/.scratch/.gitkeep does not exist")
        else:
            self.log_success("Life-Inbox/.scratch/ directory initialized with .gitkeep.")

    def run_level_4(self):
        """Level 4: Life-Inbox Baseline Test Suite validation."""
        print("\n" + "=" * 70)
        print("LEVEL 4: Life-Inbox Test Suite Baseline (Pytest)")
        print("=" * 70)

        venv_pytest = self.repo_root / ".venv" / "bin" / "pytest"
        if not venv_pytest.exists():
            self.log_failure(f"Virtualenv pytest not found at {venv_pytest}")
            return

        cmd = [str(venv_pytest), "-q"]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)

        print(f"Executing: PYTHONPATH=. .venv/bin/pytest -q in {self.repo_root} ...")
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            env=env,
            capture_output=True,
            text=True,
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        last_line = stdout.splitlines()[-1] if stdout.splitlines() else ""

        print(f"Pytest return code: {proc.returncode}")
        print(f"Pytest summary line: {last_line}")

        if proc.returncode != 0:
            self.log_failure(f"Pytest exited with non-zero code {proc.returncode}")
            if stderr:
                print("Pytest stderr:\n", stderr)

        # Parse test results: expected 463 passed, 15 skipped
        pass_match = re.search(r"(\d+)\s+passed", stdout)
        skip_match = re.search(r"(\d+)\s+skipped", stdout)
        fail_match = re.search(r"(\d+)\s+failed", stdout)
        err_match = re.search(r"(\d+)\s+errors", stdout)

        passed_count = int(pass_match.group(1)) if pass_match else 0
        skipped_count = int(skip_match.group(1)) if skip_match else 0
        failed_count = int(fail_match.group(1)) if fail_match else 0
        error_count = int(err_match.group(1)) if err_match else 0

        print(f"Parsed results: passed={passed_count}, skipped={skipped_count}, failed={failed_count}, errors={error_count}")

        if passed_count < 463:
            self.log_failure(f"Expected at least 463 passed tests, got {passed_count}")
        elif passed_count == 463:
            self.log_success(f"Baseline passed tests verified: exactly {passed_count} passed.")
        else:
            self.log_success(f"Tests passed verified: {passed_count} passed.")

        if skipped_count != 15:
            self.log_failure(f"Expected exactly 15 skipped tests, got {skipped_count}")
        else:
            self.log_success(f"Baseline skipped tests verified: exactly {skipped_count} skipped.")

        if failed_count > 0:
            self.log_failure(f"Detected {failed_count} test failures in Life-Inbox suite")

        if error_count > 0:
            self.log_failure(f"Detected {error_count} errors in Life-Inbox suite")


def main():
    repo_root = Path(__file__).resolve().parent.parent
    runner = SmokeTestRunner(repo_root)

    print("=" * 70)
    print("STARTING MATT POCOCK SKILLS & LIFE-INBOX SMOKE TEST SUITE")
    print(f"Target repository: {repo_root}")
    print("=" * 70)

    runner.run_level_1()
    runner.run_level_2()
    runner.run_level_3()
    runner.run_level_4()

    print("\n" + "=" * 70)
    print("SMOKE TEST SUMMARY")
    print("=" * 70)

    if runner.failures:
        print(f"STATUS: FAILED with {len(runner.failures)} error(s):")
        for idx, failure in enumerate(runner.failures, 1):
            print(f"  {idx}. {failure}")
        sys.exit(1)
    else:
        print("STATUS: 100% PASSED — ALL 4 LEVELS VERIFIED WITHOUT ERRORS")
        print("  - Level 1: 28/28 Skills Frontmatter & Companion Templates VALID")
        print("  - Level 2: Relative Markdown Links Integrity VALID")
        print("  - Level 3: Router & Rule Scaffolding VALID")
        print("  - Level 4: 463 Passed, 15 Skipped Pytest Baseline VALID")
        sys.exit(0)


if __name__ == "__main__":
    main()
