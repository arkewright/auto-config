# 配置处理器

主机处理器将同一采集内核的快照按配置项聚合，输出配置请求、变更理由和延迟模块清单。构建使用 `./kcfg --build-tools`；宿主编译器和 FTXUI 来源在 `.env` 中选择。

## 批处理与终端审阅

```bash
./kcfg --plan                 # 按 .env 中的快照和策略生成方案
./kcfg --build-kernel candidate
```

要在保存前查看建议，用 `./kcfg --plan --review` 代替第一条。方向键选择建议，空格撤回或恢复，`s` 保存；`q`、Esc 或 Ctrl-C 取消且不产生方案。每次使用新的输出目录。

## 输入与决策

| 输入 | 用途 |
|---|---|
| `profile/campaign/baseline.config` | 构建采集内核时实际使用的完整配置。 |
| `profile/campaign/scope.tsv` | 连续编号、配置名、模块名、策略和 C 源文件路径，每行五列，以制表符分隔。 |
| `profile/campaign/campaign.id` | 本次采集构建的随机标识。 |
| `profile/campaign/coverage.tsv` | 各编号对应的有效插入点数量，零或缺失表示覆盖不足。 |
| `trace.tsv` | 首行是 `KCFG-TRACE`、版本 `1` 和构建标识；各数据行是编号、启动位、负载位、初始化位；末行是 `END` 和总行数。 |

`.env` 的 `TRACE` 选择主快照，`EXTRA_TRACES` 可指定更多同构建的快照。先对同一编号的各阶段命中取并集，再汇总同一配置项的所有文件。启动命中优先保留；有效覆盖、模块归属和板级授权共同决定其他选项的处理。详细规则见[采集与裁剪原理](../docs/architecture.md#配置决策)。

## 得到什么

`$WORK/plan/report.tsv` 解释观测结果和建议，`changes.tsv` 给出原值与请求值，`modules-load.late` 每行一个待延迟加载的模块。`requested.config` 是完整的配置请求，候选构建将它交给目标源码的 Kconfig。最终配置位于 `$WORK/candidate/.config`，候选包的 `boot/config-<release>` 也保存该配置。
