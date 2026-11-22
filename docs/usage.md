# 构建、采集与移植

## 准备依赖

需要 Git、just、uv、Python 3.11 或更高版本、CMake 3.30 或更高版本、Ninja、OpenSSL 开发文件，以及能构建目标 Linux 的主机工具。Kbuild 使用 GNU Make；常见依赖有 flex、bison、bc、Perl、libelf、kmod 和编解码工具，宽配置还可能需要 pahole 等。Ubuntu 验证器另需 QEMU、bsdtar、readelf 和静态链接的 x86_64 BusyBox。不要为了测试而安装或启动宿主机内核模块。

`just sources` 克隆或复用 `.deps/linux` 和 `.deps/ftxui`，核验固定提交并下载带固定摘要的 Ubuntu 用户空间。已存在的源码树不符时会报错，不会重置。当前工作区 `.deps/llvm` 已有匹配的私有 SDK。其他机器可以用 `SDK=/path/to/prefix just host` 指定已有的 23.1.4 SDK；前缀中必须同时有 Clang 可执行程序、开发头文件、LLVM／Clang 共享库、LLD、LLVM 工具和 CMake 包描述。仅有 `clang` 命令不够。不要用一个小版本的插件加载到另一个小版本的 Clang 中。

没有对应二进制包时，可在用户目录从固定源码构建 SDK。下面是可选的上游构建路径；本次验证使用发行包中的匹配 SDK，不需要重编 LLVM。

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

宿主插件和处理程序使用 C++26。`just host` 接受 `CC`、`CXX`、`SDK`、`SYSROOT`、`FTXUI_SOURCE_DIR`、`JOBS`、`HOST_BUILD`；也可以直接用 CMake 的 `CMAKE_C_COMPILER`、`CMAKE_CXX_COMPILER`、`CMAKE_SYSROOT` 或工具链文件。指定 glibc 时需要**完整的 sysroot**，含兼容的头文件、启动文件、动态加载器、C++ 标准库及所链接的依赖；改变工具链后使用新构建目录。SDK 携带的 Clang 进程和插件必须使用兼容的 C++ ABI。

内核和驱动是由 Kbuild 编译的 GNU C，不链接 glibc。`tools/kernel.py` 的构建命令均接受 `--cc`、`--llvm`、`--hostcc`、`--hostcxx` 和 `--host-sysroot`；后者只影响宿主工具。外部驱动与目标内核使用同一配置、架构和工具链。客体程序的 glibc 由实际 Ubuntu 根文件系统决定，通过 `qemu/guest.py run --rootfs ... --rootfs-sha256 ...` 选择，不要在运行中的宿主机替换 libc。

## 建立板级基线

上游目标名为 `allyesconfig`，没有 `allyesdefconfig`。所有可见选项尽量取 `y` 不保证镜像能启动，也可能打开自测、压力测试、调试器、互斥驱动或有副作用的设备探测。先保留已启动成功的板卡配置和救援内核，再选择允许探索的范围。

以下以参考平台为例。所有输出均要求是新目录或新文件；不要传入上游源码目录或系统安装目录。

```sh
SDK="$PWD/.deps/llvm"
tools/kernel.py configure --llvm "$SDK" --base allnoconfig \
  --fragment qemu/guest.config --fragment examples/profile.config --out build/board-pins
tools/kernel.py configure --llvm "$SDK" --base allyesconfig \
  --fragment build/board-pins/.config --unpin CONFIG_IKCONFIG \
  --unpin CONFIG_NLS_CODEPAGE_850 --out build/board-baseline
```

第一个命令生成参考虚拟板卡的约束。第二个命令让上游宽配置机制选择被放开的选项，其余固定。已有实板 `.config` 时直接用目标板已审计的约束替换它们。若要探索更宽的空间，可以改用少量片段：

```sh
tools/kernel.py configure --llvm "$SDK" --base allyesconfig \
  --fragment qemu/guest.config --fragment examples/broad.config --out build/wide
```

此宽配置只验证过配置求解，**不作为已验收启动镜像**。实板还要固定启动存储、设备树、串口、固件、签名、安全、恢复等要求。`configure` 会核验每个明确要求是否被 Kconfig 接受。采集版要求模块和内核跟踪点可用，不能裁掉外部驱动所需的导出符号。本实现要求 `CONFIG_TRIM_UNUSED_KSYMS=n`。若强制签名，按现有签名流程给外部驱动签名；不要为方便而降低产品的安全策略。

## 清单：把证据关联到正确的选项

可先发现一份只读建议，再编辑策略：

```sh
tools/kernel.py inventory --under fs/nls --symbol CONFIG_NLS_CODEPAGE_850 \
  --out build/discovered.tsv
```

以 `examples/qemu.scope.tsv` 为完整例子：使用五列实际制表符，分别是连续编号、完整配置名、真实模块名或 `-`、策略、相对源码路径。默认 `keep`；只有确认所有相关对象、非代码副作用和板卡回退需求之后，才把可拆模块标为 `defer`，或将可放弃的功能标为 `drop`。多个文件可映射到同一个配置项，必须登记完整并使用一致的模块和策略。若一个选项会生成多个模块且不能唯一归属，保持 `keep`。自动发现不解释通用 Make 语言，不会推断源码内部的条件编译。首版支持审计过的 `drivers`、`fs`、`net`、`sound` 子系统以及内核配置读取组件，不对架构入口、内存管理或调度器做全局插桩。

