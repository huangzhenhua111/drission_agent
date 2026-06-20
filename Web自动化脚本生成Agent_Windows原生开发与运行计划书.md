# Web 自动化脚本生成 Agent：Windows 原生开发与运行计划书

## 0. 最终结论

本项目采用 **Windows Native Mode** 作为唯一主路径。

也就是：

    开发环境：Windows 11 + VS Code + PowerShell + Python venv
    运行环境：Windows 11 + Python + DrissionPage + Chrome/Edge
    LLM 环境：OpenAI Python SDK + OPENAI_API_KEY
    最终产物：可独立运行的 DrissionPage Python 脚本

不再把 WSL 作为主开发或主运行路径。

WSL 只作为可选辅助环境，用于临时查文件、写文档、跑非浏览器单元测试；但 README、演示、验收、真实网站验证全部以 Windows PowerShell 为准。

## 1. 背景

本项目来自面试作业《Web 自动化脚本生成 Agent》。

题目要求借鉴 AgentBrowser 类 AI 浏览器代理的三模块设计：

1.  **Generation**

    - 将自然语言任务分解为浏览器操作序列。
    - 使用 DrissionPage 的 `ChromiumPage` 逐步执行。
    - 执行过程中捕获动作类型、元素定位信息、动作数据。
    - 根据捕获动作生成 DrissionPage Python 脚本。

2.  **Debugging**

    - 自动运行初版脚本。
    - 捕获执行失败、错误消息、失败行、页面状态、DOM 片段。
    - 必要时重新访问真实页面，结合失败现场和页面探测结果判断失败原因。
    - 优先执行规则修复，规则不足时调用 LLM 做最小脚本修复。
    - 循环“执行 → 失败 → 修复 → 重新执行”，直到通过或达到最大重试次数。

3.  **Resilience**

    - 选择器降级。
    - 智能等待。
    - 操作重试。
    - 统一异常处理。
    - 动作抽象。

最终输出的 `script_final.py` 必须是纯 DrissionPage 代码，不能包含 OpenAI API 调用、LLM 调用、Agent 内部模块依赖。

## 2. 用途和目标

## 2.1 用途

用户输入一句自然语言：

    打开 Selenium Web Form 页面，在文本框输入 hello，在下拉框选择 Two，然后点击 Submit。

系统自动完成：

    自然语言任务
      ↓
    生成高层操作计划
      ↓
    打开真实浏览器逐步执行
      ↓
    捕获真实动作和稳定选择器
      ↓
    生成初版 DrissionPage 脚本
      ↓
    自动运行验证
      ↓
    失败则调用 LLM 修复
      ↓
    输出 script_final.py

用户之后可以单独运行：

    python outputs\<run_id>\script_final.py

此时不需要 LLM，不需要 `OPENAI_API_KEY`，只需要 Python、DrissionPage 和 Chrome/Edge。

## 2.2 项目目标

本项目目标不是做万能 Web Agent，而是做一个稳定、可复现、能在面试中演示的最小可用系统。

必须完成：

1.  Windows PowerShell 原生运行完整 Agent。
2.  支持自然语言输入。
3.  支持 LLM 生成高层操作计划。
4.  支持 DrissionPage 打开真实浏览器逐步执行。
5.  支持 DOM Candidate Snapshot，减少 LLM 编造 selector。
6.  支持 CapturedAction 动作捕获。
7.  支持根据捕获动作生成初版脚本。
8.  支持自动运行初版脚本。
9.  支持失败后收集 `failure_context.json`。
10. 支持 Debug PageProbe 重新探测真实页面状态。
11. 支持 FailureAnalyzer 结合脚本错误和真实页面状态做失败归因。
12. 支持规则修复优先，必要时 LLM 做最小修复并输出完整脚本。
13. 支持最多 3 次 Debug 修复。
14. 支持输出最终独立脚本 `script_final.py`。
15. 支持至少 2 个本地稳定示例。
16. 支持至少 1 个真实网站示例。
17. 覆盖至少 4 种交互类型：
    - 页面导航
    - 点击
    - 文本输入
    - 下拉选择
    - 文件上传，作为增强项

## 3. 为什么选择 Windows 原生方案

### 3.1 原因一：最终控制的是 Windows 浏览器

DrissionPage 最终要控制 Chrome/Edge。

如果 Agent 系统、DrissionPage、浏览器和 Debug 脚本全部在 Windows 中运行，环境最一致：

    Windows Agent
      ↓
    DrissionPage
      ↓
    Windows Chrome / Edge
      ↓
    真实网站

不需要 WSL bridge，不需要跨系统路径转换，不需要跨进程 JSON worker，不需要解释 Linux 浏览器和 Windows 浏览器的差异。

