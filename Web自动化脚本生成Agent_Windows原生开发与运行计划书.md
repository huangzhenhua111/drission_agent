# Web 自动化脚本生成 Agent：Linux / WSL Ubuntu 开发与运行计划书

## 0. 结论

本项目现在应当以 **Linux / WSL2 Ubuntu** 作为主开发与主运行环境，Windows 只作为宿主机或历史兼容路径。

核心判断很简单：

- `Generation` 负责把自然语言拆成可执行计划，并在页面变化时重规划。
- `Resilience` 负责把每一个动作做稳，包括等待、重试、selector fallback、postcondition 和登录暂停。
- `Debugging` 负责在脚本失败后读懂失败、最小化修复、回归验证，而不是简单把错误扔回 LLM。

换句话说，Resilience 和 Debugging 不是“可有可无的辅助函数”，而是执行闭环的一部分。现在这两个部分都还不完整，所以后续开发必须围绕它们补齐。

---

## 1. 项目目标

把一句自然语言任务，变成一个经过真实浏览器验证、可自动修复、可独立运行的 DrissionPage Python 脚本。

输入示例：

```text
打开 https://viggle.ai/app，选择 mix 功能，在添加图片那里从我的历史库里面选第二张图片
```

输出必须包含：

- `action_plan.json`
- `captured_actions.json`
- `generation_trace.json`
- `generated_script.py`
- `script_run.json`
- `failure_context.json` 或调试修复产物

最终脚本必须是 **纯 DrissionPage 脚本**，不能依赖 Agent 内部模块、LLM、当前仓库代码或 `.env`。

---

## 2. 当前状态

### 已完成

- Planner 已能产出 ActionPlan。
- Action type 已规范化。
- 真实 DOM snapshot 已实现。
- Raw snapshot 与 compact candidates 已分离。
- click / input / select / upload 已按 action type 过滤候选。
- Selector Grounder 已有基础打分和选择规则。
- My Library / History 这类标签页已加过部分硬约束。
- 重复 selector 已记录 `index / match_count / unique`。
- 真实浏览器登录轮询已实现。
- `action-delay`、截图、postcondition、false success 检查已接入。
- Windows 真实页面验证已经跑通，说明核心链路可工作。

### 还不完整

- `DebugRunner` 仍然很薄，只是启动脚本。
- `ScriptFixer` 仍未真正实现。
- `DebugLoop` 仍未实现完整闭环。
- `Resilience` 还没有统一成一个明确的执行器层，很多逻辑仍散落在 Runtime / CaptureRunner / 生成脚本中。
- `replan` 还不够强，当前只覆盖“当前页和原计划不一致”的一部分场景。
- `fallback` 还偏规则化，不能覆盖所有复杂失败。

这份 Linux 版计划书的重点，就是把这些缺口补成明确任务。

---

## 3. 运行环境

主路径：

- WSL2 Ubuntu
- WSLg 可视化桌面
- Linux Google Chrome
- Python venv

建议目录：

```bash
~/workspace/drission_agent
```

推荐环境变量：

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

说明：

- Linux 下默认用 Chrome，不再把 Edge/Windows 路径当主路径。
- headed 调试用 WSLg。
- headless 用于 CI 或稳定回归。
- 不要把 Windows Chrome profile 复制到 Linux。

---

## 4. 总体架构

### 4.1 目标调用链

```text
Task
  -> Planner
  -> normalize / validate
  -> CaptureRunner
  -> DOM snapshot
  -> action-specific compact candidates
  -> Selector Grounder
  -> ResilientActionExecutor
  -> BrowserRuntime
```

### 4.2 脚本执行链

```text
generated_script.py
  -> standalone DrissionPage runtime
  -> selector fallback
  -> limited retry
  -> postcondition verify
  -> login pause / continue
```

### 4.3 Debug 链

```text
script failure
  -> DebugRunner
  -> failure context
  -> ScriptFixer
  -> minimal patch
  -> rerun
  -> either success or replan
```

---

## 5. Generation 模块

### 5.1 Planner

Planner 要做的不是“自由写几步”，而是输出一个可验证的、可重放的 ActionPlan。

