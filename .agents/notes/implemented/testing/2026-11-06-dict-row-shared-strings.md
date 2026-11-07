# Agent Note: 词库行与图边共用文本字段

Status: implemented

## Problem

[压缩路径页与候选行共用文本字段](2026-11-06-compact-path-page-shared-strings.md)之后，九键采样里整句解码约占 45%，`build_graph_borrowed` 自身有一半多时间在 `String::clone`：图边的词面和读音逐条从行缓存复制，同一跨度在后面的列重复出现时又从前面的边复制一遍。九键每键要解十几到二十几张图。`segment_row_cache` 取行时同样整页深拷贝，`matched_items` 还给同一页的每行各建一份相同的读音。

[iOS 连续输入按键路径的主线程预算](../bug-fix/2026-10-18-ios-keystroke-render-budget.md)记录的真机数据里，词典查询均值 12.6 ms，其中词网格 6.1 ms，比同一轮的布局均值 4.6 ms 高得多；这些是共享字符串之前的同步阶段数字，只说明真机上引擎是重头，不代表现状。

## Decision

`DictRow`、`lattice::decode::Edge` 和 `TypoEdge` 的文本字段改成 `SharedString`，行和边的克隆只加引用计数。对外返回的 `SentencePath::words` 和 `query_exact_keys_per_key` 的键仍是 `String`，在那里才复制。

`WordItem::new` 的文本参数改成 `impl Into<SharedString>`，已有调用不变，传入已共享的字段时不再深拷贝。`matched_items` 整页共用一份读音，第一行时才建，空页不申请；九键探测时空页很常见。

## Alternatives considered

- **只给词网格单独一个行类型，`DictRow` 不动**：改动面小，公共行类型不受影响。但行缓存到图边之间还要转换一次，省不下 `segment_row_cache` 的整页复制；`DictRow` 的生产构造点只有 5 处，直接改它的代价不大。
- **图边借用行缓存（`Graph<'rows>`）**：连引用计数也省了。但 `BeamPrefixCache` 要跨按键留着整句图，寿命比行缓存的借用长，要改缓存所有权，风险大得多。

## Consequences

收益（本机 M4 Max，锁定工具链 1.97.1，`ninekey_latency` 默认 20 轮，与只改了路径页的版本 ABBA 各 4 次）：指令数 98.87 G → 89.34 G（−9.6%），cycles 24.65 G → 23.06 G（−6.5%），全部位数 p50 1.575 → 1.488 ms（−5.6%），p95 3.963 → 3.493 ms（−11.9%），最大常驻内存 83.8 → 82.9 MB。测量时机器上有别的工作负载（1 分钟负载约 4），以指令数为准。

输出不变：`convert_eval` 九键句子、九键词（3000 条）、十四键句子、全拼句子、全拼词（3000 条）、语气词六组，挂句子模型重排，`--dump` 全部候选与 `--report` 都与改动前逐字节相同。

代价：从 SQLite 新读的每行多两个 `Arc`（两次申请、80 字节），只读一次就丢弃的行变贵；行缓存里每行多持有这 80 字节。没有单独量缓存占满时的内存上界。

测试：engine 单测 3001 项、golden 31 项，input-runtime 457 项，engine-capi 与 engine-wasm 通过，fmt 与 Clippy（`--all-targets -D warnings`）通过。冻结的旧实现副本里，键的复制改写成 `String::clone`，让它们继续按当时的成本计数，这样改回了 16 条比较用例而没有动断言。`graph_build_transfers_unique_rows_and_preserves_repeated_spans` 比较「省下多少次克隆」的那本账已经不存在，放宽成不多于旧实现、图内容仍逐字段比较；`first_graph_occurrence_owns_the_original_row_storage` 的重复跨度从「各有存储」翻转成「共用存储」。`typing_at_the_end_does_not_build_the_preedit_twice_for_caret_detection` 的 70 是实测值，没有逐项推导。`verify-local.sh --quick` 的结果见[压缩路径页笔记](2026-11-06-compact-path-page-shared-strings.md)：只有三个 Android 阶段因本机环境失败，macOS 原生编译被跳过。设备验收没有跑。