### 3.2 原因二：面试演示最简单

主命令：

    python -m app.cli "打开某网站，搜索 xxx，并点击第一个结果"

演示结果：

    浏览器真实打开
    真实点击输入
    输出 script_final.py
    再单独运行 script_final.py

这个路径最容易讲清楚。

### 3.3 原因三：降低跨系统复杂度

不采用 WSL 主路径后，可以移除：

    powershell.exe bridge
    Windows Browser Worker
    WSL path -> Windows path 转换
    stdin/stdout JSON 通信
    跨系统浏览器控制

系统复杂度下降，调试成功率上升。

## 4. 总体架构

    agent-browser-drission/
      ├── app/
      │   ├── cli.py
      │   ├── config.py
      │   ├── llm/
      │   │   └── client.py
      │   ├── generation/
      │   │   ├── planner.py
      │   │   ├── dom_snapshot.py
      │   │   ├── selector_grounder.py
      │   │   ├── capture_runner.py
      │   │   ├── script_writer.py
      │   │   └── templates/
      │   │       └── drission_script.py.j2
      │   ├── runtime/
      │   │   ├── browser_runtime.py
      │   │   └── drission_runtime.py
      │   ├── debug/
      │   │   ├── runner.py
      │   │   ├── page_probe.py
      │   │   ├── failure_analyzer.py
      │   │   ├── rule_fixer.py
      │   │   ├── fixer.py
      │   │   └── loop.py
      │   ├── resilience/
      │   │   ├── actions.py
      │   │   ├── selectors.py
      │   │   ├── waits.py
      │   │   ├── retry.py
      │   │   └── assertions.py
      │   └── validation/
      │       └── static_checks.py
      ├── examples/
      │   ├── local_search/
      │   ├── local_form/
      │   └── real_selenium_web_form/
      ├── scripts/
      │   ├── smoke_python.py
      │   ├── smoke_drission.py
      │   └── smoke_openai.py
      ├── tests/
      ├── outputs/
      ├── requirements.txt
      ├── .env.example
      ├── README.md
      └── AGENT_BROWSER_DRISSION_WINDOWS_PLAN.md

## 5. 核心流程

    1. 用户输入自然语言任务
       ↓
    2. Planner 生成高层 ActionPlan
       ↓
    3. DrissionRuntime 打开浏览器页面
       ↓
    4. DOM Snapshot 抽取候选交互元素
       ↓
    5. Selector Grounder 基于候选元素选择目标
       ↓
    6. Capture Runner 逐步执行动作
       ↓
    7. 记录 CapturedAction
       ↓
    8. Script Writer 根据 CapturedAction 生成 script_initial.py
       ↓
    9. Debug Runner 自动运行 script_initial.py
       ↓
    10. 失败则写 failure_context.json
       ↓
    11. Debug PageProbe 重新探测真实页面状态
       ↓
    12. FailureAnalyzer 结合失败现场和页面探测结果做归因
       ↓
    13. RuleFixer 优先执行确定性修复
       ↓
    14. 必要时 Fixer 调 LLM 做最小修复并输出完整脚本
       ↓
    15. Static Checks 检查脚本无 Agent/LLM 依赖
       ↓
    16. 再运行
       ↓
    17. 通过后输出 script_final.py

## 6. Generation 模块设计

目录：

    app/generation/
      ├── planner.py
      ├── dom_snapshot.py
      ├── selector_grounder.py
      ├── capture_runner.py
      └── script_writer.py

## 6.1 Planner

Planner 输入自然语言，输出高层 ActionPlan。

Planner 只描述动作意图，不负责凭空编 selector。

示例输出：

    {
      "task": "打开 Selenium Web Form 页面，在文本框输入 hello，在下拉框选择 Two，然后点击 Submit。",
      "steps": [
        {
          "type": "goto",
          "url": "https://www.selenium.dev/selenium/web/web-form.html",
          "target": "Selenium Web Form 页面",
          "comment": "打开真实 Web Form 页面"
        },
        {
          "type": "input",
          "target": "Text input 文本框",
          "value": "hello",
          "comment": "输入 hello"
        },
        {
          "type": "select",
          "target": "Dropdown select 下拉框",
          "value": "Two",
          "select_by": "text",
          "comment": "选择 Two"
        },
        {
          "type": "click",
          "target": "Submit 按钮",
          "comment": "点击提交"
        }
      ],
      "success_assertions": [
        {
          "type": "url_contains",
          "value": "submitted-form.html"
        }
      ]
    }

## 6.2 DOM Snapshot

每一步执行前，系统从真实页面 DOM 中抽取候选元素。

候选元素包括：

    a
    button
    input
    textarea
    select
    option
    label
    [role]
    [onclick]
    [tabindex]
    [data-*]