必须具备：

- action type 规范化
- 必填字段校验
- 常见流程修复
- 当前页面状态下的 replan
- 对“第二张图片”“My Library tab”“Add Image Library button”这类表达的稳定理解

Planner 需要区分三件事：

1. `plan`：首次把自然语言拆成步骤。
2. `replan`：当前页面和计划不一致时，只重写剩余步骤。
3. `repair`：对明显错误或可归一的步骤做轻量修复。

### 5.2 Planner 现在要补的点

- 不要再把 `Library button` 这种泛化步骤和具体区域步骤混在一起。
- 对“添加图片那里”这种表达，必须知道它指向 Add Image 区域，不是 Add Motion。
- 对“我的历史库 / My Library”必须稳定保留为用户库，而不是 History。
- 当页面已经打开弹窗时，replan 不能重复再点一遍开库按钮。
- 当当前状态显示已登录、已进入目标页面时，不要把登录流程重新写进 plan。

### 5.3 DOM Snapshot

Snapshot 分两层：

- `raw_candidates`：保留调试字段，供人看、供 Debugger 用。
- `compact_candidates`：按 action type 压缩后给 Grounder。

`raw_candidates` 可以保留：

- `context_chain`
- `css_path`
- `ancestor_text`
- `rect`
- `aria_selected`
- `data_state`
- `data_attrs`

`compact_candidates` 只保留当前动作真正需要的信息。

### 5.4 Action-specific compact view

必须按动作类型过滤：

- `click`：只保留可点击、可见、语义合理的元素
- `input`：只保留输入框 / textarea / contenteditable
- `select`：只保留下拉框及选项
- `upload`：允许隐藏的 `input[type=file]` 和上传区

这一步不是单纯“把 JSON 变短”，而是要改变候选池。

### 5.5 Selector Grounder

Grounder 的职责是：

- 在真实候选中选目标
- 不凭空发明 selector
- 对同一目标的多个候选做排序
- 对显式标签名和显式 rank 做硬约束

必须继续强化的规则：

- `My Library tab` 只能命中真正的 My Library tab
- `History` 不能冒充 My Library
- `Add Image Library button` 只能命中 Add Image 区域对应的库按钮
- 对 `second image`，优先真正的第 2 个卡片，而不是页面上别的第 2 个 button

---

## 6. Resilience 模块

现在最需要补的就是这个模块。

### 6.1 设计原则

Resilience 要做成一个明确执行层，位置应该在：

```text
CaptureRunner / generated_script
    -> ResilientActionExecutor
    -> BrowserRuntime
```

而不是把容错散落在每个调用点。

### 6.2 需要覆盖的失败类型

1. selector 没找到
2. selector 不唯一
3. 找到了但元素不可见
4. 页面异步变化导致元素刚出现又失效
5. click 成功但页面状态没变
6. 上传控件点错区域
7. 登录页出现
8. 网络慢 / 页面加载慢

### 6.3 ResilientActionExecutor 应具备的能力

建议实现为统一执行器，提供这些方法：

- `click_with_fallback`
- `input_with_retry`
- `select_with_retry`
- `upload_with_retry`
- `wait_for_state`
- `verify_postcondition`
- `refresh_and_requery`

执行顺序建议：

1. 先按高优先级 selector 查找
2. 查找失败就换 fallback
3. fallback 仍失败就重新 snapshot
4. 如果页面状态不一致，交给 replan
5. 如果命中登录页，暂停等待用户登录
6. 如果动作后没有满足 postcondition，不能算成功

### 6.4 需要统一的等待策略

不要死等。

推荐策略：

- 短等待优先
- 每次等待有上限
- 总等待有上限
- 页面变化后再重新 snapshot

例如：

- selector wait：短超时
- 页面稳定 wait：中等超时
- 登录 wait：最长超时，但要周期轮询并提示用户

### 6.5 postcondition

这是防“看似成功实际上不成功”的关键。

必须至少支持：

- tab_selected
- url_contains
- title_contains
- visible_text_contains
- upload_area_updated
- result_card_selected

