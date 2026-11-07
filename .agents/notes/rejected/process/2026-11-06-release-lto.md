# Agent Note: 产品 release 构建开 LTO

Status: rejected — 去掉行复制之后，fat LTO 加单 CGU 在九键上只省约 2%、全拼 p95 不变，换来约 6.5 倍的编译时间，thin 没有可测收益

## Problem

工作区的 `[profile.release]` 没有设置，即默认的 `lto = false`、16 个 codegen unit。各平台发布脚本只覆盖 `CARGO_PROFILE_RELEASE_DEBUG` 和 `STRIP`，没有一处开 LTO；只有脚本插件包（`plugins/script/.cargo/config.toml`）开了。按键热路径横跨 `msime-engine`、`msime-input-runtime`、`msime-host-api` 和 `chinese-ime-lm`，跨 crate 内联是 LTO 的收益来源。

## Proposal

在输入法库的平台构建脚本里（比如 `platforms/ios/build-native.sh`、`platforms/macos/package-release.sh`）加 `CARGO_PROFILE_RELEASE_LTO`，只让输入法库付这份编译时间，Tauri 桌面壳和工具不受影响。

## 测量

本机 M4 Max，锁定工具链 1.97.1，环境变量覆盖 release 配置，在[词库行与图边共用文本字段](../../implemented/testing/2026-11-06-dict-row-shared-strings.md)之后的代码上交替运行，每种配置 4 次，1 分钟负载 7–14。

`ninekey_latency`（默认 20 轮）：

| 配置 | instructions retired | cycles | p50 | p95 |
|---|---|---|---|---|
| 默认 | 89.28 G | 23.00 G | 1.468 ms | 3.463 ms |
| thin | 88.83 G（−0.5%） | 22.96 G（−0.2%） | 1.473 ms | 3.463 ms |
| fat + 单 CGU | 88.40 G（−1.0%） | 22.61 G（−1.7%） | 1.440 ms（−1.9%） | 3.403 ms（−1.7%） |

`keystroke_latency quanpin`（经 C ABI，挂句子模型），默认对 fat + 单 CGU：指令数 131.8 G → 126.9 G（−3.7%），cycles 27.79 G → 27.50 G（−1.0%），`character` 均值 0.79 → 0.77 ms，p50 和 p95 不变。那一键约一半时间在 `chinese-ime-lm` 的点积里。

编译时间（增量编到 `keystroke_latency`）：默认 56 s、thin 114 s、fat 366 s。fat 的二进制小约两成（host-api 示例 24.7 → 18.7 MB）。

测量要避开并行负载：同一份比较在另有 iOS 模拟器测试和编译同时运行、负载 12–26 时，cycles 差别被噪声盖住；另一批进程占满核时又量出过 −4～5% 的 cycles。这两个数都不可信，上表是机器上没有那些负载之后量的。

## Alternatives considered

- **只给 `msime-engine` 设 `codegen-units = 1`、不开 LTO**：Cargo 允许按包设，不拖慢其他 crate；它省不掉跨 crate 调用，收益不会超过 fat，没有单独测。
- **整个 `[profile.release]` 开 thin**：一处改动覆盖所有平台，但九键和全拼都没有可测收益，Tauri 壳、MCP server 和评测示例的编译时间却翻倍。

## Acceptance criteria

热路径结构变了之后可以重测。同样交替运行并避开并行负载，cycles 或延迟 p95 收益不到 3% 时不开。

## Risks

不开 LTO，静态库和动态库大约大两成；移动端的包对体积敏感，但体积的大头是词库和模型资源。这次只在 macOS 主机上量过，移动 CPU 上的比例可能不同，没有在真机上量。