每个候选元素记录：

    {
      "candidate_id": "e12",
      "tag": "button",
      "text": "Submit",
      "id": null,
      "name": null,
      "type": "submit",
      "role": null,
      "aria_label": null,
      "placeholder": null,
      "value": null,
      "data_attrs": {},
      "is_visible": true,
      "selector_candidates": [
        "text=Submit",
        "css:button[type='submit']",
        "css:button"
      ]
    }

目的：

    不让 LLM 凭空猜 selector。
    LLM 只能基于真实页面候选元素选择目标。

## 6.3 Selector Grounder

Selector Grounder 输入：

    当前 ActionStep
    当前页面候选元素
    任务上下文
    已经完成的动作

输出：

    {
      "target": "Submit 按钮",
      "candidate_id": "e12",
      "selectors": [
        "text=Submit",
        "css:button[type='submit']",
        "css:button"
      ],
      "reason": "候选元素文本为 Submit，类型为 submit，符合目标按钮"
    }

规则：

1.  只能选择 DOM Snapshot 中存在的 candidate。
2.  优先稳定 selector。
3.  不允许只输出长 XPath。
4.  “第一个结果”必须理解为业务语义上的结果列表第一条，不是 DOM 中第一个按钮或第一个链接。

## 6.4 Capture Runner

Capture Runner 负责逐步执行 ActionPlan。

每一步：

    snapshot 当前页面
      ↓
    ground 当前目标
      ↓
    用 fallback selectors 执行动作
      ↓
    记录真实成功的 selector
      ↓
    保存 CapturedAction

输出：

    outputs/<run_id>/action_plan.json
    outputs/<run_id>/captured_actions.json
    outputs/<run_id>/generation_trace.json
    outputs/<run_id>/generation.log

CapturedAction 示例：

    {
      "step_index": 2,
      "type": "select",
      "target": "Dropdown select 下拉框",
      "comment": "选择 Two",
      "chosen_selector": "css:select[name='my-select']",
      "fallback_selectors": [
        "css:select[name='my-select']",
        "css:select"
      ],
      "select_by": "text",
      "value": "Two",
      "before_url": "https://www.selenium.dev/selenium/web/web-form.html",
      "after_url": "https://www.selenium.dev/selenium/web/web-form.html",
      "before_title": "Web form",
      "after_title": "Web form"
    }

## 7. Runtime 模块设计

目录：

    app/runtime/
      ├── browser_runtime.py
      └── drission_runtime.py

只实现一个主 runtime：

    DrissionRuntime

职责：

    启动 ChromiumPage
    打开页面
    获取页面状态
    抽取 DOM candidate
    点击
    输入
    下拉选择
    上传文件
    截图
    关闭浏览器

基础接口：

    class BrowserRuntime:
        def start(self): ...
        def goto(self, url: str): ...
        def snapshot(self): ...
        def click(self, selectors: list[str], target: str): ...
        def input(self, selectors: list[str], value: str, target: str): ...
        def select(self, selectors: list[str], value: str, by: str, target: str): ...
        def upload(self, selectors: list[str], path: str, target: str): ...
        def state(self): ...
        def close(self): ...

## 8. Script Writer 设计

Script Writer 不直接基于 ActionPlan 写脚本，而是基于 `captured_actions.json` 写脚本。

原因：

    ActionPlan 是意图。
    CapturedAction 是真实执行成功过的动作。

生成：

    outputs/<run_id>/script_initial.py

最终通过 Debug 后复制为：

    outputs/<run_id>/script_final.py

生成脚本必须包含：

    from DrissionPage import ChromiumPage
    main()
    find_first()
    click_any()
    input_any()
    select_any()
    upload_any()
    assert_any()
    wait_page_ready()
    write_result()
    write_failure_context()
    try/except

禁止包含：

    openai
    langchain
    dotenv
    app.*
    OPENAI_API_KEY
    sk-
    Agent
    LLM

## 9. DrissionPage API 使用约束

本项目默认按 DrissionPage 4.1.1.4 设计。

页面等待：

    page.wait.doc_loaded(timeout=10)

元素等待：

    page.wait.ele_displayed(selector, timeout=8, raise_err=False)

查找元素：

    page.ele(selector, timeout=timeout)
    page.eles(selector)

点击：

    ele.click()

输入：

    ele.input(value)

下拉选择：

    ele.select.by_text(value)
    ele.select.by_value(value)
    ele.select.by_index(index)

注意：`by_index()` 从 1 开始，不按 Python list 的 0 开始处理。

文件上传：

    file_input.input(r"C:\agent_browser_drission\examples\local_form\site\upload_fixture.txt")

