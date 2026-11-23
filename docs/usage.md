# 脚本使用说明

第一次使用按[实板操作流程](riscv64-board.md)运行。本页解释每条命令的输入、输出和可调整的设置。`./kcfg` 在 x86_64 主机运行；包里的 `install` 和 `run` 在 riscv64 板上运行。

## 主机命令

| 命令 | 输入与结果 |
|---|---|
| `./kcfg fetch` | 获取缺少的 Linux 和 FTXUI 源码。需要自建 LLVM 时一并获取 LLVM 源码。 |
| `./kcfg tools` | 自动执行上述准备；构建或复用 LLVM，再编译 Clang 插件和配置处理器。两者都在主机上运行。 |
| `./kcfg config` | 在空目录运行上游 `allyesconfig`，保留原件 `config/native.config`，应用采集前提和可选板级片段后执行 `olddefconfig`，输出 `config/.config` 和逐项差异 `config/changes.tsv`。 |
| `./kcfg build baseline` | 用基线配置交叉编译未插桩的镜像、模块和设备树。 |
| `./kcfg build profile` | 用同一基线和板级策略构建采集内核、树外采集驱动及 `profile/campaign/`。该目录留在主机。 |
| `./kcfg plan` | 读取 `profile/campaign/` 和传回的 `trace.tsv`，在 `plan/` 生成配置请求、理由和延迟模块清单。 |
| `./kcfg build candidate` | 应用配置请求，核对 Kconfig 依赖，构建不带插桩的内核。成功后发布最终的 `candidate/.config`。 |
| `./kcfg pack baseline`、`profile` 或 `candidate` | 将对应的已构建镜像、运行模块、设备树和板端命令打成 `packages/<角色>.tar.gz`。 |
| `./kcfg compare` | 读取基线和候选的安装包及传回的 `baseline.tsv`、`candidate.tsv`，输出包大小和启动时间对比。 |

表中产物路径相对于 `WORK`。每轮选一个短且唯一的目录名（如 `board-01`），基线与候选用它区分内核发行版本；更换源码、基线、策略或重做采集构建时，换新 `WORK`。同一采集构建可在不同启动中观测多个负载。

## 准备工具链

从仓库根目录复制 `examples/board.env` 后按需设置，再执行 `source ./board.env`。源码、工具链和宿主构建目录可以位于仓库外。以下路径建议填绝对路径。

| 变量 | 默认值与用法 |
|---|---|
| `DEPS` | 下载和构建依赖的目录，默认 `.deps/`。 |
| `LINUX` | 默认 `DEPS/linux`。可指向板厂源码、发布归档的解包目录或本地检出。 |
| `LLVM_SOURCE`、`SDK` | 源码默认 `DEPS/llvm-project`，安装前缀默认 `DEPS/llvm`。已有带开发头文件、库及 RISC-V 后端的 SDK 时，直接设 `SDK`；要自建，选源码并把 `SDK` 指向新前缀。 |
| `FTXUI_SOURCE_DIR`、`FTXUI_DIR` | 前者指定源码，后者指定已安装的 FTXUI 包所在的 CMake 配置目录。优先用指定源码；两者均未设置时默认取 `DEPS/ftxui`。 |
| `LINUX_REF`、`LLVM_REF`、`FTXUI_REF` | 首次下载用的分支或标签，默认分别为 `v7.4-rc3`、`llvmorg-23.1.4`、`v6.1.9`。已有源码直接使用。 |
| `JOBS`、`LLVM_BUILD`、`HOST_BUILD` | 并行数默认 8，LLVM 编译目录默认 `DEPS/llvm-build`，插件和处理器默认 `build/host`。切换编译器时为宿主工具选新构建目录。 |
| `BOOTSTRAP_CC`、`BOOTSTRAP_CXX` | 从源码编译 LLVM 的宿主编译器，默认 `cc`、`c++`。 |
| `CC`、`CXX` | 编译插件和处理器，默认用 `SDK/bin` 里的 Clang、Clang++。插件与加载它的 Clang 需二进制兼容。 |
| `KERNEL_CC`、`HOSTCC`、`HOSTCXX`、`SYSROOT` | 可指定内核 Clang、Kbuild 宿主工具的编译器和自选 glibc 的完整宿主系统根。内核与模块本身不链接 glibc。 |
| `WORK`、`BOARD`、`BOARD_DIR` | 工作目录默认 `build/board`；示例选用 `build/board-01`。`BOARD` 是板上账户和地址，`BOARD_DIR` 是板上家目录下的本轮目录，两者用于 `scp`。 |

