# 构建脚本参考

主机入口是 `./kcfg`。第一次运行 `cp .env.example .env`，然后编辑 `.env`。入口每次自动读取它，无需 `source` 或 `export`。板端只用包内独立的 `deploy.sh`。完整操作顺序见[实板教程](board.md)。

## 命令和结果

| 主机命令 | 输入和结果 |
|---|---|
| `./kcfg --init-deps` | 按配置下载缺少的 Linux、FTXUI 和需自建的 LLVM 源码。已有源码直接复用。 |
| `./kcfg --build-tools` | 按 `LLVM_MODE` 复用或构建 LLVM、Clang 和 LLD，再构建主机插件与配置处理器。 |
| `./kcfg --build-kernel` | 默认构建 `profile`：生成配置、插桩、编译镜像和采集驱动，并写出一个部署包。 |
| `./kcfg --build-kernel baseline` | 使用相同基线设置构建和打包无插桩的对照内核。 |
| `./kcfg --plan` | 打开原配置与当前配置的对照编辑器。按 `a` 分析，按 `s` 存草稿，按 `A` 应用到 `$WORK/plan/`。 |
| `./kcfg --plan --batch` | 无终端时从基线、强制意见和快照直接分析并应用；忽略交互草稿。 |
| `./kcfg --build-kernel candidate` | 对处理器提出的配置运行 `olddefconfig`，构建无插桩内核，输出部署包和 `$WORK/candidate/.config`。 |

`./kcfg --help` 可直接查看命令。多块板可以各存一份完整配置，例如 `./kcfg --env /data/visionfive.env --build-kernel`。`--env` 放在动作前；配置内的相对路径以仓库根目录为准。发行包和目标设备都不需要这份文件。

## 源码、工具链和目录

[配置模板](../.env.example)中的每一项都是可用默认值，每个赋值上方都有用途说明。它使用 Bash 语法：路径可写成 `"$ROOT/..."`，多参数使用数组，例如 `('KCFLAGS=-O2 -g0' V=1)`，带空格的元素仍作为一个参数传递。

| 设置 | 如何选择 |
|---|---|
| `DEPS` | 默认 `$ROOT/.deps`。改这个父目录，下方按它定义的路径一起变化。 |
| `LINUX_SRC`、`LLVM_SRC`、`FTXUI_SRC` | 指向本地源码，或填写希望首次克隆到的位置。可以使用发布归档的解包目录和有本地修改的源码树。 |
| `LINUX_URL`、`LLVM_URL`、`FTXUI_URL` | 首次克隆的仓库，可换成本地镜像或板厂仓库。 |
| `LINUX_REF`、`LLVM_REF`、`FTXUI_REF` | 只选择首次克隆的分支或标签，留空跟随远端默认分支。模板分别选用 `v7.4-rc3`、`llvmorg-23.1.4`、`v6.1.9`；已有树原样使用。 |
| `LLVM_PREFIX`、`LLVM_MODE` | 前缀既是 LLVM 安装目的地，也可指向已有的安装。`auto` 在该目录有 `llvm-config`、`clang`、`ld.lld` 时复用；`source` 从源码编译；`installed` 只使用现有安装。 |
| `FTXUI_DIR` | 指向包含 FTXUI 软件包配置的 CMake 目录时，优先使用安装包并跳过 FTXUI 源码；留空使用 `FTXUI_SRC`。 |
| `LLVM_BUILD`、`HOST_BUILD` | 分别保存 LLVM 和宿主工程构建文件。插件在 `$HOST_BUILD/plugin/`，处理器在 `$HOST_BUILD/processor/`。 |
| `WORK`、`PACKAGE_DIR` | 实验目录默认 `build/board`，部署包默认在其 `packages/` 下。角色的内核对象与元数据留在实验目录。 |

例如，把 `.env` 中对应的赋值**就地替换**为下列值即可复用开发环境。模板从上到下求值，派生路径会引用前面已设定的值。