例如：

- 点 `My Library tab` 后，必须看到 `aria-selected=true`
- 点第二张图片后，必须看到相应 `Reuse` 或目标卡片状态发生变化

### 6.6 false success 的治理

任何满足以下情况都不能算成功：

- click 没报错，但页面没变
- 页面还停在登录页
- selector 命中了错误的同名元素
- `History` 被误判成 `My Library`
- 只点了 preview，没有真正选择资源

---

## 7. Debugging 模块

这部分现在最薄，要重点补。

### 7.1 DebugRunner

现在只负责启动脚本还不够。

需要补成：

- 启动独立子进程
- 捕获 stdout / stderr
- 保存退出码
- 保存失败截图
- 保存失败时的最后页面状态
- 保留脚本路径、环境变量摘要、浏览器 profile 路径
- Linux 下要能处理进程组结束，避免残留浏览器或子进程

### 7.2 failure_context

Debugger 看到的失败上下文必须够完整。

建议包含：

- task
- plan
- completed_actions
- failed_step
- current_state
- raw_candidates
- compact_candidates
- chosen_selector / fallback selectors
- selector metadata
- postcondition
- browser profile path
- screenshot path
- traceback / stderr

只给“报错字符串”是不够的。

### 7.3 ScriptFixer

ScriptFixer 不应该一上来就让 LLM 重写整份脚本。

优先顺序应该是：

1. 规则修复
2. 局部 patch
3. 只在结构性问题很大时才让 LLM 介入

典型规则修复：

- selector 顺序不稳
- tab 目标误判
- 重复开库步骤
- 登录后状态未验证
- 需要加短等待或重新 snapshot

### 7.4 DebugLoop

DebugLoop 要成为一个真正闭环：

```text
run script
-> collect failure context
-> classify failure
-> fix
-> rerun
-> if still failing, escalate
```

分类建议：

- `selector_miss`
- `wrong_target`
- `auth_required`
- `postcondition_failed`
- `network_or_load_delay`
- `runtime_exception`

### 7.5 Debugger 的目标

Debugger 的目标不是“把任何问题都硬修好”，而是：

- 找出失败属于哪一类
- 能规则修的先规则修
- 不能规则修的再给 LLM
- 每次修复后必须回归验证

---

## 8. Replan 策略

这是 Generation 和 Debugging 的交界。

### 8.1 什么情况必须 replan

- 当前页面状态和原计划明显不一致
- 计划需要的控件不存在
- 用户登录完成后页面结构变化
- 由于网络或异步加载，原候选集失效
- 原计划已执行的动作和当前页面冲突

### 8.2 什么情况不该 replan

- 只是某个 selector 查找慢
- 只是一个按钮暂时没 render 完
- 只是登录页，需要等用户继续登录
- 只是同一页面里同目标 selector 的 fallback

### 8.3 replan 输入必须包含

- 原任务
- 已完成动作
- 当前页面 state
- 当前 compact candidates
- 已知失败原因
- 当前 step

### 8.4 replan 输出范围

replan 只能返回：

- 当前页面之后的剩余步骤

不能：

- 重复已完成步骤
- 把错误的旧计划原样抄回来
- 把登录步骤重新塞进去

---

## 9. Runtime 层要做的事

Runtime 不只是“打开浏览器”。

它要提供：

- 浏览器发现
- profile 管理
- 页面打开
- snapshot
- click / input / select / upload 原语
- state 获取
- screenshot
- 显式等待

Linux 化时尤其要注意：

- `Path` 处理全部改为跨平台
- 浏览器路径不能写死 Windows 盘符
- 优先 `shutil.which()` 找 Linux Chrome
- headed/headless 要可切换
- 不复用 Windows 浏览器 profile

---

## 10. Script Writer 要补什么

生成脚本必须继续保持独立。

需要补的点：

- Linux 浏览器发现
- `BROWSER_HEADLESS`
- 相对脚本路径的上传文件解析
- 点击前高亮，便于现场观察
- 每步后短暂停顿，默认 2 秒更适合调试
- selector fallback 顺序
- postcondition 验证
- 登录轮询

