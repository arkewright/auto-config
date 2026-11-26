# QEMU 开发测试

QEMU 在宿主机上引导 x86_64 内核和 Ubuntu Base，用于回归采集、裁剪与负载回放。实板构建和部署不需要 QEMU。

## 四次启动

先用项目 `.env` 选择 Linux 源码和带 X86 后端的 LLVM，在宿主机上构建插件和处理器。回放使用 `uv` 导出客体配置的依赖图，通过 `--plan --batch` 完成分析。QEMU 测试额外使用 `qemu-system-x86_64`、静态 x86_64 BusyBox、bsdtar、readelf、modinfo 和 ripgrep。首次下载 Ubuntu Base 用到 curl；离线回放还需提前缓存解析器的 Python 依赖。

```sh
./kcfg --init-deps
./kcfg --build-tools
qemu/replay.sh build/qemu-01 .env
```

最后一条也可写成 `just replay build/qemu-01`。使用尚不存在的输出目录；脚本继承指定配置中的工具链和宿主构建位置，使用独立的 x86_64 配置、观测清单和包目录。它以 `allnoconfig` 和两个客体片段生成测试基线，再通过 `./kcfg --env` 编译基线、采集版、候选版，运行[四次启动](../docs/validation.md#复现)。

默认首次启动时下载 Ubuntu Base 24.04.4 amd64，之后复用缓存。离线时可指定本机归档：

```sh
QEMU_ROOTFS=/data/ubuntu-base-24.04.4-base-amd64.tar.gz qemu/replay.sh build/qemu-02 .env
```

可用 `QEMU_BUSYBOX` 指定静态 BusyBox，`QEMU_ACCEL=tcg` 强制软件模拟。默认在 `/dev/kvm` 可用时启用硬件加速。

## 单次启动与排查

也可在已有的测试构建上运行其他负载。例如，从仓库根目录启动采集版并运行自带的配置读取负载：

```sh
RUN=build/qemu-01
qemu/guest.py run --mode profile --kernel "$RUN/profile/kernel/arch/x86/boot/bzImage" \
  --module "$RUN/profile/driver/kcfg_collect.ko" --workload examples/workload.sh \
  --output "$RUN/guests/second-profile"
```

直接运行 `guest.py` 使用 uv，也可用 Python 3.11 或更新版本通过 `python3 -I qemu/guest.py` 调用。`--module` 在就绪前加载，`--late-module` 在就绪后、负载前加载；二者使用与目标内核匹配的未压缩 `.ko`，按依赖顺序传入。`--payload` 将额外文件放入客体的 `/kernel-test/input/`。`--rootfs` 指定单次启动的 Ubuntu 归档。

每次运行从归档生成内存启动镜像。客体报告就绪后运行负载；采集版在负载成功后冻结并输出 `trace.tsv`。在该次启动的输出目录先看 `guest.status` 中的执行阶段，再查 `console.log` 中的内核与负载输出。客体规模和时限可通过 `--cpus`、`--memory`、`--timeout` 调整。