不允许在模板里写：

    page.wait.load_complete()

因为 4.x 已改为：

    page.wait.doc_loaded()

所有 API 必须经过 `scripts/smoke_drission.py` 本机验证后再固定。

## 10. Debugging 模块设计

目录：

    app/debug/
      ├── runner.py
      ├── page_probe.py
      ├── failure_analyzer.py
      ├── rule_fixer.py
      ├── fixer.py
      └── loop.py

## 10.1 Runner

Runner 在 Windows 当前 venv 中运行候选脚本：

    python outputs\<run_id>\script_initial.py

捕获：

    exit_code
    stdout
    stderr
    timeout
    result.json
    failure_context.json

每次运行产物：

    outputs/<run_id>/
      ├── stdout_attempt_0.txt
      ├── stderr_attempt_0.txt
      ├── result.json
      └── failure_context.json

## 10.2 failure_context.json

候选脚本失败时，脚本自身写出：

    {
      "ok": false,
      "error_type": "RuntimeError",
      "error_message": "元素定位失败：Submit 按钮",
      "failed_step_index": 3,
      "failed_step": "点击 Submit 按钮",
      "failed_action_type": "click",
      "last_successful_step_index": 2,
      "current_url": "...",
      "title": "...",
      "html_excerpt": "...",
      "screenshot_path": "screenshots/failure_attempt_0.png",
      "page_source_path": "debug/page_source_attempt_0.html",
      "traceback": "...",
      "replay_hint": {
        "can_replay_prefix": true,
        "resume_url": "...",
        "prefix_actions": [0, 1, 2]
      }
    }

原因：

    脚本进程失败退出后，Debug 模块不能再访问脚本里的 page 对象。
    所以失败上下文必须由脚本自身落盘。

但这还不够。

    failure_context.json 是失败脚本自救留下的现场。
    它能说明“脚本当时看到了什么”，但不一定足以说明“真实页面现在是什么状态”。
    因此 Debug 模块还必须具备重新访问真实页面并二次探测的能力。

## 10.3 Debug PageProbe

PageProbe 是 Debug 阶段的真实页面探测器。

它不依赖失败脚本进程里已经死亡的 `page` 对象，而是在 Debug 进程中重新启动 DrissionPage，主动获取真实页面状态。

职责：

    重新打开 failure_context.json 中的 current_url / resume_url
    必要时从 captured_actions.json 回放到失败前一步
    重新抽取 DOM candidates
    获取当前 URL / title / html excerpt
    截图
    检查失败 selector 是否存在、可见、可点击
    检查下拉选项、文件 input、断言目标元素等关键状态

两种探测模式：

    state_probe
        直接打开失败时 URL，检查页面当前 DOM。
        适合 selector_not_found、element_not_visible、assertion_failed 等问题。

    replay_probe
        新开浏览器，从 captured_actions.json 回放到 last_successful_step_index。
        然后在失败动作之前重新 snapshot。
        适合页面状态依赖前序操作的任务，例如搜索结果、表单提交前状态。

PageProbe 输出：

    outputs/<run_id>/debug/page_probe_attempt_0.json
    outputs/<run_id>/debug/dom_candidates_attempt_0.json
    outputs/<run_id>/screenshots/probe_attempt_0.png

`page_probe_attempt_0.json` 示例：

    {
      "probe_ok": true,
      "probe_mode": "replay_probe",
      "url": "...",
      "title": "...",
      "failed_selector_checks": [
        {
          "selector": "text=Submit",
          "exists": true,
          "visible": true,
          "count": 1
        },
        {
          "selector": "css:button[type='submit']",
          "exists": true,
          "visible": true,
          "count": 1
        }
      ],
      "nearby_candidates": [
        {
          "candidate_id": "e12",
          "tag": "button",
          "text": "Submit",
          "selector_candidates": [
            "text=Submit",
            "css:button[type='submit']"
          ]
        }
      ],
      "diagnostic_notes": [
        "失败 selector 在 replay_probe 中存在且可见，优先判断为等待不足或点击时机问题。"
      ]
    }

边界：

    PageProbe 只用于诊断和生成修复上下文。
    PageProbe 不直接修改脚本。
    真实网站探测必须限制在自动化测试友好的页面，不处理登录、验证码、支付、下单等高风险动作。

## 10.4 Fixer

Fixer 不是在失败后把 `failure_context.json` 丢给 LLM，让 LLM 从零重写脚本。

正确定位：

    Fixer = 受约束的脚本修复器
    目标 = 最小修复失败点
    输出 = 修复后的完整 Python 脚本

也就是说：

    输出完整脚本，是为了避免 diff 应用失败、缩进错乱、上下文错位。
    但修复策略必须是最小修复，不允许自由重写整个任务流程。