## 在目标上采集

```sh
just host
tools/kernel.py profile --llvm "$SDK" --baseline build/board-baseline/.config \
  --scope examples/qemu.scope.tsv --plugin build/host/plugin/kcfg_frontend.so \
  --out build/board-profile
qemu/guest.py run --mode profile \
  --kernel build/board-profile/kernel/arch/x86/boot/bzImage \
  --module build/board-profile/driver/kcfg_collect.ko \
  --workload examples/workload.sh --output build/board-capture
```

移植到其他架构时在每个构建步骤传相同的 `--arch`，如 `--arch arm64 --image-target Image`（`configure` 不需要镜像目标）。编译支持不等于该板已验收：本仓库附带的 QEMU 测试机型和 Ubuntu 根文件系统为 x86_64。实板使用自身引导流程、设备树和用户空间。只在有独立回退入口时试启动采集版，不写入当前唯一可启动的槽位。

在**目标系统**上用管理员权限加载匹配的采集驱动，完成预热后操作字符设备。先确认产品负载成功；负载失败、后台工作尚未收尾或采集命令失败时，不能使用该次快照。以下命令在目标上运行，不是在宿主机上运行：

```sh
set -eu
insmod /path/to/kcfg_collect.ko
printf 'start\n' > /dev/kconfig-trace
/path/to/workload-and-wait
printf 'stop\n' > /dev/kconfig-trace
cat /dev/kconfig-trace > trace.tsv
```

把 `trace.tsv` 带回宿主机；保留同一次构建的整个 `campaign` 目录。一个启动一个窗口。业务稳态、启动恢复、设备热插拔等分别使用同一采集镜像重新启动记录，处理时使用 `--also` 合并。采集全机活动，不按进程过滤，也不要通过相减空闲快照来判定功能无用。

## 审阅、构建与延迟加载

```sh
build/host/processor/kcfg-plan build/board-profile/campaign \
  build/board-capture/trace.tsv build/board-plan --review
tools/kernel.py candidate --llvm "$SDK" --campaign build/board-profile/campaign \
  --plan build/board-plan --out build/board-candidate
```

在界面中用上下键导航，空格撤回或恢复建议，`s` 保存，`q` 取消。不加 `--review` 可批处理。`report.tsv` 解释每项决定。若 Kconfig 拒绝某项变化或连带修改了未授权选项，先复核依赖和板级策略，重新生成方案；不要用中间产物绕过校验。

对示例中保留默认建议的候选执行：

```sh
qemu/guest.py run --mode candidate \
  --kernel build/board-candidate/kernel/arch/x86/boot/bzImage \
  --late-module build/board-candidate/kernel/kernel/configs.ko \
  --workload examples/workload.sh --output build/board-replay
```

一般情形从 `deferred-paths.txt` 读取，而不是猜 `.ko` 路径；每一行相对于候选目录，硬依赖已经排在使用者前面。参考客体在就绪与负载启动之间按给定顺序 `insmod`。在真实系统中，按板卡现有流程把候选内核、匹配的模块树与固件放入独立试验槽并运行 `depmod`。将如下逻辑接到**明确的负载启动事件**，例如业务单元的 `ExecStartPre` 调用的脚本，而不是把它放进开机即读的 `modules-load.d`：

```sh
set -eu
while IFS= read -r module; do
  modprobe -- "$module"
done < /path/to/reviewed/modules-load.late
```

这不会自动阻止 initramfs、udev 别名、其他服务或模块依赖提前请求同一模块。实板验收时在目标就绪点检查 `/proc/modules`，再触发业务并核对加载时刻；不能控制早期请求就撤回延迟建议。目标没有模块加载器、依赖缺失或模块签名不受信任时，也应保留内建或修复板级打包。完成冷启动、业务、异常恢复和回滚演练后再考虑更换默认启动槽。

## 排障

| 现象 | 首先检查 |
|---|---|
| Clang 报插件加载失败，或版本不是 23.1.4 | 检查对应 SDK 的 `bin` 和 `lib` 均生效；不要混用系统 23.1.5 的共享库。 |
| 选项意外保留 | 对照 `coverage.tsv`、`objects.tsv` 和 `report.tsv` 区分无插桩、启动命中和策略保护。 |
| 驱动加载时报符号或版本错误 | 核对镜像、驱动和模块是否属于同一次采集版，是否信任模块签名；不要强制忽略版本校验。 |
| 开始或导出快照时报错 | 检查目标内权限、是否已开始或冻结、初始化钩子是否可用。重新启动再采集，不把空文件当成功。 |
| 客体未完成 | 看该次 `guest.status` 和 `console.log`；检查就绪前的内建驱动、负载退出状态以及延迟模块来源。 |

处理程序的文件协议见 `processor/README.md`；Ubuntu 镜像校验、附加输入文件和软件模拟见 `qemu/README.md`。
