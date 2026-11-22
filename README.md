# 面向板卡负载的内核裁剪

从能启动的板级配置出发，用树外 Clang 前端插件记录选定内核组件在什么时候被使用，再生成可审阅的完整 `.config` 和延迟加载清单。不因一次未命中就删除驱动，也不把一次成功的模拟启动当作实板验收。

参考平台是 x86_64 QEMU，用户空间是 Ubuntu Base 24.04.4。示例负载读取 `/proc/config.gz`：裁剪后仍要读出正确内容，但提供该接口的模块不必内建。实际开发板和负载可替换，仓库不预设它们与参考平台等价。

## 权限

| 操作 | 所需权限 |
|---|---|
| 克隆源码，编译插件、内核和驱动，审阅配置 | 宿主机普通用户，所有产物写入用户目录。 |
| 启动 QEMU 和传回快照 | 不需要宿主机 sudo。不挂载磁盘镜像，不创建网桥；无权使用 `/dev/kvm` 时可用软件模拟。 |
| 在客体中挂载文件系统、加载模块、操作采集设备 | 仅客体内需要 root；验证器的初始进程已具备该权限。 |
| 安装系统软件包或写入实板启动分区 | 不属于自动流程。只有管理员决定做这些操作时才需要相应权限；不要授予宿主机免密 sudo。 |

## 从这里开始

本工作区已验证的完整候选配置在 `build/demo/candidate/.config`，对应决策在 `build/demo/plan/report.tsv`。两者只适用于随仓库提供的参考板和示例负载。

本工作区已准备好 LLVM／Clang／LLD 23.1.4、Linux 7.4-rc3、FTXUI 6.1.9 和 Ubuntu 镜像。新机器的工具与依赖说明见 `docs/usage.md`。从仓库根目录运行：

```sh
just sources
just check
JOBS=8 just demo build/my-first-run
```

最后一条命令从头构建并启动未插桩基线、采集内核和未插桩候选内核，再做一次不加载模块的反向对照。它使用**受板级配置约束的 `allyesconfig`**，不是让所有调试器和所有硬件驱动都参与启动。每次实验都要给一个新目录，失败也不会覆盖原始证据。

| 到哪里看 | 得到什么 |
|---|---|
| `build/my-first-run/profile/campaign/objects.tsv` | 实际编译的源文件、Kbuild 模块名和插桩收据。 |
| `build/my-first-run/capture/trace.tsv` | 冻结的启动、负载和初始化命中，独立于内核日志。 |
| `build/my-first-run/plan/report.tsv` | 每个配置项变更或保留的依据。 |
| `build/my-first-run/candidate/.config` | 经目标 Kconfig 求解且已成功编译的完整候选配置。 |
| `build/my-first-run/candidate/deferred-paths.txt` | 候选构建中实际存在的延迟模块及硬依赖，按加载顺序排列。 |
| `build/my-first-run/result.json` | 同一负载的基线、候选与反向对照结果。 |

交互审阅可单独运行，不会修改已有方案：

```sh
build/host/processor/kcfg-plan build/my-first-run/profile/campaign \
  build/my-first-run/capture/trace.tsv build/my-reviewed-plan --review
```

## 交付内容

| 位置 | 内容 |
|---|---|
| `plugin/` | 实际插入函数入口调用的 C++26 Clang 前端插件和可执行测试。 |
| `driver/` | 内建记录端和树外可加载控制驱动。两者源码均不放入 Linux 源码树。 |
| `processor/` | C++26 处理程序、FTXUI 审阅界面及同构建多负载合并。 |
| `tools/`、`qemu/` | 来源固定、配置生成、Kbuild 集成与免宿主机特权的 Ubuntu 回放。 |
| `docs/architecture.md` | 组件契约、初始化隔离、并发边界和裁剪依据。 |
| `docs/usage.md` | 从准备 SDK 到替换板卡、部署延迟策略与故障排查。 |
| `docs/validation.md` | 实际验证结果、对照实验与未验证边界。 |