### 10.4.1 FailureAnalyzer

Fixer 调用 LLM 前，Debug 模块先做确定性诊断。

输入：

    stdout
    stderr
    traceback
    failure_context.json
    page_probe_attempt_N.json
    dom_candidates_attempt_N.json
    当前脚本全文
    失败 step 信息
    当前 URL / title / html_excerpt

输出失败类型：

    selector_not_found
    element_not_visible
    page_not_loaded
    wrong_drission_api
    select_option_not_found
    upload_path_error
    assertion_failed
    network_or_site_changed
    unknown_runtime_error

作用：

    把“脚本报错信息”和“真实页面探测结果”合并判断。
    能规则修复的先规则修复。
    不能规则修复的，再交给 LLM Fixer。
    LLM Fixer 必须知道失败归因，不能盲修。

### 10.4.2 规则修复优先

以下问题优先使用本地规则修复，不直接调用 LLM：

    缺少 doc_loaded 等待
    wait.load_complete() 这类旧 API
    Windows 路径字符串未使用 raw string
    result.json / failure_context.json 写入路径错误
    最终脚本误 import openai / app / dotenv
    明显的语法错误、缩进错误、缺少 main 入口

规则修复后仍然需要：

    静态检查
    自动运行
    断言验证

### 10.4.3 LLM 最小修复

只有当规则修复不足以处理失败时，才调用 LLM。

LLM 输入：

    原始自然语言任务
    action_plan.json
    captured_actions.json
    当前脚本全文
    stdout
    stderr
    failure_context.json
    page_probe_attempt_N.json
    dom_candidates_attempt_N.json
    debug.log
    FailureAnalyzer 失败归因
    失败步骤对应的 CapturedAction
    失败时重新抽取的 DOM candidates
    DrissionPage API 约束
    最终脚本禁止依赖规则

LLM 修复约束：

    必须保留原脚本中已经成功执行的动作。
    必须保留通用 helper，例如 find_first、click_any、input_any、select_any、upload_any、assert_any。
    必须优先修复失败点附近的 selector、等待、重试、断言或 API 用法。
    不允许重新规划整个任务。
    不允许删除 result.json / failure_context.json 写入逻辑。
    不允许引入 openai、langchain、dotenv、app.*、OPENAI_API_KEY。
    不允许把脚本改成依赖 Agent 内部模块。
    不允许把已捕获成功的稳定 selector 改成唯一长 XPath。

LLM 输出 JSON：

    {
      "failure_type": "selector_not_found",
      "repair_note": "修复说明",
      "changed_scope": "只修改点击 Submit 按钮的 selector fallback 和等待逻辑",
      "script": "修复后的完整 Python 脚本"
    }

不接受 diff，只接受完整脚本。

原因：

    diff 在 LLM 输出中容易出现上下文不匹配、缩进错乱、补丁应用失败。
    完整脚本更容易直接落盘、静态检查和重新运行。

但完整脚本必须满足“最小修复”约束。

### 10.4.4 修复后检查

每次修复后必须执行：

    语法检查
    静态依赖检查
    禁止 token 检查
    自动运行脚本
    断言验证

只有全部通过，才允许写入 `script_final.py`。

## 10.5 Debug Loop

最大重试：

    3 次

流程：

    script_initial.py
      ↓
    运行
      ↓
    成功？
      ├── 是：保存为 script_final.py
      └── 否：
            读取 failure_context.json
            PageProbe 重新打开真实页面或回放到失败前一步
            生成 page_probe_attempt_N.json 和 dom_candidates_attempt_N.json
            FailureAnalyzer 诊断失败类型
            尝试规则修复
            规则修复不可行时，调用 LLM 做最小修复
            静态检查
            生成 script_attempt_1.py
            重新运行

输出：

    outputs/<run_id>/debug.log

## 11. Resilience 模块设计

目录：

    app/resilience/
      ├── actions.py
      ├── selectors.py
      ├── waits.py
      ├── retry.py
      └── assertions.py

## 11.1 选择器优先级

排序：

    id
      > name
      > aria-label
      > data-testid / data-* 属性
      > placeholder
      > role + text
      > type + text
      > 可见文本
      > 短 CSS
      > 短 XPath

禁止把下面这种长 XPath 作为唯一定位方式：

    /html/body/div[1]/div[2]/div[3]/button[1]

## 11.2 find_first helper

