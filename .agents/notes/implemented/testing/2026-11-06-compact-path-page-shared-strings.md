# Agent Note: 压缩路径页与候选行共用文本字段

Status: implemented

## Problem

[全拼冷路径缓存页压缩](2026-10-23-quanpin-path-page-compaction.md)把普通词典页存成 `DictRow`（规范键、词面都是普通 `String`），取页时经 `WordItem::new` 重建候选行。它和[候选行共享字符串](../architecture/2026-10-23-shared-candidate-strings.md)是同一天的改动，算账时 `WordItem` 的文本字段还是 `String`：那时完整页复制每行 4 次申请，压缩页 3 次，压缩更省。字段换成 `SharedString` 之后，完整页复制每行 0 次申请，压缩页却变成每行 6 次：读音、词面、规范键各复制一份 `String`，`WordItem::new` 再给每份包一个 `Arc`；存页时还要把已经是 `SharedString` 的规范键和词面逐字节复制回 `String`。

本机（M4 Max）对 `ninekey_latency` 采样，`query_series_with_typos → PathPage::append_to → WordItem::new` 这一条约占主线程 13%，分配器自耗时约占 34%。[冷页入缓存的整页复制](2026-10-29-page-copy-shared-strings.md)修过同一类问题的另一条路径，压缩页当时没有覆盖到。

## Decision

`CompactPage` 的行改存 `CompactRow { canonical_pinyin, word, weight }`，两个文本字段是 `SharedString`；整页的匹配读音存一份 `SharedString`。存页（`PathPage::from_dictionary`）和取页（`copy_rows`、`append_to`、`cloned_rows`）都只克隆 `SharedString`，不复制字符串。取出的行与缓存页共用存储，任何一边原地改写时由 `SharedString` 写时复制，两边互不影响。

原来读音等于缓存键时借查询键还原、不等时每页存一份 `String`；共用存储后两种情况都只是一次引用计数，`cache_key` 参数随之从四个方法上去掉。单行页仍存完整页：压缩后省不下行结构，反而多一个 `Box`。压缩条件里为「省下的读音申请多于新增容器申请」设的行数门槛（匹配缓存键至少 2 行、否则至少 3 行）不再有意义，统一为至少 2 行。元数据校验（`ordinary_row`）、空页变体、展开页存完整页和 FIFO 容量都不变。

## Alternatives considered

- **取消压缩、全部存完整页**：`WordItem` 克隆已经只剩引用计数，完整页的取页成本与新压缩页相同，代码也更少。但完整页每行多存约 56 字节的行结构，每行还各持有一份读音的 `Arc`；128 槽缓存里的大页常驻内存会变大，压缩页省内存的那一半理由仍然成立。
- **`DictRow` 本身改用 `SharedString`**：一次改到底，`segment_row_cache` 和词网格也能受益。这里先只改路径页。

## Consequences

收益（本机 M4 Max，默认 release 配置，`ninekey_latency` 默认 20 轮，与改前二进制 ABBA 交替各 4 次，1 分钟负载 10–14）：

| | 改前 | 改后 | 变化 |
|---|---|---|---|
| 全部位数 p50 | 2.245 ms | 1.613 ms | −28.2% |
| 全部位数 p95 | 5.425 ms | 4.218 ms | −22.3% |
| instructions retired（整个进程，含加载词库） | 1338 亿 | 988.6 亿 | −26.1% |
| cycles | 311.9 亿 | 254.1 亿 | −18.5% |
| 最大常驻内存 | 99.2 MB | 87.0 MB | −12.3% |
| peak memory footprint | 53.6 MB | 40.3 MB | −25% |

全拼经 C ABI（`keystroke_latency quanpin`，挂随锁资源的句子模型，同样 ABBA 各 4 次）：`character` p50 约 0.20 ms、p95 约 6.6 ms，两臂在噪声范围内；指令数 1348 亿 → 1329 亿（−1.4%），最大常驻内存约 136 MB → 126 MB（−7.6%）。全拼这一键约一半时间在句子模型推理里，压缩页流量比九键小得多，所以延迟看不出差别。

墙钟受负载影响，以指令数为准；压缩页本身每行多两个 `Arc` 头（约 80 字节），常驻内存却下降了，原因没有单独查证。改后采样里 `PathPage::append_to` 约占 1%，分配器自耗时降到约 18%，下一步的大头是整句词网格解码（约 45%，每次解码重建图的分配占剩余分配的三成）和 SQLite 读行（约 12%）。

输出不变：`convert_eval` 九键句子、九键词（3000 条）、十四键句子、全拼句子、全拼词（3000 条）、语气词六组，挂着随锁资源的句子模型重排，`--dump` 全部候选与 `--report` 都与改前逐字节相同。

测试：`compact_page_shares_text_storage_with_rows` 钉住存页只有 `Box` 和行容器 2 次申请、三个取页出口都只申请输出容器 1 次（与行数无关）、输出行与源行共用文本存储、整页共用一份读音、改写副本不影响缓存页。两条会话分配预算按推导收紧：`ni` 后输入 `h` 存 5 行冷页少复制 5 × 2 份（98 → 88），光标处输入两次命中 5 行压缩页少 2 × 5 × 6 次申请（164 → 104）。engine 单测 3001 项、golden 31 项及其余集成测试通过，input-runtime 457 项通过，engine 的 fmt 与 Clippy（`--all-targets -D warnings`）通过。这次改动和同一天的词库行、iOS 候选栏改动合在一起跑过一次 `verify-local.sh --quick`，退出码 1：失败的只有 `android haptics pending`、`android update job cancellation`、`android host java` 三个阶段，本机没有 `javac` 和 Android API 35，这几处改动也不含 Android 文件，但没有在干净的 develop 上对照过；另有 24 个阶段被跳过，包括 macOS 原生编译。设备验收没有跑。

代价：取出的行与缓存页共用存储，调用方原地改写会触发一次写时复制；以前这份复制在取页时就已经付过，所以不会更贵。