```bash
LINUX_SRC=/src/vendor-linux
LLVM_PREFIX=/opt/llvm
LLVM_MODE=installed
FTXUI_DIR=/opt/ftxui/lib/cmake/ftxui
```

已安装的 LLVM 需要带 Clang 开发头文件、CMake 配置、`clang-cpp` 共享库、LLD、LLVM 二进制工具及目标后端。默认自建版本启用 X86、RISCV 和 BPF。宿主插件与实际加载它的 Clang 应使用兼容的二进制接口。

## 编译参数

| 设置 | 作用 |
|---|---|
| `JOBS`、`CMAKE_GENERATOR` | 默认并行数为 8；LLVM 和宿主工程默认使用 Ninja。 |
| `BOOTSTRAP_CC`、`BOOTSTRAP_CXX` | 编译 LLVM 本身的宿主编译器。 |
| `TOOLS_CC`、`TOOLS_CXX`、`TOOLS_CMAKE_ARGS` | 编译宿主插件和处理器的编译器及 CMake 参数。默认用选定 LLVM 的 Clang、Clang++ 和 Release 构建。 |
| `LLVM_CMAKE_ARGS` | 完整的 LLVM CMake 参数数组，可调整后端、共享库、优化及链接并行度。 |
| `SYSROOT` | 自选 glibc 时填写完整宿主系统根，应用到 CMake 和 Kbuild 的宿主工具。内核和模块本身不链接 glibc。 |
| `ARCH`、`KERNEL_CC`、`HOSTCC`、`HOSTCXX` | Kbuild 架构、目标内核 Clang、宿主辅助程序的 C 与 C++ 编译器。默认 `ARCH=x86_64`；切换 riscv64 时设 `ARCH=riscv`。 |
| `KBUILD_ARGS` | 逐项传给配置、编译及安装目标，例如 `KBUILD_ARGS=(CROSS_COMPILE=riscv64-linux-gnu- 'KCFLAGS=-O2 -g0')`。 |
| `KERNEL_TARGETS`、`KERNEL_IMAGE` | 两者留空时按架构选择。上游 x86_64 使用 `(bzImage modules)` 和 `arch/x86/boot/bzImage`；RISC-V 使用 `(Image modules dtbs)` 和 `arch/riscv/boot/Image`。源码树提供该架构的 `boot/dts/` 时，默认加入 `dtbs`。手工填写数组时把镜像目标放在首项；镜像不在标准位置时单独填写相对路径。 |
| `RELEASE_SUFFIX` | 附加在内核发行版本上的后缀；入口再追加角色名。采集版还追加本次采集标识的前缀。不同实验要在同一板上并存时，给它们不同的后缀。 |
| `PYTHON` | 主机用于准备和处理插桩元数据的 Python 解释器。 |

以 RISC-V 为例：只用固件提供的设备树时，设 `KERNEL_TARGETS=(Image modules)`，部署包便不含设备树。若板的 GRUB 使用 EFI 压缩镜像，在支持该目标的配置下设 `KERNEL_TARGETS=(vmlinuz.efi modules dtbs)` 和 `KERNEL_IMAGE=arch/riscv/boot/vmlinuz.efi`。镜像格式和板上已有的启动签名策略由板级配置决定。

## 选择内核配置

- **原生全选**：`KERNEL_CONFIG=''`。入口在新对象目录调用上游 `allyesconfig`，保留 `$WORK/<角色>/allyes.config` 原件，再设置 `CONFIG_MODULES=y`、`CONFIG_TRACEPOINTS=y`、`CONFIG_TRIM_UNUSED_KSYMS=n`。这是采集的三个前提；内核仍需适合本板。
- **指定配置**：`KERNEL_CONFIG=/src/board.config`。入口复制完整配置后执行 `olddefconfig`，由目标源码补齐新选项，输入文件不变。采集版需要上述三个开关；需要调整板厂配置时，可在 `CONFIG_OPTIONS` 明确指定。
- **额外开关**：`CONFIG_OPTIONS` 使用内核 `scripts/config` 的参数，例如 `CONFIG_OPTIONS=(--disable WERROR)`。这些调整在基线和采集版运行 `olddefconfig` 之前应用。在参考内核中，`TRACEPOINTS` 由跟踪功能选中；自定义配置缺少它时，应启用能选中它的跟踪功能，例如 `FTRACE` 下的 `ENABLE_DEFAULT_TRACERS`。候选版直接使用处理器的配置请求。

