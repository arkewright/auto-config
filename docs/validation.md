# 参考平台验收记录

参考平台为 x86_64 QEMU、Ubuntu Base 24.04.4、Linux 7.4-rc3 和 LLVM／Clang／LLD 23.1.4。负载检查 `/proc/config.gz` 可读、压缩内容完整且解压后含 `CONFIG_IKCONFIG_PROC=y`。

## 四次启动的结果

| 启动方式 | 负载结果 |
|---|---|
| 未插桩板级基线 | 通过。 |
| 采集内核与采集驱动 | 通过，完整快照传回宿主机。 |
| 候选内核，就绪后加载配置读取模块 | 通过。 |
| 同一候选，省略配置读取模块 | 启动到就绪，负载因接口缺失而失败。 |

| 配置项 | 启动命中 | 负载命中 | 结果 |
|---|---|---|---|
| `CONFIG_BINFMT_ELF` | 有 | 有 | 保留内建。 |
| `CONFIG_IKCONFIG` | 无 | 有 | 改为模块 `configs.ko`。 |
| `CONFIG_NLS_CODEPAGE_850` | 无 | 无 | 按板级策略关闭。 |

压缩内核镜像从 3,195,904 字节降至 3,179,520 字节，减少 16,384 字节（0.513%）。该口径只计镜像，不计独立模块。对照结果说明，参考负载需要配置读取功能，启动到就绪不需要它。

## 采集和拒绝路径

| 检查 | 结果 |
|---|---|
| 负载期间加载初始化测试模块 | 初始化回调睡眠后触发探针，仅初始化位置位。 |
| 设备状态 | 重复开始、非法命令和停止前读取均被拒绝；随后可正常结束并读取快照。 |
| 同构建多快照 | 配置读取与普通文件读写的快照可合并，并通过候选构建。 |
| Kconfig 连带变更 | 请求关闭父项导致 `olddefconfig` 关闭 `CONFIG_IKCONFIG_PROC`，候选构建拒绝发布。 |
| 带基本符号版本的模块 | 插桩的 `configs.ko` 就绪后加载，读取配置时记录到负载命中；不同发行后缀的模块在插入前被拒绝。 |
| 处理器交互与输入校验 | 通过保存、取消、身份校验、截断检测、覆盖检查和输出目录保护。 |

## 复现

```sh
just check
JOBS=8 just demo build/replay-01
tests/check-initializer.sh build/replay-01/profile build/replay-01/initializer-check
uv run --no-project python -I processor/tests/contract.py build/host/processor/kcfg-plan --tmux
```

在实验目录查阅 `result.json`、`plan/report.tsv`、`capture/trace.tsv` 和各次启动的 `guest.status`。最终配置位于 `candidate/.config`。

独立复现使用基本符号版本的模块：

```sh
SDK="${SDK:-$PWD/.deps/llvm}"
tools/kernel.py configure --llvm "$SDK" --base allnoconfig --fragment qemu/guest.config \
  --fragment examples/profile.config --fragment tests/basic-versions.config --out build/versions-board
tools/kernel.py configure --llvm "$SDK" --base allmodconfig --fragment build/versions-board/.config \
  --unpin CONFIG_IKCONFIG --out build/versions-baseline
tools/kernel.py profile --llvm "$SDK" --baseline build/versions-baseline/.config \
  --scope examples/qemu.scope.tsv --plugin build/host/plugin/kcfg_frontend.so --out build/versions-profile
qemu/guest.py run --mode profile --kernel build/versions-profile/kernel/arch/x86/boot/bzImage \
  --module build/versions-profile/driver/kcfg_collect.ko \
  --late-module build/versions-profile/kernel/kernel/configs.ko \
  --workload examples/workload.sh --output build/versions-guest
```

## 适用范围

以上结果使用精简的参考板级基线；示例中带约束的 `allyesconfig` 生成了相同的 `.config`。另一次实验从原生 `allyesconfig` 出发，调整后的宽基线已构建，客体启动尚未通过。Ubuntu 与 GRUB 开发板的实测步骤见 [RISC-V 64 实板验证](riscv64-board.md)。
