# 函数入口插桩

这是树外 Clang 前端插件。每次编译只接受一个原始 C 翻译单元；编译包装器决定哪些文件参与本轮采集。同一文件内所有入选函数共用一个编号，编号通过本轮清单关联配置项和模块，不代表函数地址。

插件在 Clang 逐组交付顶层声明时，先于代码生成消费者处理当前定义。它沿用强制包含头文件中的探针声明，在原函数体的第一条语句前插入调用，不重写源码，也不替换函数符号。声明上的汇编名将探针绑定到本轮记录端；函数原有的链接属性、参数、返回值及浮点选项保留。

## 覆盖边界

- 只处理名称拼写位置和展开位置都在主文件中的普通 C 函数定义。头文件定义、显式内联函数，以及被编译器完全消除的代码不提供独立观测点。
- 排除显式段、`notrace`、`noinstr`、裸函数、特殊调用约定、中断入口、冷热路径标注，以及不允许插入副作用的函数。保留优化器内联带探针的普通函数的权利。
- 初始化段和退出段不插桩。仅凭语法无法识别从初始化回调间接调用的普通函数；初始化调用栈和负载窗口的隔离由采集端完成。
- 汇编、内核之外的工作及未选文件不在覆盖范围内。未命中不能证明功能无用；选择一个文件采样也不能证明与某配置项相关的所有行为都已采样。

## 编译与验证

从仓库根目录构建。`LLVM_DIR` 和 `Clang_DIR` 必须指向将要加载插件的同一发行包；项目默认精确匹配 23.1.4。宿主 C++ 编译器和 glibc 根目录可分别通过 `CMAKE_CXX_COMPILER` 和 `CMAKE_SYSROOT` 指定，所选环境还须与 Clang 的 C++ 运行库兼容。

```sh
cmake -S . -B build/plugin-only -G Ninja -DKCFG_BUILD_PROCESSOR=OFF \
  -DCMAKE_C_COMPILER="$SDK/bin/clang" -DCMAKE_CXX_COMPILER="$SDK/bin/clang++" \
  -DLLVM_DIR="$SDK/lib/cmake/llvm" -DClang_DIR="$SDK/lib/cmake/clang"
cmake --build build/plugin-only --target kcfg_frontend
ctest --test-dir build/plugin-only -R frontend-instrumentation --output-on-failure
```

常规内核编译使用仓库中的包装器。低层接口如下，收据与目标文件应使用全新路径：

```sh
"$SDK/bin/clang" -fplugin="$PWD/build/plugin-only/plugin/kcfg_frontend.so" \
  -fplugin-arg-kcfgtrace-id=1 -fplugin-arg-kcfgtrace-receipt="$PWD/example.count" \
  -include "$PWD/driver/probe.h" '-DKCFG_LINK_NAME="kcfg_hit_example"' \
  -c example.c -o example.o
```

成功收据仅含十进制插入点数量和换行。前端通过 Clang 的临时输出机制提交收据，诊断失败时不发布新收据。收据不能替代目标文件检查：编译包装器还需验证实际产物引用了本轮探针，零插入点不能作为裁剪依据。测试分别在关闭优化和启用优化时运行编译产物，并独立检查中间表示、别名、排除规则和失败路径。
