# F 角色分池检测原型实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 F 离群统计从全程序统一档位改为按 `move`、`plunge`、`cut` 角色独立统计，并验证合法档位错用能被检出。

**Architecture:** 复用当前动作分类和模态状态机；每个角色分别建立模态 F 频率与显式 F 频率，显式行按当前角色使用角色内常用档位、包络和相对距离判定。保留现有硬边界、APT 参考、上下文复核和报告字段，新增角色统计数据但不引入新的坐标/固定循环语义。

**Tech Stack:** Python 3.8、标准库、`unittest`。

---

### Task 1: 添加角色分池回归测试

**Files:**
- Modify: `NCodeProcess/tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

  添加两个行为测试：切削常用 F1800 后显式切换到 F100/F6000 时产生 `feed-outlier`；同一程序中 F300 仅在 `plunge`、F1800 仅在 `cut`、F6000 仅在 `move` 时不因跨角色污染产生离群。

- [ ] **Step 2: Run focused tests to verify RED**

  Run: `conda run -n python38 python -m unittest tests.test_core.CoreTests.test_feed_role_pool_flags_legal_gear_misuse tests.test_core.CoreTests.test_feed_role_pool_keeps_roles_separate -v`

  Expected: 新测试因当前固定合法档位放行或全局统计而失败。

### Task 2: 实现角色内统计与检测

**Files:**
- Modify: `NCodeProcess/ncodeprocess/core.py:240-257`
- Modify: `NCodeProcess/ncodeprocess/core.py:1982-2110`

- [ ] **Step 1: Extend report data**

  在 `FeedOutlierData` 增加 `stage_common_feeds: Dict[str, List[float]]`，旧字段继续保留。

- [ ] **Step 2: Build role-specific counters**

  根据 `row_feeds` 建立每个动作角色的模态频率，根据 `explicit_feeds` 建立每个动作角色的显式频率；每个角色独立计算 `min_count`、常用档位和包络。

- [ ] **Step 3: Replace global lookup in the explicit-feed loop**

  在处理每个显式 F 时按 `feature` 读取对应角色的统计；没有角色常用档位时跳过第二层统计。保留硬边界和 APT 匹配逻辑。

- [ ] **Step 4: Remove unconditional legal-gear bypass**

  删除 `LEGAL_FEED_GEARS` 对少见 F 的 `pass` 分支；角色内距离、包络和近似档位规则继续决定是否报警。暂不修改上下文高/低档规则。

- [ ] **Step 5: Preserve compatibility aggregate fields**

  `common_feeds` 记录所有角色常用档位的去重并集；`envelope` 使用该并集的最小/最大值，保证已有报告和测试接口可读。

### Task 3: 验证并补充文档

**Files:**
- Modify: `NCodeProcess/docs/NC进给率异常检测算法指导文档.md`
- Modify: `NCodeProcess/docs/NCodeProcess-需求文档.md`
- Modify: `NCodeProcess/docs/NCodeProcess-审查与待办.md`

- [ ] **Step 1: Run focused core regression**

  Run: `conda run -n python38 python -m unittest tests.test_core -v`

- [ ] **Step 2: Run non-GUI regression where environment permits**

  Run: `conda run -n python38 python -m unittest tests.test_cli tests.test_preferences -v`

  Record any pre-existing Windows registry/AppData permission failures separately from algorithm failures.

- [ ] **Step 3: Update documents**

  Document that the prototype uses existing three action labels as role pools, explicit F lines as anomaly evidence, and does not yet claim G90/G91/fixed-cycle support.

- [ ] **Step 4: Run syntax and targeted scenario verification**

  Run: `conda run -n python38 python -m py_compile ncodeprocess/core.py` and rerun the two new tests plus existing APT F6000 suppression test.

## Verification checklist

- [ ] New tests were observed failing before implementation.
- [ ] New tests pass after implementation.
- [ ] Existing `tests.test_core` has no new failures.
- [ ] Hard-boundary, APT, modal-inheritance, and report-compatibility tests remain green.
- [ ] GUI/environment permission failures are not attributed to the F algorithm.
