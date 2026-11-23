# 在 RISC-V 板上裁剪并比较内核

x86_64 主机构建内核。Ubuntu 板按顺序启动三次：基线测原始表现，采集版运行负载并导出快照，候选版用同一负载验证裁剪结果。下文标明每条命令在哪台机器执行。

## 一、安装依赖

主机（以 Ubuntu 为例）：

```bash
sudo apt update
sudo apt install build-essential git cmake ninja-build python3 flex bison bc libelf-dev libssl-dev dwarves kmod cpio rsync zstd lz4 lzop xz-utils openssh-client
```

主机构建需要 CMake 3.30 以上和 Python 3.11 以上。若发行版提供的 CMake 较旧（如 Ubuntu 24.04），执行 `sudo apt install pipx && pipx install 'cmake>=3.30'`，然后执行 `export PATH="$HOME/.local/bin:$PATH"`。

板端（已有 GRUB 和 systemd 的 Ubuntu）：

```bash
sudo apt install initramfs-tools kmod rsync
mkdir ~/board-01
```

`board-01` 是本轮的空目录，用于接收安装包和保存本轮数据。准备一条能以业务账户运行的负载命令。下文用 `/opt/board-test/run-and-check` 代表它：命令等待任务完成，校验业务结果，成功返回零。三次使用相同输入和就绪点。

## 二、主机：一次设置变量并准备工具

从项目根目录打开 Bash：

```bash
cp examples/board.env board.env
${EDITOR:-vi} board.env
source ./board.env
./kcfg tools
```

在 `board.env` 中填写板的登录地址 `BOARD`，以及 **GRUB 当前默认项实际读取的内核路径 `GRUB_KERNEL` 和主 initrd 路径 `GRUB_INITRD`**。可在板上用 `sudo less /boot/grub/grub.cfg` 查看该项的 `linux` 和 `initrd` 行。这两个文件将被直接覆盖，三次填写同一对路径。例如独立挂载 `/boot` 时，菜单里的 `/vmlinuz-6.8.0-31-generic` 在 Linux 中是 `/boot/vmlinuz-6.8.0-31-generic`。按板上实际菜单填写；若同一行还有微码镜像，保留微码路径不变。

同一文件中可设置本地 Linux、LLVM、FTXUI 的位置，以及允许关闭（`DROP`）或延迟加载（`DEFER`）的配置名。两项都留空时只观测，不会建议裁剪。未指定依赖时，`./kcfg tools` 自动下载缺少的源码，编译 LLVM、Clang、LLD，再编译宿主插件和处理器；已有 `SDK` 时直接使用。只想先下载时执行 `./kcfg fetch`。新开主机终端时重新执行 `source ./board.env`。

## 三、主机：建立基线、构建、打包和传输

```bash
./kcfg config
./kcfg build baseline && ./kcfg build profile
./kcfg pack baseline && ./kcfg pack profile
scp "$WORK/packages/"{baseline,profile}.tar.gz "$BOARD:$BOARD_DIR/"
```

`config` 在新输出目录直接调用上游 `allyesconfig`，保存原件，再满足采集所需的配置。板厂驱动、根盘、固件或启动格式需要额外调整时，在第二步设置 `BOARD_CONFIG` 指向相应片段。最终差异见 `$WORK/config/changes.tsv`，三版都从这份基线出发。

两版默认都构建 `Image`、模块和设备树；只有采集版带插桩和采集驱动。板用固件提供的设备树时保持原样。若默认项有 `devicetree` 命令，在变量文件中另填 `DTB` 和 `GRUB_DTB`。默认项要求 EFI 自解压镜像或签名时，见[启动文件](usage.md#启动文件)。

## 四、板端：启动基线，然后采集同一负载

在板上自己的终端执行：

```bash
cd ~/board-01 && tar -xzf baseline.tar.gz && sudo ./baseline/install && sudo reboot
```

重启后，用业务账户执行：

```bash
~/board-01/baseline/run /opt/board-test/run-and-check
```

基线通过后安装采集版：

```bash
cd ~/board-01 && tar -xzf profile.tar.gz && sudo ./profile/install && sudo reboot
```

再次重启后：

```bash
~/board-01/profile/run /opt/board-test/run-and-check
```

安装命令把该版模块装入 `/lib/modules/<release>/`，生成匹配的 initrd，并替换默认项引用的启动文件；重启沿用原有 GRUB 菜单和内核参数。基线成功后得到 `~/board-01/baseline/boot.tsv`；采集版在负载前开始记录，成功后冻结并导出 `~/board-01/profile/trace.tsv`。

## 五、主机：生成裁剪后的配置和内核包

```bash
scp "$BOARD:$BOARD_DIR/baseline/boot.tsv" "$WORK/baseline.tsv"
scp "$BOARD:$BOARD_DIR/profile/trace.tsv" "$WORK/trace.tsv"
./kcfg plan && ./kcfg build candidate
./kcfg pack candidate && scp "$WORK/packages/candidate.tar.gz" "$BOARD:$BOARD_DIR/"
```

生成的完整配置是 **`$WORK/candidate/.config`**。`$WORK/plan/report.tsv` 解释每项为何保留、关闭或改成模块。要在终端调整建议，用 `./kcfg plan --review` 代替 `./kcfg plan`，按 `s` 保存。

## 六、板端回放，主机比较

板端：

```bash
cd ~/board-01 && tar -xzf candidate.tar.gz && sudo ./candidate/install && sudo reboot
```

重启后：

```bash
~/board-01/candidate/run /opt/board-test/run-and-check
```

候选就绪后先加载延迟模块，再运行负载。成功后，在主机执行：

```bash
scp "$BOARD:$BOARD_DIR/candidate/boot.tsv" "$WORK/candidate.tsv"
./kcfg compare
```

结果给出基线与候选的 `.tar.gz` 字节数、到 `multi-user.target` 的启动秒数及两项缩减率；负数表示变大或变慢。单次启动可跑通流程。正式测量时，在同一块板上分别多次重启并成功回放，保留各自的 `boot.tsv`，传回后用同一条命令比较中位数。包内含运行模块和设备树；板上生成的 initrd 不计入包大小。
