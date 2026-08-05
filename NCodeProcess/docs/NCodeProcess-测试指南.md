# NCodeProcess 测试指南

> 用途：说明当前测试情况（架构、基线、覆盖、已知噪音）与新增/维护测试的做法，供后续开发按此执行。
> 适用范围：本机 Windows + conda `Python 3.8 环境` 环境；目标平台 Windows 7，**必须使用 Python 3.8 跑测试**（系统 Python 3.11 的验证不能代表 3.8 兼容性）。

> **维护说明**：本文件纳入版本管理，随测试变化持续维护。测试数量、模块结构、运行命令或约定变化后，应同步更新本文件，并同步 `NCodeProcess-程序理解与操作记录.md`、`NCodeProcess-发布说明.md`、`NCodeProcess-需求文档.md`（第 15 节）及本地流程文档 `NCodeProcess-更改测试打包提交流程.md` 中的测试基线。

---

## 一、测试架构总览

- **框架**：Python 标准库 `unittest`（无第三方依赖），Python 3.8（conda `Python 3.8 环境`，3.8.19）。
- **目录**：`tests/`（与 `ncodeprocess/` 同级），模块划分与源码分层一一对应。
- **当前基线**：**211 项全部通过**（2026-08-05 合并精简后 159 项；WP-01~WP-09 与用户测试修正各新增项累计后为 211 项），全量运行约 35 秒。
- **运行命令**（cwd 为 `NCodeProcess` 项目目录）：

| 场景 | 命令 |
|---|---|
| 全量测试 | `conda run -n <Python 3.8 环境名> python -m unittest discover -s tests -v` |
| 单模块 | `conda run -n <Python 3.8 环境名> python -m unittest tests.test_core -v` |
| 单用例 | `conda run -n <Python 3.8 环境名> python -m unittest tests.test_gui.LayoutWidgetTests.test_keep_table_uses_compact_profile_without_default_horizontal_overflow -v` |

## 二、测试模块与覆盖范围

| 模块 | 文件 | 对应源码 | 覆盖内容 |
|---|---|---|---|
| `CoreTests` | `tests/test_core.py` | `ncodeprocess/core.py` | 纯逻辑：目录扫描、计划构建、头部补全（HASS/V5-2500B）、刀具解析（APT/特殊刀具/优先级）、M03 补写（after-s/standalone）、校验（必填/G00/S·F 上下限/辅助顺序/F0/重复字段/结束标记/M06/S）、换行策略、统计、报告保留三份、图号候选、扩展名配置 |
| `test_gui.py` | `tests/test_gui.py` | `ncodeprocess/gui.py` | 纯函数（差异对比/图号合并/确认阈值/列宽分配/字体选择/布局档案）+ Tk 交互（表格布局与列宽、设置对话框两页、必填字段与 F/S 间距、程序编辑/对比、悬停提示、导出报告、启动回调） |
| `PreferencesTests` / `FileBackendPreferencesTests` | `tests/test_preferences.py` | `ncodeprocess/preferences.py` | 注册表读写/清除/遗留键；注册表不可写时回退 `%APPDATA%` 设置文件，再回退用户主目录 |
| `ReleaseAssetTests` | `tests/test_release_assets.py` | `version_info.txt` / `build_portable.ps1` / `VERSION.txt` / `NCodeProcess.spec` | 发布资产一致性：版本资源文件、打包脚本引用、版本号同步 |

## 三、测试隔离约定（重要）

1. **注册表隔离**：真实 `HKCU\Software\NCodeProcess` 可能残留 EXE 运行保存的设置（如 `require_m06=1`），会污染默认值断言。注册表相关测试一律使用独立测试键：
   - `tests/test_preferences.py`：`Software\NCodeProcess_UnitTests` / `..._UnitTests_File`，teardown 中 `clear_all`。
   - `tests/test_gui.py`：`TEST_SETTINGS_KEY`；`LayoutWidgetTests._build_app` 创建 `App` 时固定传入该键。
2. **GUI 测试隔离**：`_build_app` 用 `patch.object(App, "scan", ...)` 屏蔽真实扫描，并 `withdraw`/`deiconify` 控制窗口显示；每个用例 `finally: root.destroy()`。
3. **文件后备存储隔离**：`FileBackendPreferencesTests` 将 `%APPDATA%` 与 `Path.home` patch 到临时目录，不读写真实用户目录。
4. **临时目录**：core 测试用 `make_dir()`（`tempfile.mkdtemp`）创建独立目录，测试结束后由系统清理；不污染真实样例。
5. **已清理的导入/函数**：2026-08-05 精简后静态检查确认无未使用导入、无废弃函数。

## 四、已知无害噪音

- **Tk teardown 提示**：全量测试输出中偶发 `can't invoke "event" command: application has been destroyed`，是 Tk 主题在窗口销毁瞬间的提示，对应用例仍判 `ok`，非失败。
- **悬停提示偶发失败**：`test_cell_tooltip_hides_on_leave` 在全量负载下偶发失败 1 次（`when="tail"` 事件时序），单独重跑即通过，与代码改动无关；全量跑出现偶发单败时先单独重跑确认。

## 五、添加测试的做法（TDD 约定）

1. **分层对应**：改动纯逻辑 → 补 `tests/test_core.py`；改动界面 → 补 `tests/test_gui.py`；改动设置存储 → 补 `tests/test_preferences.py`；改动发布资产 → 补 `tests/test_release_assets.py`。
2. **RED → GREEN 流程**：每个功能改动按「先写失败测试 → 确认 RED（功能缺失报错）→ 最小实现 → 目标测试 GREEN → 全量回归」执行。
3. **布局/几何断言**：布局类改动优先以 `winfo_ismapped`/`winfo_x`/`winfo_reqwidth` 等几何断言锁定；设置对话框布局锁定 `test_settings_dialog_fits_1286_and_controls_visible`（宽 ≤640、高 ≤500），新增控件行时先跑该测试确认新高度。
4. **复用公共 helper（精简后的约定）**：
   - `CoreTests`：`DEFAULT_INFO`（默认编制/审核/图号/版次/机床/控制系统/日期）、`_cfg(**overrides)`（默认 `g00_level="allow"`）、`_mpf(plan)`（取首个 MPF FilePlan）、`make_dir()`。
   - `LayoutWidgetTests`（`SettingsDialogTests` 继承）：`_build_app(w, h)`、`_descendants(widget)`、`_collect_buttons(widget)`、`_relative_x_to_root(widget, root)`、`_column_total`。
   - 成对/同模式场景用 `subTest` 表驱动合并（如 `test_fit_column_widths_cases`、`test_feed_limits_check_both_ends`、`test_drill_types_detected_independent_of_diameter`），减少重复同时保留全部断言。
5. **Python 3.8 兼容**：测试代码同样避免 3.9+ 语法（如 `str.removeprefix`、`dict |` 合并）。
6. **运行与验证**：改动后先跑目标模块，再跑全量回归；保持基线 **211 项全绿**。

## 六、与其他文档的关系

- **操作记录**：测试基线、模块结构、精简记录见 `NCodeProcess-程序理解与操作记录.md`（2.5 配套工作等）。
- **发布说明**：各版本测试基线见 `NCodeProcess-发布说明.md`（第五节）。
- **需求文档**：验收标准覆盖情况见 `NCodeProcess-需求文档.md` 第 15 节。
- **流程文档**：运行命令、TDD 约定与隔离注意事项见本地 `NCodeProcess-更改测试打包提交流程.md`（步骤 2、注意事项 6/10/11）。
