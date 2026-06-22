# Web 自动化脚本生成 Agent

本项目实现一个基于 DrissionPage 的 Web 自动化脚本生成 Agent。目标是把一句自然语言任务转换成经过执行验证和自动修复的独立 Python 脚本。

第一阶段已经搭建项目骨架、示例页面、预检脚本和基础测试。

第二阶段已经落地 Runtime、DOM Candidate Snapshot 和 selector fallback。当前可在不接 LLM 的情况下，从 HTML 中抽取候选交互元素并为每个候选元素生成按稳定性排序的 selector；DrissionRuntime 也已经具备打开页面、执行基础动作和 snapshot 的接口实现。

第三阶段已经开始落地 mock Planner、SelectorGrounder 和 CaptureRunner。当前可用 `--mock-llm --plan-only` 生成 `action_plan.json`，也可用 `--mock-llm --capture-only` 在真实浏览器中执行步骤并生成 `captured_actions.json`、`generation_trace.json` 和 DOM snapshots。

## 架构映射

- Generation：把自然语言任务拆成高层操作计划，基于真实页面 DOM candidate 选择元素，捕获成功动作，并生成 `script_initial.py`。
- Debugging：运行初版脚本，收集 stdout、stderr、失败行、DOM 片段和 `failure_context.json`，调用 LLM 生成完整修复脚本，最多重试 3 次。
- Resilience：提供选择器降级、智能等待、操作重试、统一异常处理和断言 helper，并将这些能力内嵌到最终脚本。

最终交付的 `script_final.py` 必须是纯 DrissionPage 代码，不包含 OpenAI、LLM、Agent 内部模块或 `.env` 依赖。

## Linux 运行（主路径）

推荐环境：

- Ubuntu 22.04/24.04 或同类 Linux 发行版
- Python 3.11+
- Linux Google Chrome 或 Chromium
- DrissionPage 4.1.1.4

建议把项目放在 Linux 用户目录，例如 `~/workspace/drission_agent`。

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ca-certificates wget fonts-noto-cjk
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

安装 Chrome：

```bash
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
google-chrome --version
```

桌面 Linux 可视化运行前确认图形会话可用：

```bash
echo "$DISPLAY"
echo "$WAYLAND_DISPLAY"
```

复制 `.env.example` 为 `.env`，填入：

```dotenv
TEXT_LLM_API_KEY=your_deepseek_key
TEXT_LLM_MODEL=deepseek-chat
TEXT_LLM_BASE_URL=https://api.deepseek.com
VISION_LLM_API_KEY=your_dashscope_key
VISION_LLM_MODEL=qwen3-vl-flash
VISION_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BROWSER_TYPE=chrome
BROWSER_PATH=/usr/bin/google-chrome
BROWSER_USER_DATA_PATH=outputs/browser_profiles/chrome
BROWSER_DEBUG_PORT=19222
BROWSER_HEADLESS=0
OUTPUT_DIR=outputs
```

Planner、SelectorGrounder 的 LLM 决策和 replan 使用 `TEXT_LLM_*`；只有 DOM 无法可靠定位、进入截图视觉点击 fallback 时才使用 `VISION_LLM_*`。旧的 `OPENAI_* / DEEPSEEK_* / DASHSCOPE_*` 文本配置仍兼容，但不会替代显式的 `VISION_LLM_*`。

`BROWSER_PATH` 最好设置为 `command -v google-chrome` 返回的实际路径，也可以省略；Runtime 会依次查找 `google-chrome`、`google-chrome-stable`、`chromium` 和 `chromium-browser`。桌面调试使用 `BROWSER_HEADLESS=0`，服务器/CI 使用 `BROWSER_HEADLESS=1`。

Runtime 和 smoke 脚本使用独立 Linux 用户数据目录，避免干扰日常浏览器 profile。

## 预检

```bash
python scripts/smoke_python.py
BROWSER_HEADLESS=0 python scripts/smoke_drission.py
BROWSER_HEADLESS=1 python scripts/smoke_drission.py
python scripts/smoke_openai.py
BROWSER_HEADLESS=1 python scripts/smoke_real_web_form.py
BROWSER_HEADLESS=1 python scripts/smoke_real_page_snapshot.py https://viggle.ai/app/mix --wait 8 --label viggle_mix_snapshot
```

说明：

- `smoke_python.py` 检查当前 Python 解释器。
- `smoke_drission.py` 打开浏览器访问 `https://example.com`。
- `smoke_openai.py` 需要 `.env` 或系统环境变量里有 `OPENAI_API_KEY`。
- `smoke_real_web_form.py` 打开 Selenium Web Form 真实网页，抽取真实 DOM candidates，验证关键候选 selector 可定位，并执行文本输入与下拉选择。
- `smoke_real_page_snapshot.py` 可对任意真实网页做只读 snapshot，输出 `state.json`、`candidates.json`、`summary.json` 和 `screenshot.png`。

