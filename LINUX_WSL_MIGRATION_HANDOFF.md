# Linux / WSL Ubuntu 迁移与 Codex 交接计划

## 1. 背景与迁移目标

这是一个面试作业项目：把自然语言 Web 操作需求转换为经过真实浏览器执行、验证和必要修复的独立 DrissionPage Python 脚本。

项目最初按 Windows 11 原生环境开发。面试官后来确认其系统运行在 Linux，因此从当前版本开始切换为：

```text
主开发环境：WSL2 Ubuntu（项目放在 Linux 文件系统）
本地可视调试：WSLg + Linux Google Chrome，headless=false
CI/服务器运行：Linux + Chrome，headless=true
Windows：仅作为 WSL2/WSLg 宿主机
```

这次迁移不重写 Generation、Debugging、Resilience 架构。目标是保留已经验证的核心逻辑，把浏览器发现、路径、进程、用户数据目录、脚本模板、上传文件和文档改为 Linux-first、cross-platform。

Windows 基线提交：`5a0a7b7`（`chore: snapshot working Windows baseline`）。

## 2. 当前版本已经完成的能力

- `LLMPlanner` 将自然语言任务生成 ActionPlan。
- Planner 输出经过 action type 规范化、必填字段强校验和常见流程修复。
- `DrissionRuntime` 控制真实 Chrome，支持持久化浏览器 profile。
- DOM Snapshot 从真实页面采集候选、selector、几何位置、可访问名称、上下文和标签状态。
- Raw snapshot 与给 Grounder 的 action-specific compact candidates 分开保存。
- `click/input/select/upload` 使用不同候选池、字段裁剪和排序规则。
- Selector Grounder 只能从真实候选选择，不允许凭空生成 selector。
- 重复 selector 会记录 `index/match_count/unique`，执行和生成脚本会使用正确 index。
- 动态 React/Base UI id 被识别为易变 selector，并降级到 selector 列表末尾。
- 捕获期间支持登录检测、用户完成登录后的自动轮询继续和 profile 持久化。
- 当前页面与计划不一致或定位失败时，可把历史轨迹、当前 state 和 compact candidates 交给 LLM replan。
- 标签页使用真实标签名硬约束；例如 My Library 不允许被 History 代替。
- 标签点击后支持 `tab_selected` postcondition，未真正激活时不能判成功。
- 排序目标按可见卡片的几何阅读顺序处理，相关的 Reuse/Use 控件会绑定到卡片 rank。
- Captured actions 可生成独立 DrissionPage 脚本，不依赖 Agent 内部模块或 LLM。
- 生成脚本有 selector fallback、短超时易变 selector、登录轮询、截图、点击前高亮和动作延时。
- Debug Runner 已使用 `sys.executable` 启动生成脚本，不依赖 Windows venv 路径。
- 最新真实 Viggle 流程已验证：打开 Mix、打开 Add Image Library、选择 My Library、选择第一行第二张图片并添加。
- Windows 基线单元测试：`52 passed`。

运行产物位于 `outputs/`，按设计不提交 Git。最近一次成功运行目录是 Windows 本地的 `outputs/20260620_165551`，可作为人工比对资料，但不要上传其中的 profile、登录态或页面数据。

## 3. 可直接迁移的环境无关部分

下列代码应该先原样推送并在 Ubuntu 上运行单元测试，不要为了迁移而重写：

### Generation 核心

- `app/generation/planner.py`
  - ActionPlan/ActionStep 数据结构
  - action type 规范化和强校验
  - 初始 plan 与 replan prompt
  - 常见流程修复
- `app/generation/dom_snapshot.py`
  - 静态 HTML candidate 提取
- `app/generation/candidate_compactor.py`
  - action-specific candidate view
- `app/generation/selector_grounder.py`
  - 候选打分、显式标签约束、rank 目标选择
- `app/generation/exceptions.py`

### Resilience 与 Validation

- `app/resilience/selectors.py`
- `app/resilience/actions.py`
- `app/resilience/retry.py`
- `app/resilience/waits.py`
- `app/resilience/assertions.py`
- `app/validation/assertions.py`
- `app/validation/static_checks.py`

### LLM 与通用逻辑

- `app/llm/client.py`
- `app/debug/fixer.py` 中与 prompt/schema 有关的纯逻辑
- `app/debug/loop.py` 中不涉及进程启动的控制逻辑

### 测试与 fixtures

- `tests/test_candidate_extractor.py`
- `tests/test_candidate_compactor.py`
- `tests/test_selector_grounder.py`
- `tests/test_selector_priority.py`
- `tests/test_mock_planner.py`
- `tests/test_assertions.py`
- `examples/local_search/`
- `examples/local_form/`
- `examples/real_selenium_web_form/task.txt`

