# MacAgent OS — 公开测试版 1.1.0-beta.1

[English](README.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [简体中文](README.zh-CN.md)

MacAgent OS 是使用 SwiftUI 和本地 FastAPI 后端构建的 macOS AI 助手。下载 [Apple Silicon 测试版](https://github.com/zakos95/MacAgentOS/releases/tag/v1.1.0-beta.1)，打开 DMG，并将 Mac Agent OS 复制到“应用程序”文件夹。在设置中选择 AI 服务。Ollama 需要已安装的本地模型；云端服务需要你自己的凭据。

应用使用 ad hoc 签名，尚未经过 Apple 公证。Gatekeeper 可能要求你在“系统设置 → 隐私与安全性”中允许打开。声明的最低系统版本为 macOS 14。目前仅在运行 macOS 27.2.1 的 Apple Silicon 设备上验证，Intel Mac 和其他系统版本仍需测试。

## 已验证的功能

- 使用 Ollama 进行本地聊天，并保存会话历史。
- 显式导入 Claude Desktop 或项目的 MCP 配置，支持 stdio、HTTP 和 SSE 传输，以及工具、资源和提示词。参阅 [MCP 指南](docs/MCP.md)。
- 个人和项目 Markdown 技能，支持手动调用、参数及关联的文本参考文件。参阅 [技能与智能体团队](docs/SKILLS-AND-TEAMS.md)。
- 多个文本智能体使用独立上下文工作，并汇总回答。
- 后端和网页聊天身份验证，用户数据保存在 macOS Application Support 中。

本地通过了 55 项 Python 测试和 2 项 Swift 测试。使用 qwen3:8b 实际验证了聊天、读取参考文件的技能，以及双智能体团队。MCP 测试连接了真实的本地 stdio、HTTP 和 SSE 服务器。

## 参与开发

欢迎 macOS、Swift、Python、MCP 和 AI 智能体开发者。请阅读[贡献指南](CONTRIBUTING.md)，选择适合首次参与的[任务](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)，或加入[发布讨论](https://github.com/zakos95/MacAgentOS/discussions/5)。报告问题时，请提供 macOS 版本、Mac 架构、模型和复现步骤。

当前任务包括[在更多 Mac 上测试](https://github.com/zakos95/MacAgentOS/issues/1)、[配置 GitHub Actions](https://github.com/zakos95/MacAgentOS/issues/2)、[MCP OAuth](https://github.com/zakos95/MacAgentOS/issues/3)，以及[为智能体团队添加受监督的工具](https://github.com/zakos95/MacAgentOS/issues/4)。

## 测试版的限制

兼容所有 Claude 技能和连接器仍是目标。技能钩子与 shell 执行、托管插件、可使用工具的智能体，以及内置 MCP OAuth 尚待开发。当前的智能体团队输出文本。云端服务和 macOS 权限仍需使用贡献者的账号和设备测试。目前没有对比基准测试证明其优于 Claude；回答质量取决于所选模型。

## 开发

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python server.py
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-tests .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

后端监听 127.0.0.1:8000。网页聊天需要 `~/Library/Application Support/MacAgentOS/data/api_key.txt` 中的本地令牌。设置 `MACAGENT_APP_SUPPORT_DIR` 可隔离测试数据。构建说明见[部署文档](NativeMacApp/DEPLOYMENT.md)。

## 许可证

[MIT](LICENSE)。