脚本不能依赖仓库里的 `app.*` 模块，也不能依赖 Windows 目录。

---

## 11. 需要逐步完成的里程碑

### Milestone 1：Linux 环境跑通

- 在 WSL Ubuntu 建 venv
- 安装依赖
- Linux Chrome 可启动
- headed smoke 成功
- headless smoke 成功

### Milestone 2：Runtime Linux 化

- 浏览器发现改为 Linux-first
- profile 路径跨平台
- 进程关闭方式 Linux 化
- smoke tests 通过

### Milestone 3：Generation 完整闭环

- Planner 输出稳定
- `replan` 可以只返回剩余步骤
- candidate compactor 更稳
- Grounder 的目标约束更强
- 复杂真实页面可用

### Milestone 4：Resilience 成型

- 统一执行器落地
- selector fallback 正式进入执行层
- retry / wait / postcondition 全部接入
- false success 清理干净

### Milestone 5：Debugging 成型

- DebugRunner 完整化
- failure context 完整化
- ScriptFixer 先规则后 LLM
- DebugLoop 真正闭环

### Milestone 6：真实网站回归

- Viggle Mix
- Selenium Web Form
- 其他复杂动态页面
- 登录场景
- 上传场景
- 历史库 / 卡片选择场景

---

## 12. Ubuntu Codex 接手时必须知道的上下文

Ubuntu Codex 不要从“修一两个脚本”开始，而要按以下背景理解项目：

1. 这是一个 Web 自动化脚本生成 Agent。
2. 当前 Windows 基线已经跑通过真实浏览器。
3. 面试官环境是 Linux，所以主路径现在切 Linux / WSL Ubuntu。
4. 现在最缺的是 Debugger 和 Resilience，不是单纯的页面适配。
5. Generation 也还缺 replan 纪律和更强的 candidate 约束。

接手时最重要的优先级：

- 先让 Linux 环境跑通
- 再把 Runtime 和脚本生成改成 Linux-first
- 再补 Resilience 执行器
- 再补 Debugger 闭环

---

## 13. 建议交接给 Codex 的执行顺序

1. 阅读 `LINUX_WSL_MIGRATION_HANDOFF.md`
2. 阅读这份 Linux 计划书
3. 在 Ubuntu 克隆仓库并建 venv
4. 先跑纯单元测试
5. 安装 Linux Chrome
6. 跑 headed smoke
7. 跑 headless smoke
8. 改 `config.py` 和 `drission_runtime.py`
9. 改 `script_writer.py`
10. 完成 Resilience 执行器
11. 完成 Debugger 闭环
12. 回到真实网站做回归

---

## 14. 代码层优先关注文件

### 先看

- `app/config.py`
- `app/runtime/drission_runtime.py`
- `app/generation/planner.py`
- `app/generation/candidate_compactor.py`
- `app/generation/selector_grounder.py`
- `app/generation/capture_runner.py`
- `app/generation/script_writer.py`

### 然后补

- `app/debug/runner.py`
- `app/debug/fixer.py`
- `app/debug/loop.py`
- `app/resilience/retry.py`
- `app/resilience/waits.py`
- `app/resilience/assertions.py`

### 再更新

- `scripts/smoke_*`
- `README.md`
- `.env.example`
- tests

---

## 15. 验收标准

满足以下条件后才算 Linux 版计划执行成功：

- Ubuntu 下单测通过
- headed smoke 成功
- headless smoke 成功
- 独立生成脚本可运行
- false success 被拦住
- 登录时会暂停并继续
- 页面不一致时会 replan
- selector 失败时有 fallback
- Debugger 能输出有用失败上下文
- ScriptFixer 能做局部修复
- 真实复杂页面能稳定跑通

---

## 16. 给现在的结论

这份计划的核心变化只有一个：

**别把 Debugger 和 Resilience 当外围装饰，它们是执行闭环。**

Generation 负责“该做什么”。
Resilience 负责“怎么做稳”。
Debugging 负责“失败后怎么修回来”。

这三者在 Linux 上必须一起收口，后续代码才不会越做越散。
