# 从负载证据生成候选配置

处理程序只修改平台清单明确授权的选项。采集不到使用证据，不等于功能没有用；能否移除或推迟某项功能，先由平台维护者审定，再由采集结果限制。程序不自行安装模块，不覆盖原配置，也不以模拟器测试替代实板验收。

## 运行与审阅

在仓库根目录运行。第一个参数是构建插桩内核时生成的采集目录，第二个是成功运行负载后冻结的快照，第三个必须是尚不存在的输出目录。父目录需要存在，无需管理员权限。

```sh
build/processor/kcfg-plan build/profile/campaign build/guest/profile-01/trace.tsv build/proposal --review
```

上下键切换建议，空格接受或拒绝，`s` 保存，`q` 或退出键取消。审阅只允许撤回建议，不能强行关闭受保护项。取消时不创建输出目录，退出状态为 2。去掉 `--review` 即可批处理；使用 `--also` 可以合并同一插桩构建在多次独立启动中得到的快照：

```sh
build/processor/kcfg-plan build/profile/campaign build/guest/read/trace.tsv build/proposal-union \
  --also build/guest/write/trace.tsv --also build/guest/recovery/trace.tsv
```

每个编号的启动、负载和初始化命中分别取并集，不做计数相减。任何一次启动出现的早期使用都会保护对应选项，任何一个快照不完整都会阻止发布。

## 输入与信任边界

| 文件 | 作用 |
|---|---|
| `baseline.config` | 对应目标架构已经过 Kconfig 解析的完整基线，保留未选中条目的值、注释和顺序。 |
| `scope.tsv` | 以制表符分隔连续编号、配置项、模块名、策略、源码相对路径五列，一个翻译单元一行。多个文件映射到同一配置项时，模块和策略必须一致。 |
| `campaign.seed`、`campaign.id` | 绑定本轮构建，均为 64 个小写十六进制字符加换行；程序重算并核对身份后才解析配置。 |
| `coverage.tsv` | 编号和编译阶段确认的入口数量，以制表符分隔。缺行或数量为零意味着覆盖不足，不等于没有运行。 |
| `trace.tsv` | 首行为协议标记、版本和采集身份，之后每个编号恰好出现一次，携带启动、负载和初始化三个命中位，最后一行为结束标记和行数。 |

身份摘要为 SHA-256，依次输入以下精确字节：前缀 `kcfg-scope-v1`、一个零字节、种子文件（含换行）、清单文件、一个零字节、完整基线文件。摘要用于防止串用，不是签名；构建主机、采集客体和传输链路均须可信。覆盖收据由候选内核构建步骤与原构建记录再次核对。

非连续编号、重复来源、共享同一模块名的不同选项、过期身份、未知编号和截断快照均会被拒绝。模块名按内核惯例将连字符与下划线视为等价。输入接受普通文件；不要直接把仍在采集的设备或管道当作快照传入。

## 决策与输出

按配置项合并所有清单行。范围外的配置保持原样。启动命中、覆盖不完整、基线未启用、非布尔或非三态值，以及 `keep` 策略均保持原值。模块支持选项本身始终保留。

| 策略 | 提议条件 | 输出 |
|---|---|---|
| `drop` | 所有登记文件均有插桩收据，启动和负载都没有命中。仅有初始化命中不算负载使用。 | 由 `y` 或 `m` 改为 `n`。 |
| `defer` | 没有启动命中，所有登记文件均有覆盖，模块支持已内建，且清单给出明确模块名。允许负载阶段命中。 | 由 `y` 改为 `m`，或保持原有 `m`；加入延迟加载清单。 |

`coverage.tsv` 证明登记的文件含有探针，并不证明清单涵盖该选项的全部调用路径。只要仍有未评估的硬件、回退路径或初始化副作用，就应选用 `keep`。

输出在同一文件系统内先写入私有暂存目录，再一次发布，目标已存在即失败：

- `requested.config` 是完整候选配置，不是已通过 Kconfig 和实板验收的最终配置。
- `changes.tsv` 按配置项给出原值与请求值；`modules-load.late` 每行一个模块名，需要启动策略在预定阶段显式加载，单凭 `m` 不会控制加载时刻。
- `report.tsv` 说明范围内每项的合并证据、已覆盖文件数、最终请求和保护或变更原因。
- `campaign.id` 供后续构建检查候选与原采集构建的对应关系。

## 独立构建与检查

处理程序使用 C++26、FTXUI 6.1.9 和 OpenSSL 的摘要库。可使用已有的 FTXUI 软件包，也可显式指定已检出的源码目录；源码依赖不会在配置过程中隐式下载。以下示例单独构建处理程序，与仓库整体构建的可执行文件等价。

```sh
cmake -S processor -B build/planner-only -G Ninja \
  -DCMAKE_CXX_COMPILER="$SDK/bin/clang++" -DFTXUI_SOURCE_DIR="$PWD/.deps/ftxui"
cmake --build build/planner-only
ctest --test-dir build/planner-only --output-on-failure
uv run python -I processor/tests/contract.py build/planner-only/kcfg-plan --tmux
```

自选 glibc 时传入 `-DCMAKE_SYSROOT=/path/to/sysroot`，并使用与该系统根匹配的 C++ 标准库、OpenSSL 和 FTXUI；单独替换运行时的 glibc 文件不是可移植的部署方式。交叉编译可通过 `CMAKE_TOOLCHAIN_FILE` 指定完整工具链。最后一条命令在独立终端中实际进行一次取消和一次拒绝后保存，要求宿主机具有 tmux。
