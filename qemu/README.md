# Ubuntu 客体验证器

验证器用指定的 x86_64 Linux 内核启动 Ubuntu Base，在新建的内存根文件系统中运行负载。宿主机使用普通账户；挂载和模块加载由客体初始进程完成。虚拟机不连接网络或映射宿主目录。

## 准备环境

宿主机需要 Python 3.11 或更高版本、uv、QEMU、bsdtar、readelf 和 x86_64 静态 BusyBox；下载时需 curl，加载显式模块时需宿主机的 `modinfo`。可用 `--qemu` 和 `--busybox` 指定程序。`/dev/kvm` 可读写时默认使用硬件加速，也可显式指定 `--accel tcg` 使用软件模拟。

```sh
qemu/guest.py fetch
```

默认镜像为 [Ubuntu Base 24.04.4 amd64](https://cdimage.ubuntu.com/ubuntu-base/releases/24.04.4/release/) 的 `ubuntu-base-24.04.4-base-amd64.tar.gz`。固定摘要取自 Ubuntu 签名发行清单；每次使用时校验归档并直接打包为内存启动镜像。

```text
c1e67ef7b17a6300e136118bd1dc04725009cb376c1aad10abcf8cd453628d58
```

离线可传 `--rootfs /path/to/archive.tar.gz`，自备归档还要传可信的 `--rootfs-sha256`。客体需提供 `/bin/sh`、`mount`、`cat`、`uname`、`sleep`、`sha256sum` 和负载所需的运行库。将 `qemu/guest.config` 合入目标内核配置，保持串口、PCI virtio 控制台、内存启动镜像和所需文件系统内建。

## 启动与采集

每次运行使用新输出目录。以下路径以 `just demo build/my-first-run` 的产物为例：

```sh
RUN=build/my-first-run
qemu/guest.py run --mode baseline --kernel "$RUN/plain/arch/x86/boot/bzImage" \
  --workload examples/workload.sh --output build/guest/baseline-01
qemu/guest.py run --mode profile --kernel "$RUN/profile/kernel/arch/x86/boot/bzImage" \
  --module "$RUN/profile/driver/kcfg_collect.ko" \
  --workload examples/workload.sh --output build/guest/profile-01
qemu/guest.py run --mode candidate --kernel "$RUN/candidate/kernel/arch/x86/boot/bzImage" \
  --late-module "$RUN/candidate/kernel/kernel/configs.ko" \
  --workload examples/workload.sh --output build/guest/candidate-01
```

客体核对发行版本后按 `--module` 顺序预加载模块，报告就绪。`profile` 模式随后启动采集。各模式再按 `--late-module` 顺序加载模块并运行负载；负载成功退出后，采集版冻结并导出快照。显式加载使用未压缩的 `.ko`，硬依赖排在使用者前；启用强制签名时使用内核信任的密钥。

`--workload` 接受可执行程序或带解释器声明的脚本，以退出状态报告结果。`--payload` 可重复，将文件放入客体的 `/kernel-test/input/`。需要在负载中途加载的模块可作为输入文件传入，由负载核对版本和依赖后调用 `/kernel-test/busybox insmod`。

## 输出与排查

| 文件 | 内容 |
|---|---|
| `guest.status` | 发行版、内核版本、就绪状态、延迟加载数量和退出结果。 |
| `console.log` | 客体启动日志和负载输出。 |
| `trace.tsv` | 采集版成功退出后，经主客体摘要核对的完整快照。 |

默认两颗虚拟处理器、1536 MiB 内存和 180 秒超时，可用 `--cpus`、`--memory`、`--timeout` 调整。遇到失败，先用 `guest.status` 定位阶段，再在 `console.log` 核对启动驱动、模块版本及签名或负载输出。重新启动时使用新目录。
