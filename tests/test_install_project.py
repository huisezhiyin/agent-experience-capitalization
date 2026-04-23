import tempfile
import unittest
from pathlib import Path

from runtime.core.project_install import install_project_agents


class InstallProjectTests(unittest.TestCase):
    def test_install_project_appends_block_without_replacing_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)
            agents_path = workspace / "AGENTS.md"
            agents_path.write_text("# AGENTS.md\n\n原有规则。\n", encoding="utf-8")

            result = install_project_agents(workspace)

            agents_text = agents_path.read_text(encoding="utf-8")
            sidecar_text = (workspace / "AGENTS.expcap.md").read_text(encoding="utf-8")
            gitignore_text = (workspace / ".gitignore").read_text(encoding="utf-8")

            self.assertIn("原有规则。", agents_text)
            self.assertIn("<!-- EXPCAP START -->", agents_text)
            self.assertIn("AGENTS.expcap.md", agents_text)
            self.assertIn("默认先做 get", sidecar_text)
            self.assertIn(".agent-memory/", gitignore_text)
            self.assertEqual(result["created_agents"], False)
            self.assertEqual(result["updated_agents"], True)
            self.assertEqual(result["created_gitignore"], True)
            self.assertEqual(result["updated_gitignore"], True)

    def test_install_project_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)
            agents_path = workspace / "AGENTS.md"
            agents_path.write_text("# AGENTS.md\n\n原有规则。\n", encoding="utf-8")

            install_project_agents(workspace)
            once = agents_path.read_text(encoding="utf-8")
            gitignore_once = (workspace / ".gitignore").read_text(encoding="utf-8")
            install_project_agents(workspace)
            twice = agents_path.read_text(encoding="utf-8")
            gitignore_twice = (workspace / ".gitignore").read_text(encoding="utf-8")

            self.assertEqual(once, twice)
            self.assertEqual(gitignore_once, gitignore_twice)
            self.assertEqual(once.count("<!-- EXPCAP START -->"), 1)
            self.assertEqual(gitignore_once.count(".agent-memory/"), 1)

    def test_install_project_preserves_existing_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)
            gitignore_path = workspace / ".gitignore"
            gitignore_path.write_text("dist/\n", encoding="utf-8")

            result = install_project_agents(workspace)
            gitignore_text = gitignore_path.read_text(encoding="utf-8")

            self.assertIn("dist/", gitignore_text)
            self.assertIn(".agent-memory/", gitignore_text)
            self.assertEqual(gitignore_text.count(".agent-memory/"), 1)
            self.assertEqual(result["created_gitignore"], False)
            self.assertEqual(result["updated_gitignore"], True)

            second_result = install_project_agents(workspace)
            self.assertEqual(gitignore_path.read_text(encoding="utf-8").count(".agent-memory/"), 1)
            self.assertEqual(second_result["created_gitignore"], False)
            self.assertEqual(second_result["updated_gitignore"], False)

    def test_install_project_can_also_update_claude_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)
            claude_path = workspace / "CLAUDE.md"
            claude_path.write_text("# CLAUDE.md\n\n原有 Claude 规则。\n", encoding="utf-8")

            result = install_project_agents(workspace, include_claude=True)

            claude_text = claude_path.read_text(encoding="utf-8")
            self.assertIn("原有 Claude 规则。", claude_text)
            self.assertIn("<!-- EXPCAP START -->", claude_text)
            self.assertIn("AGENTS.expcap.md", claude_text)
            self.assertEqual(result["created_claude"], False)
            self.assertEqual(result["updated_claude"], True)

            install_project_agents(workspace, include_claude=True)
            self.assertEqual(claude_path.read_text(encoding="utf-8").count("<!-- EXPCAP START -->"), 1)


if __name__ == "__main__":
    unittest.main()