## 示例

本地示例：

- `examples/local_search/task.txt`：搜索 alpha 并点击第一条结果。
- `examples/local_form/task.txt`：填写表单、选择下拉项、上传文件并提交。

真实网站示例：

- `examples/real_selenium_web_form/task.txt`：打开 Selenium Web Form，输入 hello，选择 Two，并提交。

后续主命令会是：

```bash
python -m app.cli --headed --mock-llm --task-file examples/local_search/task.txt
python -m app.cli --headless --task-file examples/real_selenium_web_form/task.txt --max-retries 3
```

当前阶段可用命令：

```bash
python -m app.cli --mock-llm --plan-only --task-file examples/local_search/task.txt
python -m app.cli --headed --mock-llm --capture-only --task-file examples/local_search/task.txt
python -m app.cli --headed --mock-llm --capture-only --task-file examples/local_form/task.txt
python -m app.cli --headless --mock-llm --capture-only --task-file examples/real_selenium_web_form/task.txt
```

在可视化模式下，检测到登录页或登录弹窗时，系统默认暂停并等待用户在浏览器中完成登录；登录成功后会自动继续执行。Chrome 用户目录会持久化，因此登录态未失效时，后续运行无需重复登录。无人值守任务如需在遇到登录时立即失败，可增加 `--no-wait-for-login`。

## 输出目录

每次运行会写入：

```text
outputs/<run_id>/
  task.txt
  action_plan.json
  dom_snapshots/
  captured_actions.json
  generation_trace.json
  script_initial.py
  script_final.py
  debug.log
  result.json
  failure_context.json
  screenshots/
```

## 当前状态

- 已创建项目目录结构。
- 已加入本地搜索和本地表单示例。
- 已加入 Python、DrissionPage、OpenAI 三个 smoke scripts。
- 已加入基础布局测试、DOM candidate 测试、selector priority 测试和 runtime fallback 测试。
- 已实现 `app/generation/dom_snapshot.py` 的 HTML candidate 提取。
- 已实现 `app/resilience/selectors.py` 的 selector 构建、去重、脆弱 XPath 过滤和过宽 selector 过滤。
- 已实现 `app/runtime/drission_runtime.py` 的 DrissionPage runtime 边界和动作 fallback。
- Linux 是默认验收环境；Windows Chrome/Edge 路径仅作为代码兼容 fallback 保留。
- 已用真实网页 `https://www.selenium.dev/selenium/web/web-form.html` 验证 DOM candidate 抽取和 selector 可用性。
- 已用复杂真实页面 `https://viggle.ai/app/mix` 验证动态页面 snapshot。现在输出拆成两份：`raw_candidates.json` / `dom_snapshots/raw_step_*.json` 保留完整调试字段如 `context_chain`、`css_path`；`candidates.json` / `dom_snapshots/step_*.json` 是传给 Grounder 的 action-specific compact 候选，会按 `click/input/select/upload` 过滤候选、裁剪字段并调整排序。
- `input` 视图只保留文本输入控件，`select` 视图只保留下拉框并携带 `options/selected`，`upload` 视图保留 `file_input/upload_zone` 并允许隐藏文件 input，`click` 视图排除文本输入框、下拉框、文件 input 和不可见元素。
- Planner 输出会先经过 `normalize_action_plan()` 强制规范化和校验：`fill/type/enter_text` 等会归一为 `input`，`choose/dropdown/select_option` 归一为 `select`，`tap/press` 归一为 `click`，`attach_file/file_upload` 归一为 `upload`，并检查每种 action 的必填字段，避免 LLM Planner 输出漂移后进入错误候选过滤逻辑。
- CLI 默认使用 `LLMPlanner` 调用 OpenAI 生成 ActionPlan；未配置 `OPENAI_API_KEY` 时会提示配置或使用 `--mock-llm`。`--mock-llm` 仍保留为离线回归测试路径。
- 对 `input[type=file]` 这类非唯一 selector，候选和 `captured_actions.json` 会记录 `index`、`match_count`、`unique` 与上传上下文，执行层会按 selector 对应 index 调用 `page.eles(...)[index]`，避免裸用重复 selector。
- 已实现 mock Planner、SelectorGrounder 和 CaptureRunner。
- 已验证 `local_search`、`local_form`、`real_selenium_web_form` 的 capture-only 流程。
- ScriptWriter 已能把 `captured_actions.json` 生成独立 DrissionPage 脚本，内置 fallback selector、重复 selector index、智能等待和失败截图；CLI 默认会在 capture 后写出 `generated_script.py`、`static_check.json`，并执行生成脚本一次，结果保存到 `script_run.json`。
- Debugging 的 LLM 修复闭环将在后续阶段继续增强。
