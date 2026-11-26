# 依赖图文件

`./kcfg --plan` 默认从目标架构的 Kconfig 和采集基线导出依赖图；已有导出文件可在 `.env` 的 `DEPENDENCY_GRAPH` 中指定。单独导出时，在仓库根目录运行：

```bash
uv run --script scripts/export_graph.py \
  --linux .deps/linux --arch x86_64 \
  --config build/board/profile/campaign/baseline.config \
  --kbuild-env build/board/profile/kernel/include/config/auto.conf.cmd \
  --output dependencies.tsv
```

导出器使用外部解析器读取源码，在临时目录执行该源码定义的编译器探测。`--kbuild-env` 从 Kbuild 的记录读取目标架构、编译器路径和探测参数；省略时使用当前环境，交叉编译应提供与采集构建一致的工具链和目标参数。编辑器只读取导出文件。

## 格式

文件为 UTF-8 文本，每行以换行符结束。首行两个字段是 `KCFG-GRAPH` 和 `1`。下表每个字段之间使用一个制表符。声明与规则的顺序不限。

| 记录 | 后续字段 | 含义 |
|---|---|---|
| `symbol` | 名称、`bool` 或 `tristate`、可选的 `hidden` | 此架构定义的开关。`hidden` 表示在采集基线中没有可见提示，自动分析保留原值。 |
| `absent` | 名称 | 反向依赖指向了本架构未定义的目标，其值为 `n`。 |
| `modules` | 名称 | 控制模块支持的布尔开关。它为 `y` 时才允许模块值。 |
| `depends` | 选项、表达式 | 表达式给出该选项的上限，包含外层菜单和条件继承的依赖。 |
| `select` | 源选项、目标选项、条件表达式 | 源选项与条件的较小值给出目标的下限。目标自身的依赖仍单独检查。 |
| `imply` | 源选项、目标选项、条件表达式、目标可见条件、目标直接依赖 | 目标可见时接受用户值；隐藏时按源和条件提高下限，但不超过目标的直接依赖。目标始终可见时省略最后两个字段。 |
| `choice` | 类型、`required` 或 `optional`、可用条件、用空格隔开的成员名 | 内建选择只能启用一个成员；必选布尔组至少有一个可用成员时必须选中其一。 |
| `scalar` | 标量名称、表达式 | 采集配置中已赋值的数值或字符串需要保持其直接依赖。 |

表达式使用空格分隔的后缀记法：`CONFIG_B CONFIG_C ||` 表示 `B || C`，`CONFIG_B ! CONFIG_C &&` 表示 `!B && C`。操作数可以是已声明的选项或 `n`、`m`、`y`。运算符包括 `!`、`&&`、`||` 以及 `=`、`!=`、`<`、`<=`、`>`、`>=`。`n < m < y`，作用在布尔选项上的 `m` 依赖界限提升为 `y`。

例如，下图允许关闭 `CONFIG_B` 和 `CONFIG_C` 中的任意一个；只要 `CONFIG_USED=y`，就必须保留其中一个：

```text
KCFG-GRAPH	1
symbol	CONFIG_USED	bool
symbol	CONFIG_B	bool
symbol	CONFIG_C	bool
depends	CONFIG_USED	CONFIG_B CONFIG_C ||
```

重新导出旧图可补上基线中的隐藏标记。导出器用采集基线的标量值求字符串和数值比较；开关及其组合保留到编辑时求值。图检查三态依赖、选择和互斥组；隐藏选项的默认值、标量范围及旧选项迁移由候选构建的原生 `olddefconfig` 处理。实际配置与已应用的请求不一致时，候选构建会指出具体选项。
