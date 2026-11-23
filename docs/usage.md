# 使用指南：从板级基线到候选内核

## 准备工具链

宿主机需要 just、uv、Python 3.11 或更高版本、CMake 3.30 或更高版本、Ninja，以及目标内核的 Kbuild 依赖。常用依赖包括 GNU Make、flex、bison、bc、Perl、libelf、kmod 和编解码工具；下载源码时需要 Git，启用内核模块签名时还需相应的密码库开发文件。虚拟机依赖见 [QEMU 验证器](../qemu/README.md#准备环境)。

```sh
just sources
SDK="$PWD/.deps/llvm"
SDK="$SDK" just check
```

`just sources` 复用已有源码并准备 Ubuntu 镜像。仅在源码目录缺失时克隆：Linux、LLVM 源码和 FTXUI 默认分别取 `v7.4-rc3`、`llvmorg-23.1.4` 和 `v6.1.9`。首次下载可设置 `LINUX_REF`、`LLVM_REF`、`FTXUI_REF`，或传给 `tools/sources.py` 对应的 `--linux-ref`、`--llvm-ref`、`--ftxui-ref`，选择其他标签或分支。脚本不切换已有源码；要并存多个版本，用 `--cache` 指定新目录。

已有板厂源码可用 `LINUX` 指定，`just sources` 直接复用，无需 Git 标签或 `.git` 目录。已有 LLVM 开发套件用 `SDK` 指定，例如 `SDK="$(llvm-config --prefix)" just check`。内核工具也接受显式的 `--linux` 和 `--llvm`。用同一套 Clang 可执行文件、头文件和库编译并加载插件；切换工具链时使用新的 `HOST_BUILD` 目录。需要从源码构建时：

```sh
tools/sources.py --llvm-source
SDK="$PWD/.deps/llvm-local"
cmake -S .deps/llvm-project/llvm -B build/llvm-sdk -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$SDK" \
  -DLLVM_ENABLE_PROJECTS='clang;lld' -DLLVM_TARGETS_TO_BUILD='X86;AArch64' \
  -DLLVM_BUILD_LLVM_DYLIB=ON -DLLVM_LINK_LLVM_DYLIB=ON -DCLANG_LINK_CLANG_DYLIB=ON
cmake --build build/llvm-sdk --target install --parallel 8
SDK="$SDK" just check
```

| 构建部分 | 选择编译器和运行库 |
|---|---|
| C++26 插件与处理器 | `just host` 接受 `SDK`、`CC`、`CXX`、`SYSROOT`、`FTXUI_SOURCE_DIR`、`JOBS` 与 `HOST_BUILD`。自选 glibc 时使用包含头文件、加载器、C++ 标准库和依赖的完整系统根。 |
| 内核与树外驱动 | `tools/kernel.py` 接受 `--llvm` 和 `--cc`；`--hostcc`、`--hostcxx` 和 `--host-sysroot` 用于构建 Kbuild 宿主工具。内核与驱动由 GNU C 编译，不链接 glibc。 |
| 客体负载 | glibc 由 Ubuntu 根文件系统决定，用 `--rootfs` 指定可信来源的本地归档。 |

## 选择基线

在目标板上从已验证可启动的完整 `.config` 开始。保留启动存储、控制台、设备树、固件、签名、恢复和安全策略所需的功能。采集配置要求 `CONFIG_MODULES=y`、`CONFIG_TRACEPOINTS=y` 和 `CONFIG_TRIM_UNUSED_KSYMS=n`。强制签名时，用内核信任的密钥签署采集驱动。

参考流程先用 `allnoconfig` 和两个片段建立精简配置，再把该配置作为约束运行 `allyesconfig`，只解除内核配置读取和 CP850 代码页的约束：

```sh
tools/kernel.py configure --llvm "$SDK" --base allnoconfig \
  --fragment qemu/guest.config --fragment examples/profile.config --out build/board-pins
tools/kernel.py configure --llvm "$SDK" --base allyesconfig \
  --fragment build/board-pins/.config --unpin CONFIG_IKCONFIG \
  --unpin CONFIG_NLS_CODEPAGE_850 --out build/board-baseline
```

原生 `allyesconfig` 则直接调用上游目标，不传板级片段：

```sh
export LD_LIBRARY_PATH="$SDK/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
env -u KCONFIG_ALLCONFIG -u KCONFIG_CONFIG make -C .deps/linux \
  O="$PWD/build/native-allyes" ARCH=x86_64 LLVM="$SDK/bin/" allyesconfig
```

探索宽基线时，可从原生配置逐项记录为构建、启动或采集所需的调整；也可用 `qemu/guest.config` 与 `examples/broad.config` 起草宽约束。两个方案都应先通过目标启动回放，再作为体积和启动时间的比较基线。配置、构建和回放各使用新输出目录。

## 登记组件

```sh
tools/kernel.py inventory --under fs/nls --symbol CONFIG_NLS_CODEPAGE_850 \
  --out build/discovered.tsv
```

以 [`examples/qemu.scope.tsv`](../examples/qemu.scope.tsv) 为模板。五列依次为连续编号、配置名、模块名或 `-`、策略、相对源码路径，使用制表符分隔。同一选项的多个文件使用一致的模块名和策略。`inventory` 根据直接 Kbuild 规则起草清单，默认标记 `keep`。核对编译对象、注册副作用和回退路径后，再将可关闭的项标记 `drop`，将允许后加载的项标记 `defer`。

## 构建与采集

```sh
SDK="$SDK" just host
tools/kernel.py profile --llvm "$SDK" --baseline build/board-baseline/.config \
  --scope examples/qemu.scope.tsv --plugin build/host/plugin/kcfg_frontend.so \
  --out build/board-profile
qemu/guest.py run --mode profile \
  --kernel build/board-profile/kernel/arch/x86/boot/bzImage \
  --module build/board-profile/driver/kcfg_collect.ko \
  --workload examples/workload.sh --output build/board-capture
```

实板从独立试验槽启动，各构建步骤使用相同的 `--arch`；镜像步骤可指定 `--image-target`，例如 `--arch arm64 --image-target Image`。在 x86_64 上交叉构建、用 Ubuntu 与 GRUB 上板并比较运行包和启动时间，见 [RISC-V 64 实板验证](riscv64-board.md)。

在目标系统上以管理员身份加载与内核匹配的驱动。预热完成后启动采集，等待负载和要纳入观测的后台任务完成，再冻结快照：

```sh
set -eu
insmod /path/to/kcfg_collect.ko
printf 'start\n' > /dev/kconfig-trace
/path/to/workload-and-wait
printf 'stop\n' > /dev/kconfig-trace
cat /dev/kconfig-trace > trace.tsv
```

只导出成功负载的快照，并保留该构建的完整 `campaign` 目录。每次启动提供一个整机观测窗口；覆盖业务、恢复、热插拔等场景时，分别启动同一采集镜像，再把成功快照合并。

## 审阅、回放与部署

```sh
build/host/processor/kcfg-plan build/board-profile/campaign \
  build/board-capture/trace.tsv build/board-plan --review
tools/kernel.py candidate --llvm "$SDK" --campaign build/board-profile/campaign \
  --plan build/board-plan --out build/board-candidate
```

方向键选择建议，空格撤回或恢复，`s` 保存，`q` 取消。省略 `--review` 可批处理，`--also` 可合并同一构建的快照。通过 `report.tsv` 核对决定；若 Kconfig 改写请求或牵动其他选项，复核依赖和策略后重新生成方案。

用候选内核运行原负载。QEMU 的 `--late-module` 参数按 `deferred-paths.txt` 的顺序传入，路径相对于候选目录。参考方案只延迟一个模块：

```sh
qemu/guest.py run --mode candidate \
  --kernel build/board-candidate/kernel/arch/x86/boot/bzImage \
  --late-module build/board-candidate/kernel/kernel/configs.ko \
  --workload examples/workload.sh --output build/board-replay
```

部署时，将候选内核、匹配的模块和固件装入试验槽，运行 `depmod`。在明确的业务启动事件加载 `modules-load.late`，例如交给服务的 `ExecStartPre` 脚本：

```sh
set -eu
while IFS= read -r module; do
  modprobe -- "$module"
done < /path/to/reviewed/modules-load.late
```

在就绪点和负载前后检查 `/proc/modules`，核对启动镜像、`udev` 别名、其他服务和模块依赖是否提前触发加载。无法保证计划加载时刻时，撤回该项建议。冷启动、业务、异常恢复和回滚通过后，再切换默认启动槽。

## 排障

| 现象 | 检查 |
|---|---|
| 插件加载失败或版本不符 | 确认同一 LLVM 前缀的 `bin` 和 `lib` 同时生效。 |
| 选项保持原值 | 看 `report.tsv` 的理由，以 `coverage.tsv` 和 `objects.tsv` 核对覆盖和归属。 |
| 模块加载失败 | 核对内核发行版本、硬依赖和签名。 |
| 采集命令失败 | 核对目标权限、采集状态和初始化钩子；新开一轮时重新启动。 |
| 客体未完成 | 用 `guest.status` 定位失败阶段，再检查 `console.log`。 |