迁移原则：先确保这些测试在 Ubuntu 中不启动浏览器也能通过，再改环境层。若纯逻辑测试失败，优先判断编码、换行或 Python 版本差异，不要先改业务算法。

## 4. 必须改成 Linux-first / cross-platform 的部分

### 4.1 `app/config.py`

当前问题：

- `BROWSER_TYPE` 默认值是 `edge`。
- 没有统一的 headless 配置。
- 浏览器路径和 profile 路径还缺少平台规范化验证。

修改目标：

- Linux 默认浏览器改为 `chrome`。
- 新增 `browser_headless: bool`，读取 `BROWSER_HEADLESS=0/1`。
- 保留 `BROWSER_PATH` 显式覆盖。
- `BROWSER_USER_DATA_PATH` 始终通过 `Path(...).expanduser().resolve()` 处理。
- 测试配置解析，覆盖 Linux 路径、布尔值和环境变量缺失场景。

### 4.2 `app/runtime/drission_runtime.py`

当前 Windows 耦合：

- `_resolve_browser_path()` 只枚举 `C:\\Program Files...` 等 Windows 路径。
- 默认浏览器命名和 profile 目录来自 Windows 浏览器习惯。
- 尚未显式配置 headed/headless 两条路径。

修改目标：

- 优先使用 `BROWSER_PATH`。
- 其次用 `shutil.which()` 查找：

```python
google-chrome
google-chrome-stable
chromium
chromium-browser
```

- Windows 路径保留为兼容 fallback，但用 `platform.system()` 分支，不作为主路径。
- 根据 `BROWSER_HEADLESS` 调用 DrissionPage 对应的 headless 配置 API。
- profile 默认放在 `outputs/browser_profiles/chrome`，不复用 Windows Chrome profile。
- 调试端口继续可配置；启动前处理端口冲突，避免连接旧浏览器进程。
- 确认 `close()` 只关闭 Agent 启动/接管的实例，不误杀用户其他 Chrome。
- WSLg headed 模式下验证截图、点击、上传、登录轮询和窗口显示。
- Linux headless 模式下验证 DOM snapshot 和生成脚本回放。

### 4.3 `app/generation/script_writer.py`

当前 Windows 耦合：

- 生成脚本的 `resolve_browser_path()` 内置 Windows Chrome/Edge 路径。
- 生成脚本还没有 headless 配置参数。

修改目标：

- 生成的独立脚本使用与 Runtime 一致的跨平台浏览器发现逻辑。
- 生成脚本只依赖标准库、DrissionPage 和任务文件，不 import `app.*`。
- 加入 `BROWSER_HEADLESS` 支持。
- 默认 profile 使用 `Path(__file__).resolve().parent / "browser_profile"` 或调用方明确传入的目录。
- 生成脚本中的上传路径必须是相对脚本位置可解析的路径，例如：

```python
BASE_DIR = Path(__file__).resolve().parent
upload_file = (BASE_DIR / "fixtures" / "upload.png").resolve()
```

- 不生成盘符、反斜杠 venv 路径或 PowerShell 命令。
- 更新 `tests/test_script_writer.py`，Linux 路径为主，同时保留一条 Windows 路径兼容测试。

### 4.4 `app/generation/templates/drission_script.py.j2`

- 确认它是否仍是实际模板来源；当前主路径主要使用 `SCRIPT_TEMPLATE`。
- 若继续保留，必须与 `ScriptWriter` 的 Linux 浏览器发现、headless、profile、上传路径和 postcondition 行为一致。
- 若已经废弃，删除并补测试，避免两套模板漂移。

### 4.5 `app/debug/runner.py`

当前已经正确使用：

```python
subprocess.run([sys.executable, str(path)], ...)
```

Ubuntu 仍需验证：

- 不使用 `shell=True`、PowerShell、`.venv/Scripts/python.exe`。
- `cwd` 明确设为项目目录或脚本目录。
- 环境变量通过副本传入，不覆盖 PATH/DISPLAY/WAYLAND_DISPLAY。
- timeout 后终止整个进程组，避免遗留 Chrome/子进程。
- stdout/stderr 使用 UTF-8 容错解码。
- Linux signal/进程组行为补测试。

### 4.6 `app/cli.py`

- 输出路径继续使用 `Path`。
- 保留 `--action-delay`、`--wait-for-login`、`--max-replans`。
- 新增或接入 `--headless/--headed`，优先级应清楚：CLI > env > 平台默认。
- headed 模式应保留 DISPLAY/WAYLAND_DISPLAY 给 Debug Runner。
- CLI 帮助和示例全部改为 bash/Linux-first。

