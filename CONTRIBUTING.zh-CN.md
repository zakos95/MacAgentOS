# 参与 MacAgent OS 开发

[English](CONTRIBUTING.md) · [Français](CONTRIBUTING.fr.md) · [Русский](CONTRIBUTING.ru.md) · [简体中文](CONTRIBUTING.zh-CN.md)

感谢帮助改进测试版。开始前请在[待办任务](https://github.com/zakos95/MacAgentOS/issues)下留言，以避免重复工作。标记为 [`good first issue`](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) 的任务适合首次贡献。

SwiftUI 应用位于 `NativeMacApp/`，FastAPI 后端位于 `server.py`，MCP 集成位于 `mcp_hub.py`，Markdown 技能位于 `claude_skills.py`，智能体团队位于 `team_runtime.py`。请参阅[中文 README](README.zh-CN.md)、[MCP 指南](docs/MCP.md)及[技能与团队指南](docs/SKILLS-AND-TEAMS.md)。

## 运行检查

需要 Python 3.12 和 Swift 6。在仓库根目录运行：

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-contributor .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

使用 `.venv312/bin/python server.py` 启动后端。自动测试不要求安装 Ollama；使用真实模型测试时需先安装本地模型。不要把云端 API 密钥或私人文件加入提交或公开任务。

## 提交拉取请求

从 `main` 创建分支，让修改集中于一个任务，说明修改前后的具体行为，并列出已运行的检查。对于可能再次出现的问题，请添加回归测试。界面修改请附截图。真实设备测试请注明 macOS 版本、Mac 架构和型号。

声称兼容 Claude 的功能应引用最新的 Claude 或 MCP 文档，准确说明支持的格式及限制。安全问题请通过维护者的 GitHub 个人资料私下联系，不要在公开任务中泄露秘密信息。

代码贡献按项目的 [MIT 许可证](LICENSE)发布。