最终脚本内嵌：

    def find_first(page, selectors, timeout=8, index=None):
        errors = []

        for selector in selectors:
            try:
                ok = page.wait.ele_displayed(selector, timeout=timeout, raise_err=False)
                if not ok:
                    errors.append(f"{selector}: not displayed")
                    continue

                if index is None:
                    ele = page.ele(selector, timeout=timeout)
                else:
                    eles = page.eles(selector)
                    if len(eles) <= index:
                        errors.append(f"{selector}: index {index} out of range")
                        continue
                    ele = eles[index]

                if ele:
                    return ele, selector

            except Exception as exc:
                errors.append(f"{selector}: {type(exc).__name__}: {exc}")

        raise RuntimeError("元素定位失败：" + " | ".join(errors))

## 11.3 断言策略

最终脚本不能只判断“没报错”。

每个示例至少包含一种断言：

    url_contains
    title_contains
    element_text_contains
    element_displayed

示例：

    def assert_url_contains(page, text):
        if text not in page.url:
            raise AssertionError(f"URL 断言失败：期望包含 {text}，实际 {page.url}")

## 12. 环境依赖

## 12.1 Windows 环境

建议：

    Windows 11
    Python 3.11+
    Chrome 或 Edge
    DrissionPage 4.1.1.4

项目路径必须使用英文路径，避免中文路径和空格：

    C:\agent_browser_drission

不要放在：

    C:\Users\中文用户名\Desktop\新建文件夹

## 12.2 创建虚拟环境

PowerShell：

    cd C:\
    mkdir agent_browser_drission
    cd agent_browser_drission

    py -m venv .venv
    .\.venv\Scripts\Activate.ps1

    python -m pip install --upgrade pip

PowerShell 执行策略报错时：

    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    .\.venv\Scripts\Activate.ps1

也可以不激活 venv，直接用：

    .\.venv\Scripts\python.exe -m app.cli ...

## 12.3 requirements.txt

    DrissionPage==4.1.1.4
    openai
    python-dotenv
    pydantic
    jinja2
    rich
    beautifulsoup4
    pytest

安装：

    pip install -r requirements.txt

如果本机安装不到 `DrissionPage==4.1.1.4`，不要直接乱改代码。先执行：

    python -m pip index versions DrissionPage

确认可安装版本后，统一修改：

    requirements.txt
    README.md
    smoke_drission.py
    脚本模板

## 12.4 .env.example

    OPENAI_API_KEY=your_api_key
    OPENAI_MODEL=your_model_name
    BROWSER_TYPE=chrome
    OUTPUT_DIR=outputs

`.env` 必须加入 `.gitignore`。

## 13. Smoke Test

必须先写 3 个预检脚本。

## 13.1 Python 预检

`scripts/smoke_python.py`

    import sys
    print(sys.executable)
    print(sys.version)

运行：

    python scripts\smoke_python.py

成功条件：

    打印当前 venv 的 python.exe 路径
    打印 Python 版本
    退出码为 0

## 13.2 DrissionPage 预检

`scripts/smoke_drission.py`

    from DrissionPage import ChromiumPage

    page = ChromiumPage()
    page.get("https://example.com")
    page.wait.doc_loaded(timeout=10)
    print(page.title)
    page.quit()

运行：

    python scripts\smoke_drission.py

成功条件：

    浏览器打开
    页面加载
    打印标题
    退出码为 0

## 13.3 OpenAI 预检

`scripts/smoke_openai.py`

作用：

    读取 .env
    检查 OPENAI_API_KEY 是否存在
    调用一次最小 LLM 请求
    打印模型返回

成功条件：

    API key 能读取
    LLM 请求成功
    退出码为 0

## 14. 示例设计

## 14.1 示例一：local_search

任务：

    打开本地搜索页面，搜索 alpha，并点击第一个结果。

页面：

    examples/local_search/site/index.html
    examples/local_search/site/detail-alpha.html

覆盖：

    页面导航
    文本输入
    点击搜索按钮
    点击第一条搜索结果
    URL/title 断言

作用：

    稳定展示完整 Generation + Capture + Script Writer + Debug 流程。

## 14.2 示例二：local_form

任务：

    打开本地表单页面，在姓名输入框输入 Alice，在类型下拉框选择 Two，上传 upload_fixture.txt，然后点击提交。

页面：

    examples/local_form/site/index.html
    examples/local_form/site/submitted.html
    examples/local_form/site/upload_fixture.txt

覆盖：

    页面导航
    文本输入
    下拉选择
    文件上传
    点击提交
    元素文本断言

作用：

    覆盖题目要求的 4 种以上交互类型。

## 14.3 示例三：real_selenium_web_form

任务：

    打开 Selenium Web Form 页面，在文本框输入 hello，在下拉框选择 Two，然后点击 Submit。

页面：

    https://www.selenium.dev/selenium/web/web-form.html

覆盖：

    真实网站访问
    页面导航
    文本输入
    下拉选择
    点击提交
    URL 断言

