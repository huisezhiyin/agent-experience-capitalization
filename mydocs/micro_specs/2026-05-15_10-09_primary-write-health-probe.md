# Spec: primary write health probe for expcap review commands

## Goal
- 要解决什么问题：`status/doctor/dashboard` 在受限环境下可能仍能读到有效数据，但主路径 `~/.expcap` 写侧已经退化到 fallback，当前摘要不够直接。
- 最终目标：让 review/diagnostic 输出显式暴露主路径写健康，区分 primary writable、fallback only、hard failure。
- 本轮核心目标：为 status/doctor/dashboard 增加轻量主路径写健康探针与诊断展示。
- 验收结果：受限/正常两类测试都能稳定区分写侧状态，doctor 能直接给出对应结论。

## Done Contract
- 什么算完成：CLI payload 中新增主路径写健康摘要，doctor 检查项能覆盖该摘要。
- 由什么证明：相关单测通过，并在本地真实跑一次 `status/doctor/dashboard` 输出可见。
- 哪些情况仍算未完成：只有 fallback warning 文案变化、但没有新的结构化状态字段或诊断结论。

## Scope
- In: `runtime/cli/main.py` 的 status/doctor/dashboard 写健康探针、摘要与测试。
- Out: 修改 automation 平台权限策略；重做现有 fallback 持久化模型。
- 用户已切分的任务单元：继续推进 daily review 暴露的主路径写入退化问题。
- 轻量评估：足够小

## Facts / Constraints
- 已确认事实：当前全权限会话下 `auto-start` 已能写回主路径；昨日 automation 审阅里 `dashboard/status/doctor/auto-finish` 曾全部 fallback 到 `/var/folders/...`。
- 技术/业务约束：不能破坏现有 fallback 写入路径；诊断输出要能区分 permission/sandbox 与真实 runtime failure。
- 已知风险：写健康探针如果设计成真实写入，必须保证无残留、低副作用。

## Open Questions
- [x] 探针是否需要真实写文件，还是只做权限推断：优先做短生命周期真实 probe，避免 `os.access` 误判。

## Restated Understanding
- 我理解当前任务是：继续推进 expcap daily review 暴露的主路径写入退化问题，把“写侧是否健康”做成结构化可观测信号。
- 当前核心目标是：让 `status/doctor/dashboard` 直接显示 primary write health，而不是让用户从 fallback warning 反推。
- 当前边界是：只改 review/diagnostic 相关 CLI 和测试，不动更大的存储架构。
- 暂不处理：automation 宿主本身的权限策略。

## Goal Alignment Check
- 当前动作是否仍服务于核心目标：是。
- 模型当前路径是否仍在用户边界内：是。
- 是否出现更适合代码地形的水流路径：有，先补探针和诊断，比直接改 fallback 策略更稳。
- 若否，偏差在哪里：无。
- 是否需要调整本轮目标或范围：否。

## Checkpoint Summary
- 当前任务理解：补足写侧主路径健康可观测性。
- 当前核心目标：明确区分 primary writable / fallback only / hard failure。
- 当前进度：已确认问题更偏环境相关，正在落诊断增强方案。
- 下一步 1: 实现主路径写探针和状态摘要。
- 下一步 2: 接入 doctor/dashboard，并补单测。
- 涉及文件 / 模块：`runtime/cli/main.py`、`tests/test_cli_flow.py`
- 风险：探针副作用、测试 mock 颗粒度不够。
- 验证方式：单测 + 真实 CLI `status/doctor/dashboard`
- Execution Approval: `Approved`

## Change Log
- 2026-05-15: 确认本轮以“写健康探针 + 诊断展示”为主，不直接改 storage fallback 机制。
- 2026-05-15: 已在 `runtime/cli/main.py` 增加 `primary_write_health` 探针，接入 status/doctor/dashboard，并补充 5 条针对性测试。

## Validation
- Self-check: 写探针采用短生命周期临时文件，执行后立即删除，不改变持久化语义。
- Static checks: `python3 -m py_compile runtime/cli/main.py tests/test_cli_flow.py`
- Runtime / Test: `python3 -m unittest tests.test_cli_flow.CliFlowTests.test_build_primary_write_health_reports_primary_writable tests.test_cli_flow.CliFlowTests.test_build_primary_write_health_reports_fallback_only_when_all_probes_fail tests.test_cli_flow.CliFlowTests.test_cli_doctor_warns_when_primary_write_path_is_fallback_only tests.test_cli_flow.CliFlowTests.test_dashboard_html_shows_backend_runtime_panel_for_fallback_sqlite tests.test_cli_flow.CliFlowTests.test_cli_status_marks_fallback_sqlite_as_active_backend`
- Human confirmation: 真实运行 `python3 -m runtime.cli status/doctor/dashboard --workspace "$PWD"`，当前环境报告 `primary_write_health.status=primary_writable`。
- 结果汇总：完成。
- 核心目标是否已由证据证明完成：是。
- 若未完成，当前剩余差距：无。
- 剩余风险：automation 宿主本身若继续限制 `~/.expcap` 写权限，仍会退化到 fallback，但现在能被直接识别。

## Resume / Handoff
- 当前状态：实现与验证完成。
- 当前卡点：无代码阻塞，剩余是 automation 宿主权限口径是否需要外部调整。
- 下一步唯一动作：在下次 daily review 里确认受限环境是否如预期显示 `fallback_only`。
- 下一轮核心目标：继续清理 candidate queue / unproven backlog，或跟进 automation 宿主权限策略。
