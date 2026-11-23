# RISC-V 64 实板验证：从原生全选配置到负载裁剪

在 x86_64 上构建和审阅，在同一块运行 Ubuntu、由 GRUB 引导的 riscv64 板上采集和回放。最终比较两份实际安装的内核运行包，以及它们从内核启动到同一就绪点的时间。

| 板上启动的内核 | 配置来源 | 用途 |
|---|---|---|
| 基线 | 上游原生 `allyesconfig`，加上有记录的必要调整 | 先验证启动和业务，再作为体积、时间的分母。 |
| 采集版 | 同一份基线配置，加上构建时插桩 | 运行真实负载，把冻结的 `trace.tsv` 送回 x86_64。 |
| 候选 | x86_64 根据快照和板级策略生成、经 Kconfig 核验的完整配置 | 无插桩，回放同一业务，与基线配对测量。 |

## 一、先在板上确定启动契约

在原 Ubuntu 内核上执行，保存输出和现有 GRUB 可用条目。后续板端命令使用 Bash。

```bash
uname -m; uname -r; dpkg --print-architecture
cat /proc/cmdline
findmnt -T /; findmnt -T /boot; df -h / /boot
test -d /sys/firmware/efi && printf 'UEFI\n'
if command -v mokutil >/dev/null; then mokutil --sb-state; fi
if test -r /sys/firmware/devicetree/base/model; then tr '\0' '\n' < /sys/firmware/devicetree/base/model; fi
sudo cat /boot/grub/grub.cfg
```

- 记录板型和硬件修订、固件版本、根盘标识、文件系统、串口、设备树来源及安全启动状态。保存原配置 `/boot/config-$(uname -r)`、原菜单项、镜像、initrd 和模块；用串口或本地控制台实际选一次原条目，确认能够恢复。用测试盘或可还原的系统副本运行全选内核，尤其先审查上电自检和可能写存储的驱动测试。
- 核对原菜单中加载内核、initrd 和外置设备树的命令及路径。本文打包 **GRUB 能直接加载的 RISC-V EFI 镜像**：未压缩的 EFI stub `Image`，或 EFI 自解压的 `vmlinuz.efi`。`/boot/vmlinuz-*` 只是文件名；普通 `Image.gz` 和 EFI 自解压镜像格式不同。固件已提供设备树或 ACPI 时沿用它；原条目显式加载设备树时，三版使用同一块板的 DTB 和覆盖文件。
- 用原内核先跑通真实业务。准备 `/opt/board-test/run-and-check`：以业务账户使用固定输入，等待全部工作结束，校验业务结果，成功才返回零。下文使用这个现场入口。每轮从同一份测试数据副本开始，固定服务、固件和外设。若板依赖固定上游源码没有的厂商驱动或只能启动厂商容器格式，先解决板级支持和受信封装，再开始比较。

x86_64 构建和打包不需要 root。板上安装、生成 initrd、加载模块、访问采集设备和重启需要管理员权限。使用板上既有的签名制度，私钥留在签名端。

## 二、在 x86_64 编译 LLVM、插件和处理器