### 4.7 Smoke scripts

需要逐个检查：

- `scripts/smoke_drission.py`
- `scripts/smoke_real_web_form.py`
- `scripts/smoke_real_page_snapshot.py`
- `scripts/smoke_profile_persistence.py`
- `scripts/smoke_openai.py`

目标：

- 不写死 Windows 浏览器路径。
- headed 与 headless 都能通过参数或环境变量运行。
- 上传 fixture 使用脚本相对路径。
- 输出统一写到 `outputs/`。
- profile persistence 测试使用 Linux profile，验证第二次运行无需重新登录。

### 4.8 文档与环境模板

- 重写 `README.md` 为 Linux-first。
- `.env.example` 改为：

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_BASE_URL=
BROWSER_TYPE=chrome
BROWSER_PATH=/usr/bin/google-chrome
BROWSER_USER_DATA_PATH=outputs/browser_profiles/chrome
BROWSER_DEBUG_PORT=19222
BROWSER_HEADLESS=0
OUTPUT_DIR=outputs
```

- 保留 Windows 说明为兼容附录，不再称为唯一主路径。
- 原 Windows 计划书保留为历史资料，并新增醒目标记说明主运行环境已经切到 Linux。

## 5. Ubuntu / WSL2 环境安装

项目必须克隆到 WSL Linux 文件系统，不建议长期放在 `/mnt/c`：

```bash
mkdir -p ~/workspace
cd ~/workspace
git clone <GITHUB_REPOSITORY_URL> drission_agent
cd drission_agent
```

安装 Python 环境：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ca-certificates wget
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest -q
```

安装 Linux Google Chrome：

```bash
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
google-chrome --version
which google-chrome
```

WSLg 可视化检查（Windows 11 + 新版 WSL2 通常无需额外 X Server）：

```bash
echo "$DISPLAY"
echo "$WAYLAND_DISPLAY"
google-chrome --user-data-dir=/tmp/drission-agent-smoke https://example.com
```

若 Chrome 窗口能显示到 Windows 桌面，再运行：

```bash
source .venv/bin/activate
export BROWSER_PATH=/usr/bin/google-chrome
export BROWSER_HEADLESS=0
python scripts/smoke_drission.py
```

服务器/headless 验证：

```bash
export BROWSER_PATH=/usr/bin/google-chrome
export BROWSER_HEADLESS=1
python scripts/smoke_drission.py
python scripts/smoke_real_web_form.py
```

不要把 Windows 的 Chrome user-data-dir 拷入 Linux。Ubuntu 首次登录目标站点时，在 WSLg 浏览器中手工登录，之后复用 Linux profile。

## 6. 推荐迁移执行顺序

1. 从 GitHub 克隆 Windows baseline，在 Ubuntu 建立 `.venv`。
2. 不改代码先跑纯单元测试，记录初始失败。
3. 安装 Linux Chrome，完成 headed `smoke_drission.py`。
4. 改 `config.py` 和 `drission_runtime.py`，补平台与 headless 测试。
5. 完成真实 Selenium Web Form headed/headless 测试。
6. 改 `script_writer.py` 和模板，生成 Linux 独立脚本并回放。
7. 改 Debug Runner 的进程组、timeout 和 UTF-8 行为。
8. 验证上传文件、截图、输出目录和 profile persistence。
9. 使用 WSLg 手工登录 Viggle，复测 My Library 第二张图片流程。
10. 在 headless 模式选择不依赖人工登录的公开网站跑端到端测试。
11. 重写 README、`.env.example` 和运行命令。
12. 全量测试通过后，将 Linux 作为 main 分支默认环境，Windows 只做兼容回归。

每个阶段应单独提交，建议提交粒度：

```text
chore: add Linux runtime configuration
feat: discover Chrome across Linux and Windows
feat: support headed and headless browser modes
fix: make generated scripts platform independent
fix: terminate debug subprocess groups on Linux
docs: switch development guide to WSL Ubuntu
```

## 7. 验收清单

- `python -m pytest -q` 全部通过。
- headed smoke 能在 Windows 桌面显示 WSLg Linux Chrome。
- headless smoke 能在无窗口模式完成并截图。
- Runtime 能发现 `/usr/bin/google-chrome`。
- Agent 和生成脚本均不包含固定 Windows 盘符。
- Debug Runner 使用当前 venv 的 `sys.executable`。
- 生成脚本可离开源码包独立执行。
- 本地 HTML、Selenium Web Form、复杂动态页面至少各有一次真实测试。
- 上传使用 Linux fixture 路径并成功。
- 登录后 profile 可在第二次运行复用。
- plan mismatch 会 replan，认证页面只等待用户登录，不错误 replan。
- postcondition 失败时不能报告成功。
- `.env`、浏览器 profile、outputs 和账号数据未提交 Git。

