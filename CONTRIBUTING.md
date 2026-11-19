# Contributing to MacAgent OS

[English](CONTRIBUTING.md) · [Français](CONTRIBUTING.fr.md) · [Русский](CONTRIBUTING.ru.md) · [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for helping with the beta. Choose an [open issue](https://github.com/zakos95/MacAgentOS/issues) and comment there before starting to avoid duplicate work. Issues marked [`good first issue`](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) have a smaller scope.

The native SwiftUI app lives in `NativeMacApp/`; the FastAPI backend is in `server.py`. MCP integration is in `mcp_hub.py`, Markdown skills in `claude_skills.py`, and agent teams in `team_runtime.py`. See the [README](README.md), [MCP guide](docs/MCP.md) and [skills and teams guide](docs/SKILLS-AND-TEAMS.md).

## Run the checks

Use Python 3.12 and Swift 6 from the repository root.

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-contributor .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

Run the backend with `.venv312/bin/python server.py`. Ollama is optional for the automated tests. Real model checks require a locally installed model. Keep cloud API keys and private files out of commits, issues and screenshots.

## Open a pull request

Create a branch from `main`, keep the change focused on one issue, and describe a concrete before/after example. Include checks you ran. Add a regression test when it protects behavior likely to break again. For interface changes, attach a screenshot. For real tests, state the macOS version, Mac architecture and model.

Claims of compatibility with Claude should cite current Claude or MCP documentation and state the exact formats supported and any limits. Report security defects privately through the maintainer's GitHub profile, without exposing secrets in a public issue.

Code contributions are published under the project's [MIT license](LICENSE).