从仓库根目录打开 Bash。`RUN` 是本轮唯一的短名称；`BOARD` 换成板上的 SSH 账户。宿主依赖见[工具链准备](usage.md#准备工具链)，另需 `dpkg-deb`、`grub-file`、kmod、SSH 和压缩工具。板端需 systemd、kmod、`initramfs-tools` 及已有的 GRUB。整个实验使用同一终端中的变量；新开终端时恢复相同路径。

```bash
set -euo pipefail
export LC_ALL=C
ROOT=$(pwd -P); RUN=rvboard01; BOARD=lab@board.example
export LINUX="$ROOT/.deps/linux" SDK="$ROOT/.deps/llvm-riscv" JOBS=8
W="$ROOT/build/$RUN"; HOST="$W/host"
mkdir -p "$ROOT/build"; mkdir "$W"
unset ARCH CROSS_COMPILE LLVM_IAS CC CXX SYSROOT CMAKE_TOOLCHAIN_FILE LOCALVERSION
unset KCONFIG_CONFIG KCONFIG_ALLCONFIG KBUILD_OUTPUT KBUILD_EXTMOD KBUILD_EXTMOD_OUTPUT MAKEFLAGS MFLAGS

tools/sources.py --llvm-source
export KBUILD_BUILD_USER=boardlab KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP="$(git -C "$LINUX" show -s --format=%cD HEAD)"
export SOURCE_DATE_EPOCH="$(git -C "$LINUX" show -s --format=%ct HEAD)"
cmake -S .deps/llvm-project/llvm -B "$W/llvm-build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$SDK" \
  -DLLVM_ENABLE_PROJECTS='clang;lld' -DLLVM_TARGETS_TO_BUILD='X86;RISCV;BPF' \
  -DLLVM_BUILD_LLVM_DYLIB=ON -DLLVM_LINK_LLVM_DYLIB=ON -DCLANG_LINK_CLANG_DYLIB=ON
cmake --build "$W/llvm-build" --target install --parallel "$JOBS"
SDK=$(realpath -e "$SDK"); LINUX=$(realpath -e "$LINUX")
export PATH="$SDK/bin:$PATH" LD_LIBRARY_PATH="$SDK/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$SDK/bin/clang" --version; "$SDK/bin/llvm-config" --targets-built
CC="$SDK/bin/clang" CXX="$SDK/bin/clang++" HOST_BUILD="$HOST" just check
file "$HOST/plugin/kcfg_frontend.so" "$HOST/processor/kcfg-plan"
ssh "$BOARD" "umask 077; mkdir -- '$RUN'"
```

应看到 Clang 23.1.4、`RISCV` 后端，以及两个 x86-64 主机产物。插件由主机 Clang 加载，处理器也只在主机运行。内核和 `.ko` 才是 RISC-V 产物：源码固定为 Linux 7.4-rc3，Kbuild 参数是 `ARCH=riscv`，位宽由 `.config` 决定。全 LLVM 与集成汇编器无需目标 glibc 或 GNU 交叉前缀。

## 三、建立原生基线

直接在空输出目录调用上游目标，不传片段，不使用 QEMU 配置。保存原件，只在副本上做必要调整。板方指定模块签名证书时，先设 `MODULE_SIGN_KEY` 为主机上包含私钥和对应证书的 PEM 绝对路径，或不含口令的密钥令牌 URI。

```bash
mkdir "$W/native" "$W/config"
env -u KCONFIG_ALLCONFIG -u KCONFIG_CONFIG make -C "$LINUX" O="$W/native" \
  ARCH=riscv LLVM="$SDK/bin/" allyesconfig
sha256sum "$W/native/.config" > "$W/native.sha256"
cp "$W/native/.config" "$W/config/.config"
"$LINUX/scripts/config" --file "$W/config/.config" --disable TRIM_UNUSED_KSYMS
if [[ -n ${MODULE_SIGN_KEY:-} ]]; then
    "$LINUX/scripts/config" --file "$W/config/.config" --set-str MODULE_SIG_KEY "$MODULE_SIGN_KEY"
fi
# 在此按原板启动契约补入确有依据的调整，再让 Kconfig 求解。
make -C "$LINUX" O="$W/config" ARCH=riscv LLVM="$SDK/bin/" olddefconfig
"$LINUX/scripts/diffconfig" "$W/native/.config" "$W/config/.config" > "$W/native-to-baseline.diff"
for sym in RISCV 64BIT ARCH_RV64I MMU EFI EFI_STUB BLK_DEV_INITRD MODULES TRACEPOINTS; do
    rg -qx "CONFIG_${sym}=y" "$W/config/.config"
done
[[ $("$LINUX/scripts/config" --file "$W/config/.config" -s TRIM_UNUSED_KSYMS) != y ]]
```

逐项记录差异、原因和证据。关闭导出符号裁剪是树外采集驱动的前提。本工具链生成的原生配置中，`CONFIG_BUILTIN_DTB=y` 而 `CONFIG_BUILTIN_DTB_NAME` 为空；应核对实际传入的是哪一块板的设备树。对照原板检查指令集、根盘、文件系统、initrd 解压、控制台和签名策略。三版固定模块签名策略和算法。若保留 Kbuild 自动生成密钥的默认值，各版只使用各自构建时签署的模块。

预留三棵构建树、打包暂存空间，以及板上三套模块和 initrd 的空间。遇到构建失败、镜像装不下或启动停滞，保留首个错误或串口日志，仅修正相应条件，重跑 `olddefconfig` 和差异检查。基线确定后，三版共用这些条件；手工调整不算负载裁剪收益。

## 四、打包与试启动：先完成基线

在主机上构建基线。`LOCALVERSION` 区分模块目录，不改变配置。`plain` 本身就是 Kbuild 输出目录，其他两版的目录在其 `kernel/` 子目录。

```bash
K=(--linux "$LINUX" --llvm "$SDK" --arch riscv --jobs "$JOBS" --image-target Image)
LOCALVERSION="-$RUN-base" tools/kernel.py plain "${K[@]}" \
  --baseline "$W/config/.config" --out "$W/plain"
"$SDK/bin/llvm-readelf" -h "$W/plain/vmlinux"
```

读到的 ELF 应为 `ELF64`、`RISC-V`。下面定义一次打包和传输函数，基线、采集版、候选都调用它。按第一节的核对结果设置 `FORMAT` 为 `Image` 或 `vmlinuz.efi`，设置 `EFI_POLICY` 为 `signed` 或 `unsigned`。前者需设置 `EFI_KEY`、`EFI_CERT` 并使用板已信任的证书；采用外部签名服务时，将函数中的签名两行替换为该服务的签发和验签步骤。只有试验板原策略允许未签镜像时才设 `unsigned`。

如需随包交付外置设备树，可运行 `make -C "$LINUX" O="$W/config" ARCH=riscv LLVM="$SDK/bin/" dtbs`，或取板方已经验证的文件。令 `BOARD_BOOT_FILES` 指向只含所选 DTB 和覆盖文件的新目录并 `export`；使用固件所供描述时不设置它。

```bash
# 示例：已确认加载未压缩 EFI stub，且板要求签名。
FORMAT=Image; EFI_POLICY=signed
# EFI_KEY 和 EFI_CERT 由签名端提供。
pack_send() {
    local kind=$1 suffix=$4 obj cc image idfile
    obj=$(realpath -e "$2"); cc="$(realpath -e "$(dirname "$3")")/${3##*/}"
    image="$obj/arch/riscv/boot/Image"
    if [[ $FORMAT == vmlinuz.efi ]]; then
        rg -qx 'CONFIG_EFI_ZBOOT=y' "$obj/.config"
        idfile="$W/$kind-before-zboot.sha256"
        (cd "$obj" && sha256sum vmlinux arch/riscv/boot/Image Module.symvers) > "$idfile"
        make -s --no-print-directory -C "$LINUX" O="$obj" ARCH=riscv LLVM="$SDK/bin/" \
          KBUILD_ABS_SRCTREE=1 CC="$cc" LOCALVERSION="$suffix" -j"$JOBS" vmlinuz.efi
        (cd "$obj" && sha256sum -c "$idfile")
        image="$obj/arch/riscv/boot/vmlinuz.efi"
    else
        [[ $FORMAT == Image ]]
    fi
    case "$EFI_POLICY" in
        signed) sbsign --key "${EFI_KEY:?}" --cert "${EFI_CERT:?}" --output "$W/$kind.signed.efi" "$image"
                sbverify --cert "$EFI_CERT" "$W/$kind.signed.efi"; image="$W/$kind.signed.efi" ;;
        unsigned) : ;;
        *) return 1 ;;
    esac
    bash docs/riscv64/package.sh "$W" "$SDK" "$kind" "$image"
    scp -r "$W/packages/$kind" "$BOARD:$RUN/"
}
pack_send baseline "$W/plain" "$SDK/bin/clang" "-$RUN-base"
```

构建工具接受 `--image-target Image`。若板需要 `vmlinuz.efi`，上面的函数用原编译器和发行后缀调用 Kbuild 单独封装，核对载荷未变。采集内核或原始驱动与 `campaign/build.json` 中的摘要不符时，停止发包；重新构建的采集版须重新采集。

[打包示例](riscv64/package.sh)把实际启动的镜像、配置、`System.map`、该发行版本的全部运行模块及其索引、延迟清单和指定的设备树装入一个 `riscv64` 的 `.deb`。三版用同一剥离、签名及压缩流程。它在临时根运行 `modules_install`；采集版另安装树外模块并核对其版本和签名。EFI 映像签名与内核模块签名是两套信任关系；启用强制模块签名时，这份示例要求 `CONFIG_MODULE_SIG_ALL=y` 并可访问各构建所配置的模块签名密钥。

三版各执行一次板端安装步骤，`kind` 依次取 `baseline`、`profile`、`candidate`。每次重新登录先设置变量。下例使用新路径，不替换发行版的 `/boot/vmlinuz`、EFI 程序或默认菜单项。

```bash
set -euo pipefail
RUN=rvboard01; kind=baseline
cd "$HOME/$RUN/$kind"; sha256sum -c SHA256SUMS
rel=$(<release); slot="/boot/board-trial/$rel"
[[ $(uname -m) == riscv64 && $(dpkg --print-architecture) == riscv64 ]]
[[ $(dpkg-deb -f kernel.deb Architecture) == riscv64 && $rel != "$(uname -r)" ]]
for path in "$slot" "/lib/modules/$rel" "/boot/config-$rel" "/boot/System.map-$rel"; do
    [[ ! -e $path && ! -L $path ]]
done
dpkg-deb -c kernel.deb > package-files.txt
sudo dpkg -i ./kernel.deb
sudo depmod -a "$rel"
sudo mkinitramfs -o "$slot/initrd.new" "$rel"
sudo lsinitramfs "$slot/initrd.new" > initrd-files.txt
sudo mv -- "$slot/initrd.new" "$slot/initrd"
```

核对 `initrd-files.txt`、`/boot/config-$rel` 和原条目：根盘控制器、根文件系统、解密或卷管理所需组件必须内建或已进入 initrd；固件须可用。固定三版的 initramfs 工具、压缩设置和选驱动规则。为目标发行版本分别生成 initrd，不把旧内核的模块塞进新内核。

在串口或本地 GRUB 菜单选中原来的可启动项，按 `e` **只编辑这一次启动**：保留原有磁盘搜索、根分区、控制台和其他参数，把内核与 initrd 的文件路径分别指向试验槽的 `linux` 和 `initrd`。保留原有早期 initrd 的顺序；需要外置设备树时沿用原加载命令，指向槽内 `board/` 对应文件。GRUB 以独立 `/boot` 分区为根时路径从 `/board-trial/` 开始，否则按原条目的 `/boot/…` 写法。按菜单提示启动。若 GRUB 限制编辑，由管理员预建等价的受信试验项并手工选择，原项仍为默认。

```bash
# 仅在基线上执行。采集版按第五节启动采集后再跑业务，候选按第六节先加载延迟模块。
RUN=rvboard01; kind=baseline
[[ $(uname -r) == "$(<"$HOME/$RUN/$kind/release")" ]]
findmnt -T /; systemctl is-active multi-user.target
/opt/board-test/run-and-check
```

基线须成功挂载原测试根盘、到达就绪点并通过业务校验，再进入采集。若某版卡死，用控制台复位并从原菜单项恢复；保留失败日志，不把失败的启动填为零秒。

## 五、采集版上板，快照回传 x86_64

在主机从源码树的直接 Kbuild 规则起草清单。按板上硬件和业务审阅副本，选出本轮能确认归属和策略的文件；未登记的配置会保持原值。不要复制 QEMU 的裁剪授权。

```bash
tools/kernel.py inventory --linux "$LINUX" --under . --out "$W/inventory.tsv"
cp "$W/inventory.tsv" "$W/scope.tsv"
# 编辑 scope.tsv，保留本轮条目，按 1 至 N 重新编号并审定策略，然后构建。
tools/kernel.py profile "${K[@]}" --baseline "$W/config/.config" \
  --scope "$W/scope.tsv" --plugin "$HOST/plugin/kcfg_frontend.so" --out "$W/profile"
CAMP="$W/profile/campaign"; id=$(<"$CAMP/campaign.id")
"$SDK/bin/llvm-readelf" -h "$W/profile/driver/kcfg_collect.ko"
pack_send profile "$W/profile/kernel" "$CAMP/clang" "-kcfg-${id:0:16}"
```

清单是制表符分隔的连续编号、配置名、模块名或 `-`、策略、相对源码路径。起草结果均为 `keep`；确认可缺省的功能才标 `drop`，确认启动后可加载且模块名明确的才标 `defer`。保留启动存储、控制台、安全和恢复所需功能，以及 SSH 回传依赖的管理网络。查看 `coverage.tsv` 和 `objects.tsv`：覆盖不足的配置保持原值。保留整个 `campaign/` 在主机上，改清单或基线后须重新构建、重新采集。详见[登记组件](usage.md#登记组件)。

先在原系统或离线快照中复原测试输入，再按第四节以 `kind=profile` 安装并启动，避免把复原数据的操作记为启动需求。完成与基线相同的预热，到约定就绪点后，在板端执行以下命令；负载仍以原业务账户运行，只有设备操作使用 `sudo`。

```bash
set -euo pipefail
RUN=rvboard01; D="$HOME/$RUN"; rel=$(<"$D/profile/release")
[[ $(uname -r) == "$rel" ]]; systemctl is-active --quiet multi-user.target
vm=$(modinfo -F vermagic kcfg_collect); [[ ${vm%% *} == "$rel" ]]
sudo modprobe kcfg_collect; test -c /dev/kconfig-trace
mkdir "$D/capture-01"; cd "$D/capture-01"
sudo sh -c 'printf "start\n" > /dev/kconfig-trace'
rc=0; /opt/board-test/run-and-check > workload.log 2>&1 || rc=$?
sudo sh -c 'printf "stop\n" > /dev/kconfig-trace'
printf '%s\n' "$rc" > workload.exit; [[ $rc == 0 ]]
sudo cat /dev/kconfig-trace > trace.tsv.part; test -s trace.tsv.part
mv trace.tsv.part trace.tsv; sha256sum trace.tsv > SHA256SUMS
```

每次启动只有一个整机窗口。`start` 之前的普通活动计入启动，初始化调用链单列。未通过业务校验的轮次保留日志、不发布快照。其他业务或恢复场景用同一采集版重新启动，分别保存到新的目录。

```bash
# 主机，从板拉回原始文件；处理器校验构建身份、每个编号和结束行。
scp -r "$BOARD:$RUN/capture-01" "$W/"
(cd "$W/capture-01" && sha256sum -c SHA256SUMS)
IFS=$'\t' read -r magic ver trace_id < "$W/capture-01/trace.tsv"
[[ $magic == KCFG-TRACE && $ver == 1 && $trace_id == "$(<"$CAMP/campaign.id")" ]]
"$HOST/processor/kcfg-plan" "$CAMP" "$W/capture-01/trace.tsv" "$W/plan" --review
```

在终端审阅后按 `s` 保存。合并同一构建的其他成功快照时，在处理器命令后追加 `--also 路径`。查看 `plan/report.tsv`、`changes.tsv` 和 `modules-load.late`，核对每项改动的授权、启动命中和覆盖。未登记或覆盖不足不构成删除依据。

## 六、生成候选并回放同一负载

```bash
# 主机
LOCALVERSION="-$RUN-cand" tools/kernel.py candidate "${K[@]}" \
  --campaign "$CAMP" --plan "$W/plan" --out "$W/candidate"
pack_send candidate "$W/candidate/kernel" "$SDK/bin/clang" "-$RUN-cand"
```

最终配置是 `candidate/.config`。`candidate/deferred-paths.txt` 给出构建树里的模块和硬依赖顺序，包里的 `modules-load.late` 给出部署用模块名。若 `olddefconfig` 引入未授权变化，先复核计划；不要手改已发布的配置或沿用旧快照冒充新采集。

按第四节以 `kind=candidate` 安装并启动。候选每次回放均在**就绪后、业务前**执行下面的板端命令；基线在同一就绪点直接运行同一负载。

```bash
set -euo pipefail
RUN=rvboard01; rel=$(<"$HOME/$RUN/candidate/release")
[[ $(uname -r) == "$rel" ]]; systemctl is-active --quiet multi-user.target
late="/boot/board-trial/$rel/modules-load.late"
while IFS= read -r name; do [[ ! -e /sys/module/${name//-/_} ]]; done < "$late"
while IFS= read -r name; do
    sudo modprobe -- "$name"; [[ -d /sys/module/${name//-/_} ]]
done < "$late"
/opt/board-test/run-and-check
```

对照两版的退出码和业务结果，例如事务数、校验和、持久化结果。若模块在就绪前已加载，检查 initrd、udev 和服务依赖；调整加载时机或撤回延迟建议后再验收。启动通过而业务失败，仍判该候选失败。

## 七、比较运行包字节数与启动时间

**包大小**取第四节生成且板上验过摘要的单个 `kernel.deb` 的字节数。统计整套运行模块，包括从内建改成模块的功能；排除开发头文件、调试用 `vmlinux` 和仅在采集版使用的驱动。initrd 在板上生成，不在 `.deb` 内；若还关心启动分区空间，另记每版 `stat -c %s /boot/board-trial/发行版本/initrd`，不要把这两个口径混为一个百分比。

**启动时间**主指标取内核启动至 `multi-user.target` 首次激活的单调时钟微秒数，含 initrd 和 Ubuntu 用户空间，不含固件和 GRUB。另记内核日志中 `Run /init as init process` 的时间戳，表示内核即将执行 initrd 第一个用户进程。两列分别报告，可区分内核阶段与系统就绪阶段。两版均使用同一 `/init` 和首次就绪目标；不在目标激活前运行测试负载或加载延迟清单。若业务关心首次响应，再单列就绪至首个成功请求的耗时，计入延迟加载的代价。

基线和候选各预热一次不计时。随后在同一块板上做九对启动：奇数对先基线，偶数对先候选。固定重启方式、固件、GRUB 参数、根盘及数据、服务和外设；记录温度或供电异常。**每次**核对 `uname -r`、执行对应的业务回放并保存结果，再记录本次启动；人工输入、SSH 登录和事后读取不会改变已存的就绪时间戳。

```bash
# 板端，每次成功回放后填写当前对号和角色。正式测量从新的 boots.tsv 开始。
set -euo pipefail; export LC_ALL=C
RUN=rvboard01; D="$HOME/$RUN"; pair=1; kind=baseline
rel=$(uname -r); [[ $rel == "$(<"$D/$kind/release")" ]]
ready_us=$(systemctl show multi-user.target -p ActiveEnterTimestampMonotonic --value)
kernel_s=$(sudo dmesg --color=never --time-format=raw | awk -F '[][]' \
  '/Run \/init as init process$/ {gsub(/^[[:space:]]+/, "", $2); print $2}')
[[ $ready_us =~ ^[1-9][0-9]*$ && $kernel_s =~ ^[0-9]+([.][0-9]+)?$ ]]
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$pair" "$kind" "$(</proc/sys/kernel/random/boot_id)" \
  "$rel" "$kernel_s" "$ready_us" >> "$D/boots.tsv"
```

宽内核可能在读取前覆盖早期日志。先在预热轮确认两列都能取得；必要时两版统一增大日志缓冲区或统一从带内核时间戳的串口日志取同一标记。保留所有原始日志、失败和超时记录；缺测的对重新测量，不挑选最快启动。实际验收失败则先修复并重新验证，再报告性能。

```bash
# 主机：检查配对、发行版本和启动标识，输出字节数、秒数、中位数、范围及裁剪率。
scp "$BOARD:$RUN/boots.tsv" "$W/boots.tsv"
uv run --no-project python -I -u - "$W" <<'PY'
import csv, statistics, sys
from pathlib import Path
w = Path(sys.argv[1]); kinds = ('baseline', 'candidate')
pkgs = {k: w / 'packages' / k for k in kinds}
rels = {k: (p / 'release').read_text().strip() for k, p in pkgs.items()}
rows = list(csv.reader((w / 'boots.tsv').read_text().splitlines(), delimiter='\t'))
expected = [(str(i), k) for i in range(1, 10) for k in (kinds if i % 2 else kinds[::-1])]
if (any(len(r) != 6 for r in rows) or [(r[0], r[1]) for r in rows] != expected or
    len({r[2] for r in rows}) != 18):
    raise SystemExit('expected nine alternating pairs and eighteen distinct boots')
times = {k: [] for k in kinds}
for pair, kind, boot_id, rel, kernel, ready in rows:
    a, b = float(kernel), int(ready) / 1_000_000
    if rel != rels[kind] or not 0 < a <= b: raise SystemExit('invalid release or time')
    times[kind].append((a, b))
def report(metric, before, after):
    print(f'{metric}: baseline={before} candidate={after} reduction={(before-after)*100/before:.3f}%')
report('package bytes', *[(pkgs[k] / 'kernel.deb').stat().st_size for k in kinds])
for i, label in enumerate(('kernel to /init (s)', 'kernel to ready (s)')):
    samples = [[v[i] for v in times[k]] for k in kinds]
    print(label, 'ranges (baseline, candidate):', *[(min(s), max(s)) for s in samples])
    report(label, *map(statistics.median, samples))
PY
```

裁剪率统一为 `100 ×（基线值 − 候选值）／基线值`；负数表示包变大或启动变慢。归档原生配置、必要调整及理由、三版发行版本和包摘要、快照、计划、最终配置、业务校验与逐次计时。基线和候选都通过实板验收后，才把这组数字作为该板、该负载的结果。
