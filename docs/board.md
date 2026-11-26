# Ubuntu 实板：构建、部署与采集

默认用 x86_64 主机构建，在带 GRUB 的 x86_64 Ubuntu 设备上部署。若目标板是 riscv64，在主机的 `.env` 中将 `ARCH` 改为 `riscv` 并选用该板的 64 位配置；其余命令相同。

将 `ubuntu@board` 换为设备地址，将 `/opt/board-test/run-and-check` 换为自己的负载命令。它应等待业务完成并用退出状态报告结果。示例使用默认输出目录和包名。

## 主机：准备依赖和配置

在 Ubuntu 构建机上安装工具：

```bash
sudo apt update
sudo apt install build-essential git cmake ninja-build python3 bc flex bison libelf-dev libssl-dev dwarves kmod cpio rsync zstd lz4 lzop xz-utils openssh-client
```

宿主工程使用 CMake 3.30 或更新版本、Python 3.11 或更新版本。自动导出依赖图还需要 `uv`；在 `.env` 中指定已有的 `DEPENDENCY_GRAPH` 可直接读取。若系统自带的 CMake 较旧，可运行 `sudo apt install pipx && pipx install 'cmake>=3.30'`，并将 `$HOME/.local/bin` 加到 `PATH`。板端需要 `initramfs-tools`、`kmod` 及现有 GRUB 提供的 `update-grub`。

在主机仓库根目录执行：

```bash
cp .env.example .env
${EDITOR:-vi} .env
```

首次实验先确定两件事：

1. 用什么基线？`KERNEL_CONFIG=''` 表示原生 `allyesconfig` 加采集开关。已有能启动这块板的完整配置时，填写主机上的路径，例如 `KERNEL_CONFIG=/src/board.config`；脚本复制它并运行 `olddefconfig`。内核配置和镜像格式要能启动本板、挂载根盘并使用 initramfs。
2. 哪些组件参与分析？默认观测自动发现的组件，覆盖完整且启动、负载均未命中的选项才参与关闭。要限定范围，将选项写入 `DROP=(...)` 或 `DEFER=(...)`。例如，允许推迟 `/proc/config.gz` 的读取模块可写 `DEFER=(CONFIG_IKCONFIG)`。板子必须保留的开关可以写入 `FORCE_CONFIG` 指向的文件。

其余项可保留默认值。已有源码或 LLVM 工具链时，在同一文件指定位置，见[脚本参考](usage.md)。

## 主机：生成采集包

```bash
./kcfg --init-deps
./kcfg --build-tools
./kcfg --build-kernel profile
scp build/board/packages/kernel-profile.tar.gz ubuntu@board:~/
```

第三条命令完成配置、编译和打包。采集元数据保存在主机的 `build/board/profile/campaign/`。如需一份无插桩的对照内核，在相同设置下另运行 `./kcfg --build-kernel baseline`。

## 板端：安装并启动新内核

在板上自己的终端运行：

```bash
cd ~ && tar -xzf kernel-profile.tar.gz
sudo ./kernel-profile/deploy.sh
sudo reboot
```

`deploy.sh` 安装带发行版本名的文件，生成该版 initramfs，并运行 `update-grub` 添加菜单项。重启时在 GRUB 中选择与 `kernel-profile/release` 对应的版本；登录后 `uname -r` 显示正在运行的版本。板上无需 `.env`。

## 板端：运行一次负载，导出快照

待 `multi-user.target` 和业务所需服务就绪，以平时的业务账户在新目录中运行以下命令。只有加载模块和读写采集设备需要 `sudo`。

```bash
mkdir ~/capture-01 && cd ~/capture-01
(
    set -e
    sudo modprobe kcfg_collect
    sudo sh -c 'printf "start\n" > /dev/kconfig-trace'
    status=0; /opt/board-test/run-and-check || status=$?
    sudo sh -c 'printf "stop\n" > /dev/kconfig-trace'
    [ "$status" -eq 0 ] || exit "$status"
    sudo cat /dev/kconfig-trace > trace.tsv
)
```

成功后得到 `~/capture-01/trace.tsv`。负载失败时窗口也会停止，请在重启后重新采集。每次启动有一个采集窗口；观测另一个负载时重启同一采集内核，换新目录保存快照。

## 主机：生成候选配置与部署包

```bash
scp ubuntu@board:~/capture-01/trace.tsv build/board/trace.tsv
./kcfg --plan
./kcfg --build-kernel candidate
scp build/board/packages/kernel-candidate.tar.gz ubuntu@board:~/
```

第二条命令进入编辑器。按 `a` 分析，按 `d` 只看差异，按 `A` 应用后再构建候选。`build/board/plan/report.tsv` 记录保留和修改的理由；`build/board/candidate/.config` 是候选构建实际采用的配置。

## 板端：回放同一负载

```bash
cd ~ && tar -xzf kernel-candidate.tar.gz
sudo ./kernel-candidate/deploy.sh
sudo reboot
```

选择候选内核。系统和业务到达同一就绪点后，再加载延迟模块并回放：

```bash
xargs -r -n1 sudo modprobe -- < ~/kernel-candidate/modules-load.late && /opt/board-test/run-and-check
```

`modprobe` 会解析模块依赖。若板上的 initramfs 或服务已提前加载某个模块，改成 `m` 本身不能推迟它；用板上实际的加载策略和同一负载确认结果。