作用：

    证明系统不是只能跑本地 HTML，也能控制 Windows 真实浏览器访问公网真实网站。

注意：

    真实网站示例可能受网络状态影响。
    如果失败，README 中必须说明先运行 smoke_drission.py 和网络检查。

## 15. CLI 设计

主命令：

    python -m app.cli "打开本地搜索页面，搜索 alpha，并点击第一个结果。"

支持参数：

    python -m app.cli --task-file examples\local_search\task.txt
    python -m app.cli --task-file examples\local_form\task.txt
    python -m app.cli --task-file examples\real_selenium_web_form\task.txt
    python -m app.cli --mock-llm --task-file examples\local_search\task.txt
    python -m app.cli --capture-only --task-file examples\local_search\task.txt
    python -m app.cli --no-debug --task-file examples\local_search\task.txt
    python -m app.cli --max-retries 3 --task-file examples\real_selenium_web_form\task.txt

## 16. 输出目录

每次运行生成：

    outputs/<run_id>/
      ├── task.txt
      ├── action_plan.json
      ├── dom_snapshots/
      ├── captured_actions.json
      ├── generation_trace.json
      ├── script_initial.py
      ├── script_attempt_1.py
      ├── script_attempt_2.py
      ├── script_final.py
      ├── debug.log
      ├── debug/
      │   ├── page_probe_attempt_0.json
      │   ├── dom_candidates_attempt_0.json
      │   └── failure_analysis_attempt_0.json
      ├── result.json
      ├── failure_context.json
      ├── stdout_attempt_0.txt
      ├── stderr_attempt_0.txt
      └── screenshots/

## 17. 静态检查

`app/validation/static_checks.py` 必须检查 `script_final.py`。

允许 import：

    DrissionPage
    json
    sys
    os
    time
    traceback
    pathlib
    typing

禁止 import：

    openai
    langchain
    dotenv
    app
    requests
    httpx

禁止文本：

    OPENAI_API_KEY
    sk-
    llm
    agent.run
    from app
    import app

必须包含：

    from DrissionPage import ChromiumPage
    main()
    if __name__ == "__main__":
    result.json
    failure_context.json
    try
    except

每个 click/input/select/upload 动作必须走 helper，不允许裸写一堆不可控的 `page.ele(...).click()`。

## 18. 接入成功判断条件

## 18.1 环境接入成功

运行：

    python scripts\smoke_python.py
    python scripts\smoke_drission.py
    python scripts\smoke_openai.py

全部退出码为 0。

## 18.2 Generation 成功

运行：

    python -m app.cli --mock-llm --capture-only --task-file examples\local_search\task.txt

成功标准：

    生成 action_plan.json
    生成 captured_actions.json
    captured_actions.json 至少包含 goto/input/click/click
    每个非 goto 动作都有 chosen_selector
    每个非 goto 动作都有 fallback_selectors

## 18.3 Debug 成功

运行：

    python -m app.cli --task-file examples\local_search\task.txt --max-retries 3

成功标准：

    script_initial.py 存在
    script_final.py 存在
    debug.log 存在
    如果发生失败重试，debug/page_probe_attempt_N.json 存在
    如果发生失败重试，debug/failure_analysis_attempt_N.json 存在
    result.json 存在
    result.json 中 ok=true

## 18.4 最终脚本独立运行成功

运行：

    python outputs\<run_id>\script_final.py

成功标准：

    不需要 OPENAI_API_KEY
    不 import openai
    不 import app.*
    浏览器能自动完成任务
    result.json 中 ok=true
    退出码为 0

## 18.5 真实网站示例成功

运行：

    python -m app.cli --task-file examples\real_selenium_web_form\task.txt --max-retries 3

成功标准：

    浏览器打开真实网站
    完成输入
    完成下拉选择
    完成提交
    输出 script_final.py
    script_final.py 可独立二次运行

## 19. Codex 实现顺序

## Milestone 0：项目初始化

完成：

    目录结构
    requirements.txt
    .env.example
    .gitignore
    README.md 初稿

验收：

    python -m pytest

## Milestone 1：Smoke Tests

完成：

    scripts/smoke_python.py
    scripts/smoke_drission.py
    scripts/smoke_openai.py

验收：

    python scripts\smoke_python.py
    python scripts\smoke_drission.py
    python scripts\smoke_openai.py

## Milestone 2：Runtime

完成：

    app/runtime/browser_runtime.py
    app/runtime/drission_runtime.py

能力：

    start
    goto
    snapshot
    click
    input
    select
    upload
    state
    close
    screenshot

验收：

    python -m pytest tests\test_runtime_smoke.py

## Milestone 3：DOM Candidate + Selector Builder

