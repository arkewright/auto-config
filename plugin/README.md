# Clang 函数入口插件

插件按翻译单元插桩。编译包装器为每个入选的 C 文件分配一个编号，插件在该文件的普通函数入口插入探针调用。同一文件内的探针共用编号，板级清单据此关联配置项和模块。

插件在代码生成前修改函数体，沿用原函数的声明和链接属性。强制包含的头文件声明探针，声明上的汇编名将其绑定到本次采集构建的内建记录端。

## 插桩规则

| 项目 | 处理 |
|---|---|
| 主文件普通函数 | 函数名的拼写和展开位置均在主文件时插桩。 |
| 头文件定义、显式内联函数 | 跳过。优化器仍可内联已插桩的普通函数。 |
| 特殊函数 | 跳过显式段（含初始化、退出段）、`notrace`、`noinstr`、裸函数、中断入口、特殊调用约定、冷热路径标注和限制副作用的定义。 |
| 同步初始化链 | 普通辅助函数照常插桩，由[内建记录端](../docs/architecture.md#时间窗口)区分初始化和负载命中。 |

## 独立构建

从仓库根目录运行。使用与加载插件的 Clang 同一发行版的 LLVM／Clang 头文件和库；默认版本为 23.1.4。自选宿主编译器和 glibc 时使用 `CMAKE_CXX_COMPILER` 和完整 `CMAKE_SYSROOT`，并保持与 Clang 的 C++ ABI 兼容。

```sh
SDK="${SDK:-$PWD/.deps/llvm}"
export PATH="$SDK/bin:$PATH" LD_LIBRARY_PATH="$SDK/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cmake -S . -B build/plugin-only -G Ninja -DKCFG_BUILD_PROCESSOR=OFF \
  -DCMAKE_C_COMPILER="$SDK/bin/clang" -DCMAKE_CXX_COMPILER="$SDK/bin/clang++" \
  -DLLVM_DIR="$SDK/lib/cmake/llvm" -DClang_DIR="$SDK/lib/cmake/clang"
cmake --build build/plugin-only --target kcfg_frontend
ctest --test-dir build/plugin-only -R frontend-instrumentation --output-on-failure
```

## 编译接口

每次传入一个未预处理的 C 文件。参数 `id` 取 1 至 65,535，`receipt` 指定收据路径。手动验证时，准备自己的 `example.c`，并使用新的收据和对象路径。

```sh
"$SDK/bin/clang" -fplugin="$PWD/build/plugin-only/plugin/kcfg_frontend.so" \
  -fplugin-arg-kcfgtrace-id=1 -fplugin-arg-kcfgtrace-receipt="$PWD/example.count" \
  -include "$PWD/driver/probe.h" '-DKCFG_LINK_NAME="kcfg_hit_example"' \
  -c example.c -o example.o
```

成功收据是十进制插入点数加换行。编译失败时，Clang 丢弃新的临时收据。覆盖校验还要求生成的对象引用探针符号，以确认优化后至少保留了一个观测点。
