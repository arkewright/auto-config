# 面向板卡负载的 Linux 内核裁剪

用目标板和真实负载的执行记录生成完整 `.config` 与延迟加载清单。工具区分启动、负载和初始化活动，按板级策略提出裁剪建议，再用新内核回放同一负载。

## 快速上手

参考环境：x86_64 QEMU、Ubuntu Base 24.04.4、Linux 7.4-rc3、LLVM／Clang／LLD 23.1.4、FTXUI 6.1.9。可通过 `LINUX` 和 `SDK` 选择板厂源码和其他兼容工具链。构建与模拟使用宿主机普通账户；客体初始进程负责挂载和加载模块。见[工具链准备](docs/usage.md#准备工具链)。

```sh
just sources
just check
JOBS=8 just demo build/my-first-run
```

`just demo` 使用精简的板级基线，依次验证基线、采集版和候选内核。示例负载在候选内核就绪后读取 `/proc/config.gz`：先加载配置读取模块时通过，省略模块时失败。每次运行使用新输出目录。

| 产物（相对于输出目录） | 用途 |
|---|---|
| `capture/trace.tsv` | 查看启动、负载和初始化命中。 |
| `plan/report.tsv` | 查看配置项保留或变更的依据。 |
| `candidate/.config` | 使用已通过依赖检查和构建的完整配置。 |
| `candidate/deferred-paths.txt` | 按顺序加载候选模块和硬依赖。 |
| `result.json` | 核对回放结果与压缩镜像大小。 |

## 文档

- [使用指南](docs/usage.md)：搭建工具链，选择基线，采集、审阅与部署。
- [架构](docs/architecture.md)：插桩、运行窗口、裁剪规则与构建校验。
- [验收记录](docs/validation.md)：裁剪结果、反向对照与复现命令。
- [Clang 插件](plugin/README.md)、[配置处理器](processor/README.md)、[QEMU 验证器](qemu/README.md)：组件接口与独立运行方式。