## 8. 交给 Ubuntu Codex 的完整上下文

可将下面内容原样发给 Ubuntu 环境中的 Codex：

```text
你正在接手一个 Web 自动化脚本生成 Agent。面试题要求实现 Generation、Debugging、Resilience 三个模块，输入自然语言任务，输出经过真实浏览器执行验证和自动修复的独立 DrissionPage Python 脚本。

项目最初在 Windows 11 上开发，面试官现已确认运行环境是 Linux。请将项目迁移为 Linux-first / cross-platform，主开发环境为 WSL2 Ubuntu，使用 WSLg 显示 Linux Chrome 做可视调试；服务器使用 headless Chrome。不要重写环境无关的 Grounder/Planner/DOM 算法，只改必要的环境边界。

Windows baseline commit 是 5a0a7b7。当前已有能力：
- LLM Planner + ActionPlan 规范化和强校验
- 真实 DOM snapshot、raw/compact candidates 分离
- click/input/select/upload action-specific filtering
- Selector Grounder、selector fallback、重复 selector index
- 登录轮询与浏览器 profile 持久化
- plan mismatch 时携带历史轨迹、state、candidates replan
- My Library 等显式标签硬约束和 tab_selected postcondition
- 可见卡片几何排序和 related_item_rank
- 独立 DrissionPage 脚本生成、回放、截图、点击高亮、动作延时
- Windows 基线 52 个测试通过
- Viggle Mix -> Add Image Library -> My Library -> 第二张图片真实流程已成功

先完整阅读 LINUX_WSL_MIGRATION_HANDOFF.md。执行顺序：
1. 在 ~/workspace/drission_agent 创建 venv，安装 requirements.txt，先跑 pytest。
2. 安装 /usr/bin/google-chrome，确认 DISPLAY/WAYLAND_DISPLAY 和 WSLg 窗口。
3. 先跑 headed DrissionPage smoke，再改代码。
4. 修改 app/config.py：Linux Chrome 默认、BROWSER_HEADLESS、Path expanduser/resolve。
5. 修改 app/runtime/drission_runtime.py：shutil.which 查找 Linux Chrome/Chromium，平台分支，headed/headless，Linux profile 和端口处理。
6. 修改 app/generation/script_writer.py 及模板：去掉固定 Windows 路径，加入 Linux Chrome/headless，上传文件相对脚本定位，保持生成脚本独立。
7. 审核 app/debug/runner.py：继续使用 sys.executable，补 Linux 进程组 timeout/kill、cwd、UTF-8 和环境变量透传。
8. 更新 smoke scripts、README.md、.env.example 和测试。
9. 分别跑纯单测、headed smoke、headless smoke、公开真实网页 E2E、登录/profile persistence E2E。

安全要求：不要读取、打印或提交 .env 中的密钥；不要提交 outputs/ 或 browser profile；不要复制 Windows Chrome profile 到 Linux。遇到目标页面与 plan 不一致必须 replan；遇到登录必须等待用户登录并继续；每个动作的成功必须由 postcondition 或页面状态证明，不能只因 click() 没报错就判成功。

完成迁移后给出：修改文件、测试命令与结果、headed/headless 浏览器证据、剩余风险。继续后续功能开发时，以 Linux 为唯一验收主路径。
```

## 9. Linux 迁移完成后的后续开发

迁移不是项目终点。环境稳定后继续：

- 增强 Debug Fixer：失败分类、真实页面诊断、局部修复优先、修复后回归验证。
- 将 postcondition 从标签页扩展到表单提交、上传成功、列表选择、导航和下载。
- replan 增加轨迹压缩与已完成步骤去重，避免重复执行开弹窗等动作。
- 优化 LLM 调用延迟和 token，优先规则执行，只有不一致时才 replan。
- 增加多个复杂真实网站测试，避免针对 Viggle 写站点特例。
- 增加 Linux CI，至少覆盖 Python 单测与不依赖登录的 headless smoke。
- 整理最终演示、架构图、README 和面试说明。

## 10. Git 与安全说明

- `.env` 已被 `.gitignore` 排除。
- `outputs/*` 已排除，仅保留 `outputs/.gitkeep`。
- `.venv/`、缓存、日志和临时文件已排除。
- GitHub 只保存源码、测试、fixtures、需求文档和迁移文档。
- 推送前再次运行 secret scan 和 `git status`。
- 建议为 Windows baseline 创建 tag：`windows-baseline-20260620`。
- Linux 迁移在分支 `linux-migration` 开发，通过后合并 main。
