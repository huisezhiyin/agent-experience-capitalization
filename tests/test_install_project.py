import json
import tempfile
import unittest
from pathlib import Path

from runtime.core.project_install import (
    INTEGRATION_MODE_CODEX_HOOKS,
    INTEGRATION_MODE_CLAUDE_HOOKS,
    INTEGRATION_MODE_DOCS_ONLY,
    install_project_agents,
)


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
            policy_text = json.loads((workspace / ".expcap-project.json").read_text(encoding="utf-8"))
            gitignore_text = (workspace / ".gitignore").read_text(encoding="utf-8")

            self.assertIn("原有规则。", agents_text)
            self.assertIn("<!-- EXPCAP START -->", agents_text)
            self.assertIn("AGENTS.expcap.md", agents_text)
            self.assertIn("只要这个项目里真的开了新 chat，默认仍然会执行 `auto-start`", sidecar_text)
            self.assertIn("默认先做 get", sidecar_text)
            self.assertEqual(policy_text["project_status"], "active")
            self.assertEqual(result["project_status"], "active")
            self.assertEqual(result["integration_mode"], INTEGRATION_MODE_DOCS_ONLY)
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
            policy_once = (workspace / ".expcap-project.json").read_text(encoding="utf-8")
            install_project_agents(workspace)
            twice = agents_path.read_text(encoding="utf-8")
            gitignore_twice = (workspace / ".gitignore").read_text(encoding="utf-8")
            policy_twice = (workspace / ".expcap-project.json").read_text(encoding="utf-8")

            self.assertEqual(once, twice)
            self.assertEqual(gitignore_once, gitignore_twice)
            self.assertEqual(json.loads(policy_once)["project_status"], "active")
            self.assertEqual(json.loads(policy_twice)["project_status"], "active")
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

            result = install_project_agents(workspace, integration_mode=INTEGRATION_MODE_CLAUDE_HOOKS)

            claude_text = claude_path.read_text(encoding="utf-8")
            self.assertIn("原有 Claude 规则。", claude_text)
            self.assertIn("<!-- EXPCAP START -->", claude_text)
            self.assertIn("AGENTS.expcap.md", claude_text)
            self.assertEqual(result["integration_mode"], INTEGRATION_MODE_CLAUDE_HOOKS)
            self.assertEqual(result["created_claude"], False)
            self.assertEqual(result["updated_claude"], True)
            self.assertEqual(json.loads((workspace / ".expcap-project.json").read_text(encoding="utf-8"))["integration_mode"], INTEGRATION_MODE_CLAUDE_HOOKS)

            install_project_agents(workspace, integration_mode=INTEGRATION_MODE_CLAUDE_HOOKS)
            self.assertEqual(claude_path.read_text(encoding="utf-8").count("<!-- EXPCAP START -->"), 1)

    def test_install_project_claude_hooks_generate_settings_and_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)

            result = install_project_agents(workspace, integration_mode=INTEGRATION_MODE_CLAUDE_HOOKS)

            settings_path = workspace / ".claude" / "settings.json"
            prompt_hook = workspace / ".claude" / "hooks" / "expcap_user_prompt_submit.sh"
            stop_hook = workspace / ".claude" / "hooks" / "expcap_stop.sh"
            settings = json.loads(settings_path.read_text(encoding="utf-8"))

            self.assertEqual(Path(result["claude_settings_path"]).resolve(), settings_path.resolve())
            self.assertEqual(Path(result["claude_hooks_dir"]).resolve(), (workspace / ".claude" / "hooks").resolve())
            self.assertTrue(settings_path.exists())
            self.assertTrue(prompt_hook.exists())
            self.assertTrue(stop_hook.exists())
            self.assertIn("UserPromptSubmit", settings["hooks"])
            self.assertIn("Stop", settings["hooks"])
            self.assertIn("EXPCAP_STORAGE_PROFILE", settings["env"])
            self.assertIn("scripts/expcap-hook", prompt_hook.read_text(encoding="utf-8"))
            self.assertIn("scripts/expcap-hook", stop_hook.read_text(encoding="utf-8"))

    def test_install_project_codex_hooks_generate_settings_and_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)

            result = install_project_agents(workspace, integration_mode=INTEGRATION_MODE_CODEX_HOOKS)

            hooks_path = workspace / ".codex" / "hooks.json"
            prompt_hook = workspace / ".codex" / "hooks" / "expcap_user_prompt_submit.sh"
            stop_hook = workspace / ".codex" / "hooks" / "expcap_stop.sh"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))

            self.assertEqual(Path(result["codex_hooks_path"]).resolve(), hooks_path.resolve())
            self.assertEqual(Path(result["codex_hooks_dir"]).resolve(), (workspace / ".codex" / "hooks").resolve())
            self.assertTrue(hooks_path.exists())
            self.assertTrue(prompt_hook.exists())
            self.assertTrue(stop_hook.exists())
            self.assertIn("UserPromptSubmit", hooks["hooks"])
            self.assertIn("Stop", hooks["hooks"])
            self.assertIn(
                "bash .codex/hooks/expcap_user_prompt_submit.sh",
                json.dumps(hooks["hooks"]["UserPromptSubmit"], ensure_ascii=False),
            )
            self.assertIn("scripts/expcap-hook", prompt_hook.read_text(encoding="utf-8"))
            self.assertIn("--host codex", prompt_hook.read_text(encoding="utf-8"))
            self.assertIn("scripts/expcap-hook", stop_hook.read_text(encoding="utf-8"))

    def test_install_project_include_claude_maps_to_claude_hooks_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)

            result = install_project_agents(workspace, include_claude=True)

            self.assertEqual(result["integration_mode"], INTEGRATION_MODE_CLAUDE_HOOKS)
            self.assertTrue((workspace / ".claude" / "settings.json").exists())

    def test_install_project_can_mark_workspace_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "repo"
            workspace.mkdir(parents=True, exist_ok=True)

            result = install_project_agents(workspace, project_status="inactive")

            sidecar_text = (workspace / "AGENTS.expcap.md").read_text(encoding="utf-8")
            policy_text = json.loads((workspace / ".expcap-project.json").read_text(encoding="utf-8"))

            self.assertEqual(result["project_status"], "inactive")
            self.assertEqual(policy_text["project_status"], "inactive")
            self.assertIn("当前项目状态：`inactive`", sidecar_text)
            self.assertIn("不用于阻断新 chat 激活", sidecar_text)


if __name__ == "__main__":
    unittest.main()
