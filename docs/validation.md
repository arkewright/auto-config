# 参考板验收记录

验证日期：2026 年 11 月 22 日。使用 LLVM／Clang／LLD 23.1.4，Linux 源码固定在 `v7.4-rc3`，宿主工具按 C++26 编译。QEMU 启动真实 Ubuntu Base 24.04.4 的 amd64 用户空间。整条流程不使用宿主机 sudo，也没有把测试驱动装入宿主机。

## 同一负载的四次启动

测试对象不是启动日志，而是应用可观察的接口：负载要求 `/proc/config.gz` 可读、压缩数据完整，并且解压内容含有已启用的配置读取接口。

| 镜像和操作 | 实测结果 |
|---|---|
| 未插桩板级基线 | 成功进入 Ubuntu，负载通过。 |
| 对应的采集版和外部驱动 | 同一负载通过，冻结快照成功传回宿主机。 |
| 关闭未用代码页，在就绪后加载配置读取模块 | 未插桩候选成功启动，模块加载后负载通过。 |
| 同一候选不加载该模块 | 同样到达就绪点，但负载失败。不是虚拟机没有启动，也不是目标功能仍意外内建。 |

本次清单中的实际决策：

| 配置 | 启动命中 | 负载命中 | 结果 |
|---|---|---|---|
| `CONFIG_BINFMT_ELF` | 是 | 是 | `y` 保留；可执行文件加载是启动契约的一部分。 |
| `CONFIG_IKCONFIG` | 否 | 是 | `y` 改为 `m`；候选内核产出并实际加载 `configs.ko`。 |
| `CONFIG_NLS_CODEPAGE_850` | 否 | 否 | `y` 改为 `n`；该参考板和负载的清单明确允许移除。 |

未读取配置文件的独立文件读写负载没有命中配置读取组件。它用于交叉核对观测能力，不用于从业务轨迹中减去后台活动。两份同构建快照的合并也已通过候选构建。`result.json` 记录当次未插桩基线和候选的压缩镜像字节数；它不包含单独分发的模块，不表示系统总内存、启动时间或真实设备吞吐量。

## 初始化与拒绝路径

- 在负载窗口内加载专用测试模块。其初始化回调调用普通辅助函数，睡眠后才触发测试探针。结果只有初始化位置位，启动和负载位均为零。该实验使用独立快照，不作为删除生产功能的证据。
- 开始后再次开始、输入非法命令、停止前试图读取，均被实际驱动拒绝。正常停止后的快照仍完整。
- 保留 `CONFIG_IKCONFIG_PROC=y` 而请求关闭其父选项时，真实 `olddefconfig` 会让子项消失；完整配置比较拒绝了这次未授权的 `y` 到 `n` 变化。
- 主机验证覆盖身份不匹配、截断快照、重复编号、缺失收据、跨负载合并和已有输出目录保护。实际终端验证过撤回后保存及取消不写盘。
- 另一次采集将配置读取组件本身编译为带探针的 `.ko`，只启用基本符号版本表。客体在就绪后加载它，实际负载读到了配置，快照中该组件只有负载命中。将另一发行后缀的模块交给候选客体时，验证器在模块插入之前就拒绝了它。

## 重现

```sh
just check
JOBS=8 just demo build/replay-01
tests/check-initializer.sh build/replay-01/profile build/replay-01/initializer-check
uv run --no-project python -I processor/tests/contract.py build/host/processor/kcfg-plan --tmux
```

主实验的证据在 `build/replay-01/plan/report.tsv`、各次启动的 `guest.status` 和 `capture/trace.tsv`。`candidate/.config` 位于该实验目录下。仓库只跟踪程序、策略示例和复现步骤，不把下载的工具链、内核或虚拟机镜像提交到 Git。

单独复现带符号版本的模块插桩：

```sh
SDK="$PWD/.deps/llvm"
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

## 边界

实际启动的是受参考板约束的 `allyesconfig` 基线；使用少量限制的更宽基线已验证能求解，未将其宣称为已完成启动验收。没有给出真实开发板型号、硬件清单或业务负载，因此未验证实板、具体固件、产品 glibc、冷门设备、故障恢复或生产加载时序。移植后仍需按 `docs/usage.md` 的回放和回滚流程验收。