每个角色使用新的 `$WORK/<角色>/kernel/`。同一实验的基线、采集和候选共用 `WORK`；重做采集或更换源码时选择新的 `WORK`，以便原快照仍有对应的构建。只重试某个失败的角色时，先把它的角色目录移开。

## 自动发现组件与处理快照

采集构建从 Kbuild 自动发现能直接归属到开关的 C 文件，将编号与归属写入 `$WORK/profile/campaign/scope.tsv`。把它与同一次构建的快照一起保留。归属不明或覆盖不完整的选项保持原值。

`INVENTORY_POLICY=auto` 是新采集的默认值。启动和负载都未命中时，建议关闭；仅在负载窗口命中且能确定对应的可加载模块时，建议在就绪后加载。`drop` 只建议关闭，`keep` 只观测。三种模式的观测范围相同。

升级后，旧 `.env` 中的手工名单不再限制新采集；原有快照仍按各自清单中记录的策略处理。要使用自动发现的范围和新策略，请在新的 `WORK` 中重新采集。

`TRACE` 是第一份快照，默认 `$WORK/trace.tsv`。其他启动取得的同构建快照可写成 `EXTRA_TRACES=(/data/second.tsv /data/third.tsv)`。`FORCE_CONFIG` 可指向每行一个 `CONFIG_名称=y/m/n` 赋值的文件。`DEPENDENCY_GRAPH` 可指定同一源码、架构和基线的解析器导出文件；留空时，主机通过 `uv` 运行外部解析器生成依赖图。

`--plan` 中按 `/` 搜索，按 `i` 修改选项，按 `?` 查看[完整操作](../processor/README.md)。草稿保存在 `$WORK/plan.draft`；应用写入新的 `plan/`。`report.tsv` 解释决定，`requested.config` 是请求，`modules-load.late` 列出计划延迟加载的模块。候选构建核对实际取值，成功后的 `$WORK/candidate/.config` 是最终配置。

## 部署包和板端脚本

`PACKAGE_NAME` 是包名前缀，`GZIP_LEVEL` 控制压缩级别，`MODULE_STRIP` 对应 Kbuild 的 `INSTALL_MOD_STRIP`。`DEPLOY_SCRIPT` 指向要原样放进包的脚本，默认是仓库根目录的 [deploy.sh](../deploy.sh)。可在打包前改源码，也可解压后直接修改脚本。

以默认采集包 `kernel-profile.tar.gz` 为例：

```text
kernel-profile/
  deploy.sh                   板上执行的独立安装脚本
  release                     该内核的发行版本
  boot/vmlinuz-<release>      选定的启动镜像
  boot/config-<release>       实际编译配置
  boot/dtbs/<release>/        所选设备树（构建设备树时）
  lib/modules/<release>/      安装后的模块（构建 modules 时）
```

采集包的模块目录还包含 `kcfg_collect`；候选包在顶层另带 `modules-load.late`。模块沿用该内核配置的压缩和签名。initramfs 在目标板上生成。

在已有 GRUB 的 Ubuntu 板上以 `sudo ./deploy.sh` 安装：脚本把镜像和配置放入 `/boot/`，复制包中的设备树和模块，对安装的模块运行 `depmod`，再调用 `update-initramfs` 和 `update-grub`。新版本增加到 GRUB 菜单，重启和选项选择由用户操作。若板的启动链要求特定设备树位置，在这份板端脚本中调整安装路径。