板上不需要 LLVM、Python、just、项目源码或配置处理器。x86_64 QEMU 的模拟环境另按 [QEMU 验证器](../qemu/README.md)准备。

## 基线和裁剪策略

`BOARD_CONFIG` 可指向 Kconfig 格式的小片段，例如包含板厂模块签名密钥路径的 `CONFIG_MODULE_SIG_KEY="/keys/board.pem"`。它在原生 `allyesconfig` **之后**应用。采集要求 `CONFIG_MODULES=y`、`CONFIG_TRACEPOINTS=y`、`CONFIG_TRIM_UNUSED_KSYMS=n`；脚本保留已满足的值。板型相关的改动以实际启动和构建需要为准。

### 登记组件

在变量文件中用空格分隔配置名。以下是写法示例，是否授权应按本板用途决定：

```bash
export DEFER='CONFIG_IKCONFIG'
export DROP='CONFIG_NLS_CODEPAGE_850'
```

`build profile` 从 Kbuild 直接规则查找这些配置的 C 文件，自动编号并生成清单。`DROP` 允许在覆盖充分、启动和负载都未命中时关闭；`DEFER` 允许在覆盖充分、启动未命中且模块名明确时延迟加载。启动必需的功能保持内建。两个变量都为空时，工具观察能直接识别的条目，全部保留。

复杂聚合对象可用 `SCOPE` 指定已审定的清单，代替上述自动映射。格式见[清单示例](../examples/qemu.scope.tsv)：编号（从 1 开始）、配置名、模块名或 `-`、策略、相对 C 源文件路径，以制表符分隔。相同配置的各成员使用同一策略。配置保持原值的具体原因见 `plan/report.tsv`。

## 启动文件

- `GRUB_KERNEL` 和 `GRUB_INITRD` 是板上现有默认启动项的固定文件路径，在主机打包时填写。若默认项引用的是版本化路径，三轮都写最初的那一对；不要每次重启后重新按 `uname -r` 取值。安装保留菜单、根盘参数和控制台设置。依赖同一路径的其他菜单项也会读到新文件。
- 默认构建 `Image modules dtbs`。包内 `boot/Image` 是所选镜像，`lib/modules/<release>/` 是匹配的运行模块，`dtbs/` 是本次配置选中的设备树。三版采用不同的内核发行后缀。采集版额外带 `kcfg_collect`，候选版带填写好的 `modules-load.late`。
- 安装时设备树放在 `/boot/dtbs/<release>/`。固件提供设备树时不必设置变量。默认项显式加载外置设备树时，`DTB` 填其在包内 `dtbs/` 下的相对路径，`GRUB_DTB` 填板上默认项引用的绝对路径。已有的其他覆盖文件仍按原菜单加载。
- 默认项需要 EFI 自解压内核时，设 `IMAGE_TARGET=vmlinuz.efi` 并在基线启用 `CONFIG_EFI_ZBOOT`；普通未压缩 EFI stub 使用默认 `Image`。板要求 EFI 签名时，安装主机工具 `sbsigntool`，同时设置 `EFI_KEY` 和板已信任的 `EFI_CERT`。模块签名使用本次内核配置和对应密钥。

## 采集、回放和比较

进入板上的本轮目录，执行 `./profile/run 负载 参数…`。它在系统就绪后打开整机采集窗口，以调用者身份运行负载，成功后冻结并写入 `profile/trace.tsv`。每次启动只有一个窗口。要合并场景，先将 `trace.tsv` 改名保存，再重启同一采集内核运行另一个负载。将两份快照传回主机后执行：

```bash
./kcfg plan first.tsv --also second.tsv --review
```

默认批处理；加 `--review` 可用方向键和空格调整建议，按 `s` 保存，按 `q` 取消。`plan/requested.config` 是请求，`candidate/.config` 是经 Kconfig 和实际构建确认的结果。

`./baseline/run` 和 `./candidate/run` 都接受同一条负载命令。候选版在就绪后用 `modprobe` 加载清单内的模块及依赖；已在就绪前加载的模块会被报告。成功回放后 `boot.tsv` 记录角色、内核发行版本、本次启动标识和就绪时间戳（微秒）。

`compare` 用实际传输的两个 `.tar.gz` 的字节数，以及各版从内核启动到 `multi-user.target` 激活的时间中位数（秒）。就绪时间不含固件、GRUB、用户登录和工作负载。缩减率为 `100 ×（基线 − 候选）／基线`。两版各一次成功启动即可比较；重复采样时固定业务输入、服务和外设，得到的样本数会随结果显示。
