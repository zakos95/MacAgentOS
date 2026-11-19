# MacAgent OS — public beta 1.1.0-beta.1

[English](README.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [简体中文](README.zh-CN.md)

MacAgent OS is a macOS AI assistant built with SwiftUI and a local FastAPI backend. Download the [Apple Silicon beta](https://github.com/zakos95/MacAgentOS/releases/tag/v1.1.0-beta.1), open the DMG, and copy Mac Agent OS into Applications. Select an AI provider in settings. Ollama requires a locally installed model. Cloud providers require your own credentials.

The app has an ad hoc signature and has not been notarized by Apple. Gatekeeper may require approval in System Settings → Privacy & Security. The declared minimum is macOS 14. Testing so far covers Apple Silicon on macOS 27.2.1. Intel Macs and other macOS versions need validation.

## Tested features

- Local Ollama chat with persistent conversation history.
- Explicit import of Claude Desktop or project MCP configurations, with stdio, HTTP and SSE transports and support for tools, resources and prompts. See the [MCP guide](docs/MCP.md).
- Personal and project Markdown skills with manual invocation, arguments and linked text references. See [Skills and teams](docs/SKILLS-AND-TEAMS.md).
- Teams of text agents with independent contexts and a combined answer.
- Backend and web chat authentication, with user data stored in macOS Application Support.

The local suite passed 55 Python and 2 Swift tests. Real tests with qwen3:8b covered chat, a skill that read its reference file, and a two-agent team. MCP tests connected to real local stdio, HTTP and SSE servers.

## Help build it

We welcome macOS, Swift, Python, MCP and agent developers. Read [CONTRIBUTING.md](CONTRIBUTING.md), pick a [good first issue](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22), or join the [launch discussion](https://github.com/zakos95/MacAgentOS/discussions/5). Report bugs with your macOS version, Mac architecture, model and reproduction steps.

Current work includes [testing on more Macs](https://github.com/zakos95/MacAgentOS/issues/1), [GitHub Actions](https://github.com/zakos95/MacAgentOS/issues/2), [MCP OAuth](https://github.com/zakos95/MacAgentOS/issues/3) and [supervised tools for agent teams](https://github.com/zakos95/MacAgentOS/issues/4).

## Beta limits

Full compatibility with every Claude skill and connector is a goal. Skill hooks and shell execution, hosted plugins, agents with tools and built-in MCP OAuth remain to be developed. Current teams produce text. Cloud providers and macOS permissions still need testing with contributors' own accounts and machines. No comparative benchmark establishes superiority over Claude. Output quality depends on the chosen model.

## Development

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python server.py
```

The backend listens on 127.0.0.1:8000. Web chat requests the local token from `~/Library/Application Support/MacAgentOS/data/api_key.txt`. Set `MACAGENT_APP_SUPPORT_DIR` to isolate test data.

```sh
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-tests .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

See [deployment instructions](NativeMacApp/DEPLOYMENT.md) for building the app.

## License

[MIT](LICENSE).
