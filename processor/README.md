# 配置处理器

`kcfg-plan` 将同一采集构建的快照按配置项合并，输出完整请求配置、变更理由和延迟模块列表。输入来自采集构建的 `campaign` 目录和成功负载的冻结快照。

## 使用与审阅

```sh
RUN=build/my-first-run
build/host/processor/kcfg-plan "$RUN/profile/campaign" \
  "$RUN/capture/trace.tsv" build/my-reviewed-plan --review
```

使用尚不存在的输出目录，父目录须已存在。`--review` 需要终端输入和输出：方向键选择建议，空格撤回或恢复，`s` 保存；`q`、Esc 或 Ctrl-C 取消，退出码为 2，无输出。省略 `--review` 可批处理；重复传入 `--also 路径` 可合并快照，启动、负载和初始化位分别取并集。

## 输入协议

| 文件 | 格式与用途 |
|---|---|
| `baseline.config` | 完整基线，输出保留未变更条目的内容和顺序。 |
| `scope.tsv` | 五列制表符分隔：连续编号、配置名、模块名、策略和相对源码路径。每行登记一个翻译单元。 |
| `campaign.id` | 采集构建时随机生成的标识，含 64 个小写十六进制字符和一个换行。 |
| `coverage.tsv` | 每行记录编号和前端插入点数；对象含探针引用才记为正数。缺少该编号或数量为零时视为覆盖不足。 |
| `trace.tsv` | 首行 `KCFG-TRACE`、版本 `1`、构建身份；每个编号一行，依次为编号、启动位、负载位、初始化位；末行为 `END` 和行数。各列用制表符分隔。 |

处理器用 `campaign.id` 将快照归到同一次采集构建，旧版目录和快照沿用相同格式。输入含重复或未知编号、截断快照或不一致的模块归属时，处理器报错。模块名的连字符与下划线等价。

## 决策与交付

保留范围外选项、`keep` 项、模块支持选项，以及启动命中、覆盖不足或基线未启用的选项。已启用且覆盖完整的选项按下列规则处理：

| 策略 | 条件 | 请求值 |
|---|---|---|
| `drop` | 启动与负载均未命中。 | `n` |
| `defer` | 启动未命中，模块支持已启用，且模块名明确。允许负载命中。 | `m`，并加入延迟清单。 |

初始化命中单独记录。按[板级清单规则](../docs/usage.md#登记组件)决定哪些功能可关闭或延迟。

| 产物 | 内容 |
|---|---|
| `requested.config` | 请求应用的完整配置，供候选构建运行 Kconfig。 |
| `changes.tsv` | 配置项、原值和请求值。 |
| `modules-load.late` | 延迟模块名，每行一项。 |
| `report.tsv` | 聚合命中、覆盖数量和决定理由。 |
| `campaign.id` | 供候选构建核对采集身份。 |

处理器在同一文件系统的暂存目录写完所有产物后，一次发布到目标目录。

## 独立构建

在 Linux 上使用支持 C++26 的 LLVM／Clang 和兼容的 FTXUI 构建。可用已安装的 FTXUI 软件包，也可通过 `FTXUI_SOURCE_DIR` 指定本地源码。项目默认获取 FTXUI 6.1.9，已安装的软件包不限定版本。自选 glibc 用 `CMAKE_SYSROOT` 指定完整系统根；交叉编译可用 `CMAKE_TOOLCHAIN_FILE`。

```sh
SDK="${SDK:-$PWD/.deps/llvm}"
export PATH="$SDK/bin:$PATH" LD_LIBRARY_PATH="$SDK/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cmake -S processor -B build/planner-only -G Ninja \
  -DCMAKE_CXX_COMPILER="$SDK/bin/clang++" -DFTXUI_SOURCE_DIR="$PWD/.deps/ftxui"
cmake --build build/planner-only
ctest --test-dir build/planner-only --output-on-failure
uv run --no-project python -I processor/tests/contract.py build/planner-only/kcfg-plan --tmux
```

最后一条命令使用 tmux 验证交互审阅的保存和取消。