完成：

    app/generation/dom_snapshot.py
    app/resilience/selectors.py

能力：

    抽取交互元素
    生成 candidate_id
    生成 selector_candidates
    按稳定性排序
    过滤不可见元素
    禁止长 XPath 作为唯一 selector

验收：

    python -m pytest tests\test_candidate_extractor.py tests\test_selector_priority.py

## Milestone 4：Planner + Selector Grounder

完成：

    app/generation/planner.py
    app/generation/selector_grounder.py
    app/llm/client.py

先支持 `--mock-llm`，再接真实 LLM。

验收：

    python -m app.cli --mock-llm --plan-only --task-file examples\local_search\task.txt

## Milestone 5：Capture Runner

完成：

    app/generation/capture_runner.py

验收：

    python -m app.cli --mock-llm --capture-only --task-file examples\local_search\task.txt

## Milestone 6：Script Writer

完成：

    app/generation/script_writer.py
    app/generation/templates/drission_script.py.j2

验收：

    python -m app.cli --mock-llm --no-debug --task-file examples\local_search\task.txt

生成：

    script_initial.py

## Milestone 7：Debug Diagnostics + Loop + Fixer

完成：

    app/debug/runner.py
    app/debug/page_probe.py
    app/debug/failure_analyzer.py
    app/debug/rule_fixer.py
    app/debug/fixer.py
    app/debug/loop.py
    app/validation/static_checks.py

验收：

    python -m app.cli --task-file examples\local_search\task.txt --max-retries 3

## Milestone 8：示例与 README

完成：

    examples/local_search/
    examples/local_form/
    examples/real_selenium_web_form/
    README.md

验收：

    python -m app.cli --task-file examples\local_search\task.txt
    python -m app.cli --task-file examples\local_form\task.txt
    python -m app.cli --task-file examples\real_selenium_web_form\task.txt

## 20. README 必须写清楚

README 至少包括：

    1. 项目背景
    2. AgentBrowser 三模块思想
    3. 本项目如何映射 Generation / Debugging / Resilience
    4. 为什么采用 Windows Native Mode
    5. 环境安装步骤
    6. OPENAI_API_KEY 配置
    7. DrissionPage smoke test
    8. CLI 使用方法
    9. 三个示例的运行方法
    10. 输出文件说明
    11. Debug Loop 说明
    12. Resilience 策略说明
    13. 最终脚本为什么能独立运行
    14. 静态检查规则
    15. 已知限制和风险

## 21. 注意事项

### 21.1 项目路径必须简单

推荐：

    C:\agent_browser_drission

不要使用：

    中文路径
    空格路径
    OneDrive 同步目录
    桌面临时目录

### 21.2 不要把 API Key 写进代码

`.env` 可以有：

    OPENAI_API_KEY=...

但 `.env` 不能提交。

最终脚本不能读取 `.env`，不能包含 OpenAI 相关代码。

### 21.3 不要让 LLM 凭空编 selector

错误做法：

    LLM 直接说点击 @id=submitBtn

正确做法：

    先 snapshot 真实 DOM
    再从候选元素里选目标
    再生成 fallback selectors

### 21.4 “第一个结果”不是第一个按钮

规则：

    第一个结果 = 搜索结果列表中的第一条业务结果

不是：

    DOM 中第一个 button
    页面中第一个 a
    页面上第一个可点击元素

### 21.5 主验收不依赖高风险网站

不要选：

    百度
    Google
    淘宝
    京东
    知乎
    小红书
    需要登录的网站
    有验证码的网站
    支付/下单/发邮件网站

真实网站示例优先选自动化测试友好的公开页面。

## 22. 风险点

以下风险只有在 smoke test 和本地示例都跑通后还解决不了时，再写进 README 的“已知限制”。

### 22.1 DrissionPage 版本差异

处理方式：

    固定 requirements.txt
    所有 API 以 smoke_drission.py 验证结果为准

### 22.2 Chrome / Edge 启动失败

处理方式：

    先运行 smoke_drission.py
    必要时在 README 中说明如何设置浏览器路径

### 22.3 真实网站网络失败或改版

处理方式：

    本地示例作为稳定主验收
    真实网站示例作为真实能力证明
    失败时输出明确网络/DOM 改版原因

### 22.4 LLM 输出不稳定

处理方式：

    Planner 和 Fixer 都要求 JSON 输出
    用 Pydantic 校验
    失败时自动重试一次格式修复
    保留 --mock-llm 保证本地演示可复现

### 22.5 复杂网站语义理解失败

处理方式：

    MVP 明确支持搜索、表单、点击、下拉、上传等常见任务
    复杂登录、验证码、支付、风控任务不作为本项目承诺范围
