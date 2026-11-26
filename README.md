# 按开发板和负载裁剪 Linux 内核

在 x86_64 主机上编译；在 Ubuntu 板上启动采集内核、运行负载，然后把快照传回主机生成候选 `.config`。每次内核构建都直接产出可部署的 `.tar.gz`。

```bash
cp .env.example .env
${EDITOR:-vi} .env
./kcfg --init-deps
./kcfg --build-tools
./kcfg --build-kernel profile
```

默认面向 x86_64，构建 `bzImage` 和模块。在 `.env` 把 `ARCH` 改为 `riscv` 即改用 RISC-V 的 `Image`、模块和设备树。[配置模板](.env.example)逐项说明了主机设置；入口自动读取 `.env`。可以直接填写板厂源码、已有 LLVM 安装和板子的完整 `.config`。取回快照后，`./kcfg --plan` 打开配置对照编辑器。按 `a` 分析，按 `A` 应用；强制意见和依赖约束在同一份配置上生效。

默认部署包是 `build/board/packages/kernel-profile.tar.gz`。在板上解压并运行包内的 `deploy.sh` 即可按版本安装新内核、生成 initramfs 并更新 GRUB 菜单。脚本可直接查看和修改。

- [实板教程](docs/board.md)：从准备依赖到运行负载、取回快照和回放候选内核。
- [脚本参考](docs/usage.md)：入口参数、配置示例、包内容及部署行为。
- [配置编辑器](processor/README.md)：锁定板级要求、检查依赖、保存草稿与应用变更。
- [采集与裁剪原理](docs/architecture.md)：启动、负载、初始化命中如何决定配置。
- [开发测试记录](docs/validation.md)：独立的 QEMU 回归测试和实测范围。
