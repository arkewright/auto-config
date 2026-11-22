# 在 Ubuntu 客体中验证裁剪结果

此验证器把指定的 x86_64 内核和真实的 Ubuntu Base 用户空间一起启动。每次运行都会创建新的内存根文件系统；负载在客体内以 root 身份执行。它验证的是这台虚拟机能否启动并完成同一负载，不证明真实板卡的设备、固件、异常路径或服务启动顺序可用。

## 权限与依赖

宿主机只需普通账户的文件读写权限。不挂载镜像，不创建网桥，不共享主机目录，也不在宿主机加载模块。能读写 `/dev/kvm` 时默认使用硬件加速；设备不可访问时默认使用软件模拟，硬件加速初始化失败也可显式指定 `--accel tcg`。客体内挂载文件系统、插入模块和重启所需的权限由虚拟机内的 root 提供。

宿主机需要 Python 3.11 或更高版本、uv、QEMU、bsdtar、readelf 和面向 x86_64 静态链接的 BusyBox。首次获取镜像还需 curl；显式加载模块时还需要宿主机的 `modinfo`，用于事先读取模块的发行标识，与运行中的客体内核核对。BusyBox 只负责客体内的 `insmod` 和 `reboot`；运行负载的 shell 与常用工具来自 Ubuntu。可以用 `--busybox` 和 `--qemu` 指定普通用户目录中的程序。此处不编译用户程序；替换客体 glibc 应使用与负载匹配的 Ubuntu 根文件系统。

## 准备镜像与内核

在项目根目录运行：

```sh
qemu/guest.py fetch
```

默认获取 Ubuntu Base 24.04.4 的 amd64 归档，并打印缓存文件路径。归档下载到全新的临时目录，校验通过后才进入缓存；归档内容直接转换为内存启动镜像，不在宿主机展开。每次使用均重新校验 SHA-256。首次固定摘要时已核对 Ubuntu 镜像签名密钥 `843938DF228D22F7B3742BC0D94AA3F0EFE21092` 对发行清单的签名。

```text
https://cdimage.ubuntu.com/ubuntu-base/releases/24.04.4/release/
ubuntu-base-24.04.4-base-amd64.tar.gz
c1e67ef7b17a6300e136118bd1dc04725009cb376c1aad10abcf8cd453628d58
```

离线时传 `--rootfs /path/to/archive.tar.gz`。使用自备的 amd64 Ubuntu 归档时，同时传入可信的 `--rootfs-sha256`；用户空间必须含 `/bin/sh`、mount、cat、uname、sleep 和 sha256sum。可执行负载需能在该用户空间中启动。

把 `qemu/guest.config` 合入目标 x86_64 基线并用 Kconfig 重新求解。它是验证器的增量要求，不是独立完整的板卡配置。尤其要确认串口控制台、PCI 上的 virtio 控制台、gzip 初始内存盘、devtmpfs、procfs 和 sysfs 均内建，不能依靠尚未传入的模块启动。内核启用强制模块签名时，传入的模块应由内核信任的密钥签名。裁剪前保留未修改的基线映像；不要用虚拟机配置替换开发板的启动配置。

## 启动、采集、复验

以下示例中，`BASE`、`PROFILE`、`CANDIDATE` 分别指向已构建的三套内核输出目录；`COLLECTOR` 指向与采集内核匹配的外部驱动，`DEFERRED` 指向由候选内核构建的未压缩模块。每个输出目录必须尚不存在。

```sh
qemu/guest.py run --mode baseline \
  --kernel "$BASE/arch/x86/boot/bzImage" \
  --workload examples/workload.sh --output build/guest/baseline-01

qemu/guest.py run --mode profile \
  --kernel "$PROFILE/arch/x86/boot/bzImage" --module "$COLLECTOR" \
  --workload examples/workload.sh --output build/guest/profile-01

qemu/guest.py run --mode candidate \
  --kernel "$CANDIDATE/arch/x86/boot/bzImage" --late-module "$DEFERRED" \
  --workload examples/workload.sh --output build/guest/candidate-01
```

三种模式都先挂载客体文件系统，确认 Ubuntu 身份，然后按 `--module` 的给定顺序执行 `insmod`。依赖模块必须列在使用者前面。客体达到就绪点后，才按 `--late-module` 的给定顺序加载延迟模块并启动负载；载入失败或负载退出状态非零，整次验证失败。候选模式不需要采集驱动。将配置改成 `m` 本身不会按需加载，这是一次显式延迟加载验证，不是对生产系统自动加载策略的承诺。

采集模式在就绪点向 `/dev/kconfig-trace` 写入 `start`，在负载成功退出后写入 `stop`，随后读取冻结的快照。因此预加载不在采集窗口内，主动延迟加载则在窗口内。初始化段由插件排除，负载期的同步初始化链由驱动单独记账。每次启动只采集一个窗口；需要覆盖多个负载时分别启动，不复用上次结果。

`--workload` 可接受带解释器声明的脚本或客体可执行文件。上例读取客体内核提供的压缩配置并检查其内容；仅需验证虚拟机自身时，可以改用 `qemu/workload.sh` 做简单的文件读写。真实工作负载应该自行检查业务结果，错误时返回非零。可重复指定 `--payload 文件`，其内容复制到客体的 `/kernel-test/input/`，不映射宿主机路径。若模块必须在负载中途才加载，可把模块当作普通输入文件带入，由负载脚本在相应时刻运行 `/kernel-test/busybox insmod "$KERNEL_TEST_INPUT/文件名.ko"`。两种加载方式不要用于同一模块。

## 读取输出

`console.log` 保存客体控制台与负载输出；`guest.status` 记录实际发行版、运行内核、就绪状态、延迟加载数量和负载结果。采集数据走独立的 virtio 串口，绝不混入控制台。只有客体成功退出、快照非空、主客体摘要一致时，宿主机才发布 `trace.tsv`。失败目录中的 `trace.pending` 不能用于生成配置。

默认使用两个虚拟处理器和 1536 MiB 内存；可用 `--cpus`、`--memory` 和 `--timeout` 调整。默认启动及负载超时为 180 秒。失败后先看控制台与客体记录的失败阶段：无就绪记录先查内建启动依赖；预加载失败先查模块版本、依赖和签名；负载失败先核对客体中的程序及输入。重试时指定新输出目录，保留原始证据。
