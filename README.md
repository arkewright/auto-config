# 按板和负载裁剪 Linux 内核

在 x86_64 主机上交叉编译，在 riscv64 板上运行真实负载。工具区分启动、负载和初始化命中，生成完整 `.config`，再比较基线和候选内核的运行包大小与启动时间。

## 从这里开始

[实板操作流程](docs/riscv64-board.md)按执行顺序列出主机和 Ubuntu 板端的命令：安装依赖、换核、采集、裁剪、回放、比较。主机从这几行开始：

```bash
cp examples/board.env board.env
${EDITOR:-vi} board.env
source ./board.env
./kcfg tools && ./kcfg config
```

`tools` 自动获取缺少的源码并编译工具，也可复用已有依赖。`config` 从原生 `allyesconfig` 建立基线。基线、采集版和候选版各自打成一个 `.tar.gz`，传到板上后由用户在本机安装并重启。

| 关心的结果 | 主机上的位置 |
|---|---|
| 原生配置与必要调整 | `$WORK/config/native.config`、`$WORK/config/changes.tsv` |
| 负载快照与裁剪理由 | `$WORK/trace.tsv`、`$WORK/plan/report.tsv` |
| 最终配置与可安装的内核包 | `$WORK/candidate/.config`、`$WORK/packages/candidate.tar.gz` |
| 包体积和启动时间差异 | `./kcfg compare` |

## 参考

- [脚本使用说明](docs/usage.md)：命令、变量、包内容、策略和指标。
- [架构](docs/architecture.md)：插桩、采集窗口和配置决策。
- [参考平台验收](docs/validation.md)：x86_64 QEMU 的四次启动结果及复现命令。
- [Clang 插件](plugin/README.md)、[配置处理器](processor/README.md)、[QEMU 验证器](qemu/README.md)：组件独立使用说明。
